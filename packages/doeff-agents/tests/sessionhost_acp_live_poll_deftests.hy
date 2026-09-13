;;; 実況の push の周期の焦点の検(段 8 lane 4aa・agora-redesign #63)。
;;;
;;; headless の器の実況は events file の行の増分で、file の追記は合図を持たない —— agentd が offset から
;;; 読んで中継へ押す**拍の周期がそのまま push の間隔**になる。購読者が居る間は events の周期(≤ 50 ms —
;;; AgentdSettings.events_poll_seconds)、居なければ transcript の周期(記録の追記だけ)。tui の器(frames)の
;;; capture の周期(frame_interval_seconds・2〜5 Hz)は変えない。判断は judgment.wait-seconds-for の 1 点。
;;; HTTP も subprocess も無い(fake の handler で agentd を一周)。

(require doeff-hy.macros [deftest])

(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  InFlightJob
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [wait-seconds-for])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])
(import dataclasses [replace])


(setv NODE "mac-1")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record) "\n"))


(defn #^ AcpRow row-of [#^ str namespace #^ str kind #^ str resource-id #^ dict spec #^ (| dict None) status]
  (AcpRow :namespace namespace :key f"{namespace}:{kind}:{resource-id}" :kind kind :resource-id resource-id
          :version "v1" :generation 1 :created-at-ms 500 :labels {} :payload {} :spec spec :status status))


(defn #^ AcpRow bound-job [#^ str job-id #^ list inputs]
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id
          {"subject" CONVERSATION "inputs" inputs
           "charter" {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                      "agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
          {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"} "conditions" []}))


(defclass World []
  "backend = headless の器(events file が実況の正本)で agentd を一周させる最小の世界。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes"
                                        :backend-kind "headless" :stream-capability "events"))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND NODE
                               {"name" NODE "labels" {} "capacity" 1 "streamCapability" "events"}
                               {"state" "joined"}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND "m-1" {"id" "m-1" "body" "first"} {"state" "inbox"}))
    (.put-row self.acp (bound-job "j-1" ["m-1"]))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-secret"}))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.state (initial-state)))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ str sid [self]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1") status))
    (assert (isinstance status dict))
    (setv handle (get status "sessionHandle"))
    (assert (isinstance handle dict))
    (setv session-id (get handle "sessionId"))
    (assert (isinstance session-id str))
    session-id)

  (defn #^ float last-wait [self]
    (get self.acp.waits -1)))


(deftest test-events-are-polled-every-50ms-while-someone-watches
  ;; 起動の拍: job が無いので idle の上限。受けの後: 購読者 0 → 記録の周期(1 s)。購読者が現れて frame を押した拍の後:
  ;; events の周期(50 ms)。購読者が消えた拍の後: 記録の周期へ戻る。
  (setv world (World))
  (.tick world 0)
  (assert (= (.last-wait world) world.settings.idle-wait-seconds) world.acp.waits)
  (setv sid (.sid world))
  (.tick world 1000)
  (assert (= (.last-wait world) world.settings.transcript-poll-seconds) world.acp.waits)
  ;; 購読者が現れる(画面が attach した)— 次の push(本文の delta の frame)が 1 を返し、その次の拍から events の周期。
  ;; ⚠ headless の capturing は push の答え(購読者の数)でしか動かない — frame にならない行(init だけ)では動かない。
  (setv (get world.acp.subscribers sid) 1)
  (setv part-1 (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
                  (stream-line {"type" "stream_event"
                                "event" {"type" "content_block_delta" "index" 0
                                         "delta" {"type" "text_delta" "text" "hel"}}})))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") part-1)
  (.tick world 1000)
  (assert (. (get world.state.jobs 0) capturing))
  (.tick world 50)
  (assert (= (.last-wait world) world.settings.events-poll-seconds) world.acp.waits)
  (assert (<= world.settings.events-poll-seconds 0.05) "events の周期は 50 ms の中(出来事ごとの push に最も近い有界の拍)")
  ;; 購読者が消える — 次の push が 0 を返し、その次の拍から記録の周期。
  (setv (get world.acp.subscribers sid) 0)
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
        (+ part-1
           (stream-line {"type" "stream_event"
                         "event" {"type" "content_block_delta" "index" 0
                                  "delta" {"type" "text_delta" "text" "lo"}}})))
  (.tick world 50)
  (assert (not (. (get world.state.jobs 0) capturing)))
  (.tick world 50)
  (assert (= (.last-wait world) world.settings.transcript-poll-seconds) world.acp.waits))


(deftest test-frames-keep-the-capture-interval-and-events-take-the-poll
  ;; 判断の 1 点: capturing の job が在る時、tui(frames)は capture の間隔(2〜5 Hz)のまま・headless(events)は events の
  ;; 周期。capturing の job が無ければ器の種類に依らず記録の周期 / idle。
  (setv job (InFlightJob :job-key "k" :job-namespace AGENT-JOB-NAMESPACE :job-id "j" :subject CONVERSATION :session-id "s"
                         :agent-type "claude" :node NODE :profile "p" :model "m" :started-ms 0 :turn-floor-ms 0
                         :start-offset 0 :transcript-offset 0 :delta-seq 0 :lease-id None :lease-kind None
                         :lease-account None :lease-hold-ms None :capturing True :stream-gone False :last-frame-ms 0
                         :last-probe-ms 0 :pending-conditions #()))
  (setv state (replace (initial-state) :jobs #(job)))
  (setv frames (AgentdSettings :node-name NODE :backend-kind "tmux" :stream-capability "frames"))
  (setv events (AgentdSettings :node-name NODE :backend-kind "headless" :stream-capability "events"))
  (assert (= (run (wait-seconds-for state frames)) frames.frame-interval-seconds))
  (assert (= (run (wait-seconds-for state events)) events.events-poll-seconds))
  (assert (< events.events-poll-seconds frames.frame-interval-seconds))
  (setv idle-job (replace job :capturing False))
  (setv resting (replace state :jobs #(idle-job)))
  (assert (= (run (wait-seconds-for resting events)) events.transcript-poll-seconds))
  (assert (= (run (wait-seconds-for resting frames)) frames.transcript-poll-seconds)))
