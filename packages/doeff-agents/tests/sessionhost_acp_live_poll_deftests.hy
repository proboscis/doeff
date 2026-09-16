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
  LIST-MODE-NONE
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  TURN-RECORD-KIND
  WatchAdvance])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [list-mode-for wait-seconds-for])
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
    (get self.acp.waits -1))

  (defn #^ dict record-status [self]
    (setv status (. (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1") status))
    (assert (isinstance status dict))
    status)

  (defn #^ str job-phase [self]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1") status))
    (assert (isinstance status dict))
    (setv phase (get status "phase"))
    (assert (isinstance phase str))
    phase)

  (defn #^ list record-entries [self]
    (setv entries (.get (.record-status self) "entries" []))
    (assert (isinstance entries list))
    (list entries))

  (defn #^ list record-writes [self]
    (lfor [key status] self.acp.writes :if (in f":{TURN-RECORD-KIND}:" key) status)))


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


(deftest test-record-appends-keep-the-transcript-period-while-pushes-follow-the-poll
  ;; 記録(turn-record)への追記は transcript の周期(1 s)のまま — 50 ms の拍ごとに書くと走っている手番 1 つで毎秒 10〜20 の
  ;; event が ACP の journal に並び、画面の糊の watch の拍が飽和する(実弾 2026-09-13 18:3x)。push は拍ごと・書かない拍の
  ;; 出来事は持ち越して次の追記に乗る(落とさない)。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (.tick world 1000)
  (setv (get world.acp.subscribers sid) 1)
  ;; assistant の message の行 = 実況の frame(text)と記録の出来事(entry)の両方になる行。
  (defn #^ str delta-line [#^ str text]
    (stream-line {"type" "assistant"
                  "message" {"id" (+ "msg-" text) "role" "assistant" "model" "claude-opus-5"
                             "content" [{"type" "text" "text" text}]
                             "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0
                                      "cache_read_input_tokens" 0}}}))
  (setv lines (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
  (.tick world 1000)
  (setv writes-before (len (.record-writes world))
        pushes-before (len world.acp.pushes))
  ;; 50 ms 刻みで 10 拍(合計 500 ms・追記の周期の中): push は拍ごと・記録の書きは増えない・出来事は持ち越し。
  (for [i (range 10)]
    (setv lines (+ lines (delta-line f"w{i} ")))
    (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
    (.tick world 50))
  (assert (>= (- (len world.acp.pushes) pushes-before) 10) "push は拍ごと")
  (assert (= (len (.record-writes world)) writes-before) "記録の書きは周期の中では増えない")
  (assert (= (len (. (get world.state.jobs 0) pending-entries)) 10) "書かない拍の出来事は持ち越す")
  ;; 周期が経った拍に 1 回で書く(持ち越した 10 件が乗る)。
  (setv lines (+ lines (delta-line "last ")))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
  (.tick world 600)
  (assert (= (len (.record-writes world)) (+ writes-before 1)) "周期が経てば 1 回で書く")
  (assert (= (len (. (get world.state.jobs 0) pending-entries)) 0))
  (assert (= (len (lfor e (.record-entries world) :if (= (get e "kind") "text") e)) 11) "持ち越した出来事は落ちない"))


(deftest test-a-session-wake-settles-the-turn-end-in-that-tick-without-relisting
  ;; 段 12 lane 12b(agora-redesign #207 根 1): host の monitor が手番の終わり(turn_ended_at)を刻んだ拍に、器の出来事の
  ;; 合図(WatchAdvance kind session)が拍を起こす。その拍は ACP の行を読み直さず(list-mode none — ACP の sequence は
  ;; 進んでいない)、走っている job の観測だけで turn-record を ended・job を Ended にする。拍の周期(transcript_poll_seconds)
  ;; を待たない — 周期は保険。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  ;; 手番の材料(記録が進んだ証拠 = 送った本文が届いて手番が始まった)。
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
        (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
           (stream-line {"type" "assistant"
                         "message" {"id" "msg-1" "role" "assistant" "model" "claude-opus-5"
                                    "content" [{"type" "text" "text" "done"}]
                                    "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0
                                             "cache_read_input_tokens" 0}}})))
  (.tick world 1000)
  (assert (= (.job-phase world) "Running"))
  ;; ACP を静める(前の拍の書きで進んだ sequence に since を追いつかせる)— 次の拍を起こすのは器の合図だけ。
  (.tick world 100)
  (assert (= world.state.since world.acp.sequence))
  ;; host が手番の終わりを刻む → 器の合図だけが届く(ACP の sequence は進めない)。
  (.finish-turn world.sessions sid (+ world.local.now-ms 200))
  (setv since world.state.since)
  (setv lists-before (len world.acp.lists))
  (.append world.acp.wakes (WatchAdvance :kind "session" :sequence since))
  (.tick world 100)
  (assert (= (len world.acp.lists) lists-before) "器の合図の拍は一覧(AcpGet)を撃たない")
  (assert (= (.job-phase world) PHASE-ENDED) "合図の拍で job は Ended")
  (assert (= (get (.record-status world) "state") "ended") "合図の拍で turn-record は ended")
  (assert (= world.state.jobs #()))
  (assert (= world.state.since since) "器の合図は ACP の since を進めない"))


(deftest test-list-mode-for-reads-a-session-wake-as-none
  ;; 判断の 1 点: 器の合図(kind session)は行の読み直しを求めない(ACP の sequence が進んでいない)— 周期の保険が来て
  ;; いなければ none。
  (setv settings (AgentdSettings :node-name NODE :backend-kind "headless" :stream-capability "events"))
  (setv state (replace (initial-state) :last-resync-ms 1000 :since 7))
  (assert (= (run (list-mode-for (WatchAdvance :kind "session" :sequence 7) state 1500 settings)) LIST-MODE-NONE)))
