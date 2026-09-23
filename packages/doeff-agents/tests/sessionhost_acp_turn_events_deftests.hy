;;; 手番の出来事(turn-record の status.entries)の耐久化の焦点の検(段 8 lane 4u・agora-redesign #49・
;;; ADR-DOE-AGENTS-012 R19)。
;;;
;;; 既知の形 = event-sourced の出来事の列(append-only・durable): runner(agentd)が出来事の書き手、
;;; control plane(ACP の行)が正本、画面は read model。ここで撃つのは
;;;   * 出来事 → entry の純関数(text / tool_use / tool_result / system / error・toolUseId — 段 9f lane 9f-4 より
;;;     entry は見出し〔TurnEntryHeadline〕で本文の欄を持たない・本文は record の検が撃つ)
;;;   * 行の上限(byte)の切り詰め: 古い出来事から落とし、先頭に印(kind system・dropped)を残す・印を引き継ぐ
;;;   * 追記の post-image(行の entries + 新しい出来事)と手番の終わりの書き(追記の上に ended・usage)
;;;   * 採番の衝突(拾い直した job の seq 0)は行の次から振り直す
;;;   * fake の handler で agentd を一周: 出来事は手番の**途中**で行に在る・Conflict は読み直して積み直す・
;;;     断られた出来事は持ち越して手番の終わりに乗る
;;;   * 段 9p(agora-redesign #76): 行を作れない拍(頭が答えない)は周期で作り直し、作れた拍に持ち越した出来事が乗る・
;;;     決定論的な断りと期限切れは given-up で Ended に condition RecordUnavailable(記録なしで黙って終わらない)
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import dataclasses)
(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  Conflict
  DELTA-INPUT-STRING-LIMIT
  DeltaBatch
  InFlightJob
  MESSAGE-KIND
  OpenToolBlock
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  PHASE-PENDING
  PHASE-RUNNING
  PHASE-WITHDRAWN
  RECORD-CREATE-CREATED
  RECORD-CREATE-GIVEN-UP
  RECORD-CREATE-PENDING
  Refused
  TURN-RECORD-ENTRIES-BYTE-BUDGET
  MODEL-UNDECLARED
  TURN-RECORD-KIND
  TURN-RECORD-SWEEP-END
  TURN-RECORD-SWEEP-SKIP
  TurnEntryHeadline
  Written])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  claude-result-error
  provider-limit-condition-of
  attempt-refused?
  binding-attempt-of
  job-rows-running-on
  live-node-names-of
  turn-record-sweep-verdict
  refused-attempt-status-of
  retired-rows-of
  claude-system-note
  deltas-of
  tool-input-chunks
  entries-within-budget
  entry-json-of
  headline-of-body
  is-drop-marker
  next-seq-after
  renumbered-entries
  text-body
  tool-result-body
  tool-use-body
  tool-use-frame
  record-create-verdict
  turn-record-appended-status
  turn-record-ended-status])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "mac-1")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv AT 1789000000000)


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record) "\n"))


(defn #^ str claude-events [#^ str session-id #^ str text]
  "claude の print mode(stream-json)の 1 手番の行(init・本文の delta・本文 + 道具・結果・result)。"
  (setv usage {"input_tokens" 3 "output_tokens" 7 "cache_creation_input_tokens" 1 "cache_read_input_tokens" 2})
  (.join "" [(stream-line {"type" "system" "subtype" "init" "session_id" session-id "model" "claude-opus-5"
                           "permissionMode" "bypassPermissions" "cwd" "/work" "tools" ["Bash" "Read"]})
             (stream-line {"type" "system" "subtype" "status" "status" "requesting"})
             (stream-line {"type" "stream_event"
                           "event" {"type" "content_block_delta" "index" 0
                                    "delta" {"type" "text_delta" "text" text}}})
             (stream-line {"type" "assistant"
                           "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "text" "text" text}
                                                 {"type" "tool_use" "id" "t1" "name" "Read"
                                                  "input" {"file_path" "/work/a.txt"}}]
                                      "usage" usage}})
             (stream-line {"type" "user"
                           "message" {"role" "user"
                                      "content" [{"type" "tool_result" "tool_use_id" "t1"
                                                  "content" "alpha" "is_error" False}]}})
             (stream-line {"type" "result" "subtype" "success" "is_error" False "usage" usage})]))


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

  (defn #^ AcpRow record [self]
    (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1"))

  (defn #^ dict record-status [self]
    (setv status (. (.record self) status))
    (assert (isinstance status dict))
    status)

  (defn #^ list record-entries [self]
    (setv entries (.get (.record-status self) "entries" []))
    (assert (isinstance entries list))
    (list entries))

  (defn #^ list record-writes [self]
    (lfor [key status] self.acp.writes :if (in f":{TURN-RECORD-KIND}:" key) status)))


;; ---------------------------------------------------------------------------
;; 出来事 → entry の純関数
;; ---------------------------------------------------------------------------

(deftest test-entries-carry-tool-use-id-and-no-body
  ;; tool_use: toolName・toolUseId・bytes / sha256(本文の同一性)— summary も input も無い(段 9f lane 9f-4)。
  (setv use (run (headline-of-body (run (tool-use-body 3 AT "t1" "Bash" {"command" "ls"})))))
  (assert (isinstance use TurnEntryHeadline))
  (assert (= #(use.seq use.at use.kind use.tool-name use.tool-use-id use.is-error) #(3 AT "tool_use" "Bash" "t1" False)))
  (setv use-json (run (entry-json-of use)))
  (assert (= (set (.keys use-json)) #{"seq" "at" "kind" "toolName" "toolUseId" "bytes" "sha256"}) use-json)
  (assert (> (get use-json "bytes") 0))
  ;; tool_result: toolUseId・isError は誤りの時だけ名乗る・output は無い。
  (setv result (run (entry-json-of (run (headline-of-body (run (tool-result-body 5 AT "t1" "a\nb" False)))))))
  (assert (= (set (.keys result)) #{"seq" "at" "kind" "toolUseId" "bytes" "sha256"}) result)
  (setv failed (run (entry-json-of (run (headline-of-body (run (tool-result-body 6 AT "t1" [{"type" "text" "text" "boom"}] True)))))))
  (assert (is (get failed "isError") True))
  (assert (not-in "boom" (json.dumps failed :ensure-ascii False)) "本文が見出しに漏れた")
  ;; text: 本文も model も無い(見出しに欄が無い — 発明しない)。長い本文でも見出しの大きさは変わらない。
  (setv text (run (entry-json-of (run (headline-of-body (run (text-body 7 AT "hello" "claude-opus-5")))))))
  (assert (= (set (.keys text)) #{"seq" "at" "kind" "bytes" "sha256"}) text)
  (setv long-text (run (entry-json-of (run (headline-of-body (run (text-body 8 AT (* "y" 100000) None)))))))
  (assert (= (get long-text "bytes") (+ 100000 (len "{\"text\":\"\"}"))))
  (assert (not-in "truncated" long-text) "agentd は切らない(切り詰めは service の責務)")
  ;; 反例(型): 見出しの型は本文の欄を持たない — 欄の集合は閉じている(text を渡す呼び出しは型検査と実行の両方で落ちる)。
  (setv names (set (lfor field (dataclasses.fields TurnEntryHeadline) field.name)))
  (assert (= names #{"seq" "at" "kind" "bytes" "sha256" "tool_name" "tool_use_id" "is_error"}) names)
  (assert (= (set.intersection names #{"text" "summary" "input" "output" "model"}) (set)) "見出しの型に本文の欄が在る"))


(deftest test-claude-system-and-result-lines-become-system-and-error-entries
  ;; system の行: init と API の retry と hook の失敗だけを人の読む 1 行に(雑音は None)。
  (assert (= (run (claude-system-note {"type" "system" "subtype" "init" "model" "claude-opus-5"
                                       "permissionMode" "bypassPermissions" "cwd" "/w" "tools" ["a" "b"]}))
             "session started: model claude-opus-5 · permission bypassPermissions · cwd /w · tools 2"))
  (assert (= (run (claude-system-note {"type" "system" "subtype" "api_retry" "attempt" 1 "max_retries" 10
                                       "error_status" 401 "error" "authentication_failed"}))
             "API retry 1/10: 401 authentication_failed"))
  (assert (= (run (claude-system-note {"type" "system" "subtype" "hook_response" "hook_name" "Stop:x"
                                       "exit_code" 2 "outcome" "failure" "stderr" "nope\n"}))
             "hook Stop:x failed (exit 2): nope"))
  (assert (is (run (claude-system-note {"type" "system" "subtype" "hook_response" "hook_name" "Stop:x"
                                        "exit_code" 0 "outcome" "success"})) None))
  (for [noise ["status" "hook_started" "thinking_tokens"]]
    (assert (is (run (claude-system-note {"type" "system" "subtype" noise})) None)))
  ;; result の行: 成功は記録しない・誤りは理由(result の本文 → errors → subtype)。
  (assert (is (run (claude-result-error {"type" "result" "subtype" "success" "is_error" False})) None))
  (assert (= (run (claude-result-error {"type" "result" "subtype" "error_during_execution" "is_error" True
                                        "result" "rate limited"})) "rate limited"))
  (assert (= (run (claude-result-error {"type" "result" "subtype" "error_max_turns" "is_error" True}))
             "error_max_turns"))
  ;; streamed の deltas-of は system / error を entries に写す(frame には出さない)。
  (setv text (+ (stream-line {"type" "system" "subtype" "init" "model" "m"})
                (stream-line {"type" "result" "subtype" "error_during_execution" "is_error" True "result" "boom"})))
  (setv batch (run (deltas-of "claude" "events" text "job" 0 AT #())))
  (assert (= (lfor entry batch.entries entry.kind) ["system" "error"]))
  (assert (= (lfor entry batch.entries entry.seq) [0 1]))
  (assert (= (get (get batch.bodies 1) "text") "boom") "本文は bodies に(見出しには無い)")
  (assert (= batch.frames #()))
  ;; transcript(tui)の行は system / result を読まない(従来どおり)。
  (setv quiet (run (deltas-of "claude" "transcript" text "job" 0 AT #())))
  (assert (= quiet.entries #()))
  (assert (= quiet.entries #())))


(deftest test-a-turn-refused-by-the-providers-limit-keeps-the-row-running-and-names-the-attempt
  ;; 段 11 lane 11n 便 C(agora-redesign #179)→ agora-redesign #519(段 12・D-519-3): 一周の後半 —— 器が限度の断りで
  ;; 終端(status failed・cause rate_limited)。制御面は cause を読み、agent-job に条件 ProviderLimit{model, profile, attempt, at}
  ;; を刻み、**phase は Ended にしない**(終端の巻き戻しは engine が断る — 置き直しは配置の supervision provider-refused)。
  ;; turn-record も ended にしない(1 手番 1 行・次の試みが続ける)。札は返し、memory から外し、置き直し待ちの行は拾い直さない。
  ;; 実弾 2026-09-17 20:29〜21:15: Ended に書いた手番の郵便が delivered のまま 46 分止まった。
  (setv world (refused-world))
  (setv said "You've hit your individual spend limit · ask your admin to raise it")
  (setv status (job-status world))
  (assert (= (get status "phase") PHASE-RUNNING) status)
  (assert (isinstance (.get status "sessionHandle") dict) "sessionHandle は行のまま")
  (assert (= (get (get status "binding") "profile") "personal") status)
  (setv conditions (job-conditions world))
  (setv limits (lfor item conditions :if (= (.get item "type") "ProviderLimit") item))
  (assert (= (len limits) 1) conditions)
  (setv limit (get limits 0))
  (assert (= (get limit "status") "True") limit)
  (assert (= (get limit "reason") "rate-limited") limit)
  ;; model = 手番が走らせようとした model(charter.model)ちょうど。
  (assert (= (get limit "model") "claude-opus-5") limit)
  (assert (= (get limit "message") said) limit)
  ;; #519: 記録は口座(この手番の binding.profile)・試み(binding.attempt — 欄の無い結びは 1)・時刻(記録を書いた拍)を名乗る。
  (assert (= (get limit "profile") "personal") limit)
  (assert (= (get limit "attempt") 1) limit)
  (assert (= (get limit "at") world.local.now-ms) limit)
  (assert (not-in "until" limit) "until は書かない(窓を知るのは予算の controller)")
  ;; 器の終端の条件(SessionFailed)は足さない — この試みの結末は ProviderLimit の記録が名乗る(Ended の語彙は Ended の時だけ)。
  (assert (not-in "SessionFailed" (lfor item conditions (.get item "type"))) conditions)
  ;; turn-record は running のまま(次の試みが同じ行を続ける)。
  (assert (= (get (.record-status world) "state") "running") (.record-status world))
  ;; 出来事の側は今日どおり(kind error の見出し 1 行に CLI の文が残る)。
  (assert (in "error" (lfor entry (.record-entries world) (get entry "kind"))))
  ;; 札は返し、memory からは外す。
  (assert (= (len world.custody.revoked) 1) world.custody.revoked)
  (assert (= world.state.jobs #()) world.state.jobs)
  ;; log に 1 行(どの試み・どの口座・どの model が断られたか)。
  (assert (any (gfor line world.local.logs (and (in "refused by the provider's limit" line) (in "attempt 1" line) (in "profile personal" line)))) world.local.logs)
  ;; 次の拍: 置き直し待ちの Running の行(自分の記録がいまの試みを名乗る)は拾い直さない — fail-missing で Ended にしない。
  (.tick world 1000)
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.state.jobs)
  (assert (= (get (job-status world) "phase") PHASE-RUNNING) (job-status world))
  (assert (= (lfor item (job-conditions world) :if (in (.get item "type") ["SessionFailed" "SessionLost"]) item) []) (job-conditions world))
  (assert (= (get (.record-status world) "state") "running"))
  ;; 限度でない終端(普通の失敗)は今日どおり Ended(条件 ProviderLimit は乗らない・記録は ended)。
  (setv plain (World))
  (.tick plain 0)
  (setv psid (.sid plain))
  (.tick plain 1000)
  (.finish plain.sessions psid "failed" None None)
  (.tick plain 1000)
  (assert (= (get (job-status plain) "phase") PHASE-ENDED) (job-status plain))
  (assert (= (lfor item (job-conditions plain) :if (= (.get item "type") "ProviderLimit") item) [])
          (job-conditions plain))
  (assert (= (get (.record-status plain) "state") "ended")))


(deftest test-the-next-attempt-of-a-refused-turn-continues-the-same-turn-record
  ;; agora-redesign #519(段 12・D-519-3): 配置が断られた試みを置き直し(Pending → Bound attempt 2・別の口座)、同じ node が
  ;; 受けた時 — 新しい session を起こし、turn-record の create は Conflict(既に在る・running)で、拾い直しと同じ採番
  ;; (recovered-record-of = 行の generation + 1・seq は続き)で**同じ記録を続ける**。attempt 1 の記録(ProviderLimit)は行に残る。
  (setv world (refused-world))
  (setv first-sid (.sid world))
  (setv record-generation (. (.record world) generation))
  (setv creates-before (.get world.acp.creates f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1" 0))
  (assert (= creates-before 1))
  ;; 配置の書き(替え玉): Pending へ戻し(binding は試みの記録として残る)、attempt 2 を別の口座 second で同じ node に結ぶ。
  (place-job-again world PHASE-BOUND {"node" NODE "profile" "second" "account" "acct" "attempt" 2 "at" 7000} [])
  (.tick world 1000)
  (setv job (in-flight world))
  (setv second-sid (.sid world))
  (assert (!= second-sid first-sid) "attempt 2 は新しい session")
  (assert (= job.profile "second") job)
  ;; create は撃った(2 度目)が Conflict → 同じ記録を続ける: stream の番は行の generation + 1・seq は行の続き。
  (assert (= (.get world.acp.creates f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1") (+ creates-before 1)))
  (assert (= job.record-create "created") job.record-create)
  (assert (> job.record-attempt 1) job.record-attempt)
  (assert (= job.record-attempt (+ record-generation 1)) #(job.record-attempt record-generation))
  (assert (>= job.delta-seq (len (.record-entries world))) #(job.delta-seq (len (.record-entries world))))
  ;; 記録の行は作り直されていない(spec は attempt 1 の sessionId のまま・state running)。
  (assert (= (get (. (.record world) spec) "sessionId") first-sid))
  (assert (= (get (.record-status world) "state") "running"))
  (assert (any (gfor line world.local.logs (in "continues the existing turn-record" line))) world.local.logs)
  ;; attempt 1 の記録は行に残る(予算の係の材料)。
  (setv limits (lfor item (job-conditions world) :if (= (.get item "type") "ProviderLimit") item))
  (assert (= (lfor item limits (get item "attempt")) [1]) limits)
  ;; attempt 2 が普通に終わる: 記録は ended・agent-job は Ended・attempt 1 の記録はそのまま・新しい断りは無い。
  (setv (get world.local.transcripts f"/events/{second-sid}.events.jsonl") (claude-events second-sid "hello again"))
  (.tick world 1000)
  (.finish world.sessions second-sid "done" {"ok" True} None)
  (.tick world 1000)
  (assert (= (get (job-status world) "phase") PHASE-ENDED) (job-status world))
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (= (lfor item (job-conditions world) :if (= (.get item "type") "ProviderLimit") (get item "attempt")) [1]))
  (assert (= world.state.jobs #())))


(deftest test-a-retired-turn-of-mine-ends-its-turn-record-once
  ;; agora-redesign #519(段 12・D-519-3): 配置が試みの上限で退役させた行(Withdrawn + Unschedulable{retry-budget-exhausted})
  ;; の turn-record は最後の runner(自分)が ended にする — level-triggered・冪等(2 度目の拍は書かない)。
  (setv world (refused-world))
  (assert (= (get (.record-status world) "state") "running"))
  (place-job-again world PHASE-WITHDRAWN {"node" NODE "profile" "personal" "account" "acct" "attempt" 3 "at" 9000}
                   [{"type" "Unschedulable" "status" "True" "reason" "retry-budget-exhausted" "rescues" []}])
  (.tick world 1000)
  (assert (= (get (.record-status world) "state") "ended") (.record-status world))
  (assert (not-in "usage" (.record-status world)) "退役の記録は usage を書かない(消費の和は手番の終わりだけ)")
  (setv ended-writes (lfor status (.record-writes world) :if (= (.get status "state") "ended") status))
  (assert (= (len ended-writes) 1) ended-writes)
  (assert (any (gfor line world.local.logs (in "turn-record of retired job j-1 ended" line))) world.local.logs)
  ;; 冪等: 次の拍は書かない。
  (.tick world 1000)
  (assert (= (len (lfor status (.record-writes world) :if (= (.get status "state") "ended") status)) 1))
  ;; agent-job の phase は触らない(Withdrawn のまま — 書き手は配置)。
  (assert (= (get (job-status world) "phase") PHASE-WITHDRAWN))
  ;; 退役でない Withdrawn(operator の取り下げ — Cancelled)の記録は触らない(今日どおり interrupt-job の腕の座)。
  (setv other (refused-world))
  (place-job-again other PHASE-WITHDRAWN {"node" NODE "profile" "personal" "account" "acct" "attempt" 1}
                   [{"type" "Cancelled" "status" "True" "reason" "operator"}])
  (.tick other 1000)
  (assert (= (get (.record-status other) "state") "running") (.record-status other)))


(deftest test-refused-attempt-judgements-read-the-binding-attempt-and-the-record
  ;; agora-redesign #519 の純関数: binding.attempt の読み(欄なし = 1・bool は数でない)/ attempt-refused?(いまの試みを名乗る
  ;; ProviderLimit True だけ)/ refused-attempt-status-of(phase・sessionHandle はそのまま・条件を足すだけ)/
  ;; job-rows-running-on(置き直し待ちの行は拾い直さない)/ retired-rows-of(退役 + 自分の行だけ)。
  (assert (= (run (binding-attempt-of {"binding" {"attempt" 3}})) 3))
  (assert (= (run (binding-attempt-of {"binding" {"node" NODE}})) 1))
  (assert (= (run (binding-attempt-of {})) 1))
  (assert (= (run (binding-attempt-of {"binding" {"attempt" True}})) 1))
  (assert (= (run (binding-attempt-of {"binding" {"attempt" 0}})) 1))
  (setv record-1 {"type" "ProviderLimit" "status" "True" "attempt" 1 "at" AT "profile" "personal"})
  (setv record-2 {"type" "ProviderLimit" "status" "True" "attempt" 2 "at" AT "profile" "second"})
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 1} "conditions" [record-1]})) True))
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 2} "conditions" [record-1]})) False) "古い attempt の記録")
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 2} "conditions" [record-1 record-2]})) True))
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"node" NODE} "conditions" [record-1]})) True) "欄の無い結びは attempt 1")
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 1} "conditions" [{"type" "ProviderLimit" "status" "True"}]})) False) "attempt を名乗らない旧 agentd の記録")
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 1} "conditions" [{#** record-1 "status" "False"}]})) False))
  (assert (is (run (attempt-refused? {"phase" "Running" "binding" {"attempt" 1}})) False))
  ;; refused-attempt-status-of: phase / sessionHandle / binding / result はそのまま、条件は末尾に足す。
  (setv before {"phase" "Running" "binding" {"attempt" 1 "profile" "personal"} "sessionHandle" {"sessionId" "s-1"}
                "conditions" [{"type" "InputUnavailable" "status" "True"}]})
  (setv after (run (refused-attempt-status-of before #(record-1))))
  (assert (= (get after "phase") "Running"))
  (assert (= (get after "sessionHandle") {"sessionId" "s-1"}))
  (assert (= (get after "binding") {"attempt" 1 "profile" "personal"}))
  (assert (= (lfor item (get after "conditions") (get item "type")) ["InputUnavailable" "ProviderLimit"]))
  (assert (= (get before "conditions") [{"type" "InputUnavailable" "status" "True"}]) "元の status は触らない")
  ;; job-rows-running-on: 自分の Running の行のうち、いまの試みを名乗る記録を持つ行は外す。
  (setv handle {"sessionId" "s-1" "stream" {"owner" "agentd"}})
  (setv waiting (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-w" {"subject" CONVERSATION "inputs" [] "charter" {}}
                        {"phase" PHASE-RUNNING "binding" {"node" NODE "attempt" 1} "sessionHandle" handle "conditions" [record-1]}))
  (setv moved-on (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-m" {"subject" CONVERSATION "inputs" [] "charter" {}}
                         {"phase" PHASE-RUNNING "binding" {"node" NODE "attempt" 2} "sessionHandle" handle "conditions" [record-1]}))
  (setv plain (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-p" {"subject" CONVERSATION "inputs" [] "charter" {}}
                      {"phase" PHASE-RUNNING "binding" {"node" NODE} "sessionHandle" handle "conditions" []}))
  (setv mine (run (job-rows-running-on #(waiting moved-on plain) NODE None "agentd")))
  (assert (= (lfor row mine row.resource-id) ["j-m" "j-p"]) (lfor row mine row.resource-id))
  ;; retired-rows-of: Withdrawn + Unschedulable{retry-budget-exhausted} + 自分の handle の行だけ。
  (setv retired-condition {"type" "Unschedulable" "status" "True" "reason" "retry-budget-exhausted" "rescues" []})
  (setv retired (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-r" {"subject" CONVERSATION "inputs" [] "charter" {}}
                        {"phase" PHASE-WITHDRAWN "binding" {"node" NODE "attempt" 3} "sessionHandle" handle "conditions" [record-1 retired-condition]}))
  (setv cancelled (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-c" {"subject" CONVERSATION "inputs" [] "charter" {}}
                          {"phase" PHASE-WITHDRAWN "binding" {"node" NODE "attempt" 1} "sessionHandle" handle "conditions" [{"type" "Cancelled" "status" "True" "reason" "operator"}]}))
  (setv elsewhere (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-e" {"subject" CONVERSATION "inputs" [] "charter" {}}
                          {"phase" PHASE-WITHDRAWN "binding" {"node" "other-node" "attempt" 3} "sessionHandle" {"sessionId" "s-9" "stream" {"owner" "agentd"}} "conditions" [retired-condition]}))
  (setv still-running (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND "j-s" {"subject" CONVERSATION "inputs" [] "charter" {}}
                              {"phase" PHASE-RUNNING "binding" {"node" NODE "attempt" 3} "sessionHandle" handle "conditions" [retired-condition]}))
  (setv found (run (retired-rows-of #(retired cancelled elsewhere still-running) NODE None "agentd")))
  (assert (= (lfor row found row.resource-id) ["j-r"]) (lfor row found row.resource-id)))


(deftest test-provider-limit-condition-reads-the-containers-terminal-cause-not-the-cli-text
  ;; 段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 案 c′): 制御面の判断は
  ;; **器が書いた終端の cause** を読む 1 点(族の表は器の側 = impls/markers.hy の 1 点で、
  ;; ここには無い — ADR-DOE-AGENTS-008 R1)。category = rate_limited だけが条件になる。
  (setv said "You've reached your Fable limit. /model to switch models.")
  (setv condition (run (provider-limit-condition-of {"category" "rate_limited" "reason" said "limit_scope" "model"} "claude-fable-5-1" "personal" 2 AT)))
  ;; agora-redesign #519: 記録は口座・試み・時刻を自分で名乗る(契約 scheduling.json providerRefusal.fields)。
  (assert (= condition {"type" "ProviderLimit" "status" "True" "reason" "rate-limited"
                        "message" said "model" "claude-fable-5-1" "scope" "model"
                        "profile" "personal" "attempt" 2 "at" AT}) condition)
  (assert (not-in "until" condition) "until は書かない(窓を知るのは予算の controller)")
  ;; 口座の名が無い手番(結ばれていない・空)は profile の欄を落とす(発明しない)。attempt / at は常に名乗る。
  (setv unbound (run (provider-limit-condition-of {"category" "rate_limited" "reason" said} "claude-fable-5-1" None 1 AT)))
  (assert (not-in "profile" unbound) unbound)
  (assert (= #((get unbound "attempt") (get unbound "at")) #(1 AT)) unbound)
  (assert (not-in "profile" (run (provider-limit-condition-of {"category" "rate_limited" "reason" said} "claude-fable-5-1" "  " 1 AT))))
  ;; 限度でない終端(器の壊れ・普通の失敗)・cause 無しは条件を作らない(黙って枯渇を名乗らない)。
  (for [cause [{"category" "run_failed" "reason" said}
               {"category" "timed_out" "reason" "deadline"}
               {"category" "cancelled" "reason" "stop"}
               {} None]]
    (assert (is (run (provider-limit-condition-of cause "claude-opus-5" "personal" 1 AT)) None) f"限度でない cause が当たった: {cause}"))
  ;; 理由の文が無い cause でも条件は作る(message は category の語 — 黙って落とさない)。
  (setv bare (run (provider-limit-condition-of {"category" "rate_limited"} "claude-opus-5" "personal" 1 AT)))
  (assert (= (get bare "message") "rate_limited") bare)
  ;; model は「手番が走らせようとした model」ちょうど(charter.model)。宣言の無い手番
  ;; (MODEL-UNDECLARED)は欄を落とす — 限度の拍の usage.model は `<synthetic>` で材料が名乗らない。
  (setv undeclared (run (provider-limit-condition-of {"category" "rate_limited" "reason" said} MODEL-UNDECLARED "personal" 1 AT)))
  (assert (not-in "model" undeclared) undeclared)
  (assert (not-in "model" (run (provider-limit-condition-of {"category" "rate_limited"} None "personal" 1 AT))))
  ;; 改行のある理由は 1 行目だけを message に(行は人が読む 1 行)。
  (setv multi (run (provider-limit-condition-of {"category" "rate_limited" "reason" (+ said "\nTry later.")} "claude-fable-5-1" "personal" 1 AT)))
  (assert (= (get multi "message") said) multi))


(deftest test-provider-limit-condition-names-the-whole-account-unless-the-container-says-model
  ;; 2026-09-23(operator の規則 2026-09-17「profile が費用の上限で止まったら、種類を問わず口座が枯れた 1 事実」):
  ;; 範囲は器の cause の欄 limit_scope(当てるのは器 — R33)。account と欄の無い cause は scope = account で model の欄を
  ;; 書かない(走っていた model の名を付けない)。器が model と言った時だけ scope = model(model = 走らせた model)。
  (setv said "Your group's usage limit is set to $0 · ask your admin for a higher limit")
  (for [cause [{"category" "rate_limited" "reason" said "limit_scope" "account"}
               {"category" "rate_limited" "reason" said}
               {"category" "rate_limited" "reason" "You've reached your Fable 5 limit." "limit_scope" "account"}]]
    (setv condition (run (provider-limit-condition-of cause "claude-opus-5-5" "p10169" 1 AT)))
    (assert (= (get condition "scope") "account") condition)
    (assert (not-in "model" condition) condition))
  (setv scoped (run (provider-limit-condition-of {"category" "rate_limited" "reason" "You've reached your Fable 5 limit." "limit_scope" "model"}
                                                 "claude-fable-5-1" "btc" 1 AT)))
  (assert (= #((get scoped "scope") (get scoped "model")) #("model" "claude-fable-5-1")) scoped)
  ;; 器が載せた戻りの時刻は resetsAt へ写す。載っていない・数でない欄は落とす(発明しない)。
  (setv resets (run (provider-limit-condition-of {"category" "rate_limited" "reason" said "limit_scope" "account" "limit_resets_at_ms" (+ AT 5)}
                                                 "claude-opus-5-5" "p10169" 1 AT)))
  (assert (= (get resets "resetsAt") (+ AT 5)) resets)
  (for [bad [None True "1790143200000"]]
    (assert (not-in "resetsAt" (run (provider-limit-condition-of {"category" "rate_limited" "reason" said "limit_resets_at_ms" bad}
                                                                 "claude-opus-5-5" "p10169" 1 AT))))))


(deftest test-tool-use-frame-carries-the-input-and-names-what-it-clipped
  ;; 段 10 lane 10j(agora-redesign #87 の裁定 問 7 / 8): 実況の道具の呼び出しは入力の object を**そのまま**運ぶ —
  ;; 表示のための whitelist は使わない(MultiEdit の edits も Read の offset / limit も落ちない)。
  (setv given {"file_path" "/work/a.ts" "offset" 10 "limit" 2
               "edits" [{"old_string" "a" "new_string" "b"}]})
  (setv frame (run (tool-use-frame "job" 3 AT "t1" "MultiEdit" "s" given)))
  (assert (= (get frame "kind") "tool_use"))
  (setv payload (get frame "payload"))
  (assert (= (get payload "input") given) payload)
  (assert (not-in "clipped" payload) payload)
  ;; 文字列は 1 つ DELTA-INPUT-STRING-LIMIT 字で切り、切った所を path で名乗る(入れ子の中も同じ規則)。
  (setv long (* "x" (+ DELTA-INPUT-STRING-LIMIT 1)))
  (setv clipped-frame (run (tool-use-frame "job" 4 AT "t2" "MultiEdit" "s"
                                           {"file_path" "/work/a.ts"
                                            "edits" [{"old_string" long "new_string" "b"}]})))
  (setv clipped-payload (get clipped-frame "payload"))
  (assert (= (get clipped-payload "clipped") ["input.edits.0.old_string"]) clipped-payload)
  (assert (= (len (get (get (get (get clipped-payload "input") "edits") 0) "old_string"))
             DELTA-INPUT-STRING-LIMIT))
  ;; encode した frame が上限を超える時は input を載せない(印は根の 1 語 — 面は summary で描く)。
  (setv many (dfor index (range 20) f"k{index}" (* "y" DELTA-INPUT-STRING-LIMIT)))
  (setv heavy (run (tool-use-frame "job" 5 AT "t3" "Write" "s" many)))
  (setv heavy-payload (get heavy "payload"))
  (assert (not-in "input" heavy-payload) (list heavy-payload))
  (assert (= (get heavy-payload "clipped") ["input"]) heavy-payload)
  ;; object でない入力(codex の function_call.arguments / commandExecution の command = 文字列)は名乗らない。
  (setv codex (run (tool-use-frame "job" 6 AT "t4" "shell" "{\"command\":\"ls\"}" "{\"command\":\"ls\"}")))
  (assert (not-in "input" (get codex "payload")) codex)
  (assert (not-in "clipped" (get codex "payload")) codex)
  ;; 走行器の行から組んだ実況にも input が載る(claude の stream-json の 1 手番)。
  (setv batch (run (deltas-of "claude" "events" (claude-events "s-1" "本文") "job" 0 AT #())))
  (setv uses (lfor item batch.frames :if (= (get item "kind") "tool_use") item))
  (assert (= (len uses) 1) uses)
  (assert (= (get (get (get uses 0) "payload") "input") {"file_path" "/work/a.txt"}) uses))


;; ---------------------------------------------------------------------------
;; 道具の呼び出しの書きかけの引数(tool_input_delta — 2026-09-19・card acp:kanban-issue:ki-0d0bcd1e81d9)
;; ---------------------------------------------------------------------------

(defn #^ str block-event [#^ dict event #^ (| str None) parent]
  "走行器の stream_event の 1 行(parent = 行の parent_tool_use_id)。"
  (stream-line {"type" "stream_event" "event" event "parent_tool_use_id" parent}))


(defn #^ str tool-start [#^ int index #^ str tool-id #^ str name]
  (block-event {"type" "content_block_start" "index" index
                "content_block" {"type" "tool_use" "id" tool-id "name" name "input" {}}} None))


(defn #^ str input-delta [#^ int index #^ str piece]
  (block-event {"type" "content_block_delta" "index" index
                "delta" {"type" "input_json_delta" "partial_json" piece}} None))


(defn #^ list drafts-of [#^ DeltaBatch batch]
  (lfor item batch.frames :if (= (get item "kind") "tool_input_delta") item))


(deftest test-tool-input-deltas-of-one-read-join-into-one-frame-per-tool
  ;; 1 回の読みの中の同じ道具の差分は連結して 1 frame(束ねる粒 = 読みの周期 — 時間の定数を足さない)。chunk は
  ;; partial_json の続きの文字列ちょうどで、JSON として解釈しない(閉じていない断面のまま運ぶ)。
  (setv text (+ (tool-start 1 "toolu_1" "Bash")
                (input-delta 1 "")
                (input-delta 1 "{\"comm")
                (input-delta 1 "and\": \"ec")))
  (setv batch (run (deltas-of "claude" "events" text "job" 5 AT #())))
  (setv drafts (drafts-of batch))
  (assert (= (len drafts) 1) drafts)
  (assert (= (get (get drafts 0) "payload") {"toolUseId" "toolu_1" "name" "Bash" "chunk" "{\"command\": \"ec"}) drafts)
  (assert (= (get (get drafts 0) "seq") 5))
  (assert (= batch.next-seq 6))
  ;; 書きかけは実況だけ: 記録(bodies / entries)には 1 字も入らない。
  (assert (= batch.bodies #()) batch.bodies)
  (assert (= batch.entries #()) batch.entries)
  ;; block はまだ開いている(次の読みの入力)。
  (assert (= batch.open-tool-blocks #((OpenToolBlock :parent "" :index 1 :tool-use-id "toolu_1" :name "Bash"))))
  (assert (= batch.orphan-input-deltas 0))
  ;; 次の読み: 開いた block の表を入力に、続きが同じ id と名で出る(開始の行はもう材料に無い)。
  (setv later (run (deltas-of "claude" "events" (+ (input-delta 1 "ho hi\"") (input-delta 1 "}"))
                              "job" batch.next-seq AT batch.open-tool-blocks)))
  (assert (= (lfor item (drafts-of later) (get item "payload"))
             [{"toolUseId" "toolu_1" "name" "Bash" "chunk" "ho hi\"}"}]))
  ;; 道具の開始の拍に走行器が送る空の差分だけの読みは、空の chunk の frame 1 つ(名前だけのカードが開始の拍に出る)。
  (setv opening (run (deltas-of "claude" "events" (+ (tool-start 0 "toolu_9" "Write") (input-delta 0 "")) "job" 0 AT #())))
  (assert (= (lfor item (drafts-of opening) (get item "payload")) [{"toolUseId" "toolu_9" "name" "Write" "chunk" ""}])))


(deftest test-tool-input-deltas-without-a-seen-start-are-counted-not-framed
  ;; 開始(content_block_start)を見ていない差分は frame にしない — id も名前も発明しない。黙って捨てず数える。
  (setv batch (run (deltas-of "claude" "events" (+ (input-delta 1 "{\"a\"") (input-delta 1 ": 1}")) "job" 0 AT #())))
  (assert (= (drafts-of batch) []))
  (assert (= batch.frames #()))
  (assert (= batch.orphan-input-deltas 2))
  (assert (= batch.next-seq 0) "frame にしない差分が seq を使っている")
  ;; id か名前を名乗らない開始は block を開かない(その差分も数えるだけ)。
  (setv nameless (+ (block-event {"type" "content_block_start" "index" 2
                                  "content_block" {"type" "tool_use" "id" "toolu_2" "name" ""}} None)
                    (input-delta 2 "{}")))
  (setv quiet (run (deltas-of "claude" "events" nameless "job" 0 AT #())))
  (assert (= (drafts-of quiet) []))
  (assert (= quiet.orphan-input-deltas 1))
  (assert (= quiet.open-tool-blocks #())))


(deftest test-open-tool-blocks-close-at-stop-and-reset-at-message-start
  (setv opened #((OpenToolBlock :parent "" :index 1 :tool-use-id "toolu_1" :name "Bash")))
  ;; content_block_stop で閉じる。
  (setv stopped (run (deltas-of "claude" "events" (block-event {"type" "content_block_stop" "index" 1} None) "job" 0 AT opened)))
  (assert (= stopped.open-tool-blocks #()))
  ;; message_start でその message の表を空に戻す(index は message ごとの番号 — 前の message の道具へ結ばない)。
  (setv restarted (run (deltas-of "claude" "events"
                                  (+ (block-event {"type" "message_start" "message" {"id" "msg_2"}} None) (input-delta 1 "{"))
                                  "job" 0 AT opened)))
  (assert (= (drafts-of restarted) []))
  (assert (= restarted.orphan-input-deltas 1))
  ;; 同じ番号の本文の block が始まれば、前の道具の block は終わっている(番号の使い回し)。
  (setv reused (run (deltas-of "claude" "events"
                               (+ (block-event {"type" "content_block_start" "index" 1
                                                "content_block" {"type" "text" "text" ""}} None)
                                  (input-delta 1 "{"))
                               "job" 0 AT opened)))
  (assert (= (drafts-of reused) []))
  ;; 下請けの agent の message(parent_tool_use_id つき)は別の表: 同じ番号でも親の道具へ結ばない・親の表を消さない。
  (setv child (+ (block-event {"type" "message_start" "message" {"id" "msg_c"}} "toolu_task")
                 (block-event {"type" "content_block_delta" "index" 1
                               "delta" {"type" "input_json_delta" "partial_json" "{\"x"}} "toolu_task")))
  (setv nested (run (deltas-of "claude" "events" child "job" 0 AT opened)))
  (assert (= (drafts-of nested) []))
  (assert (= nested.orphan-input-deltas 1))
  (assert (= nested.open-tool-blocks opened))
  ;; transcript(tui)と codex は引数の差分を運ばない — 書きかけを発明しない。
  (setv tui (run (deltas-of "claude" "transcript" (+ (tool-start 1 "toolu_1" "Bash") (input-delta 1 "{")) "job" 0 AT #())))
  (assert (= (drafts-of tui) []))
  (setv codex (run (deltas-of "codex" "events" (+ (tool-start 1 "toolu_1" "Bash") (input-delta 1 "{")) "job" 0 AT #())))
  (assert (= (drafts-of codex) [])))


(deftest test-the-draft-frame-precedes-the-completed-tool-use-and-splits-an-oversized-join
  ;; 同じ読みに完成の呼び出しが在っても、書きかけの frame はその前(最初の差分の位置)に並ぶ — 読み手は完成で置き換える。
  (setv usage {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0})
  (setv done (stream-line {"type" "assistant"
                           "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "tool_use" "id" "toolu_1" "name" "Bash"
                                                  "input" {"command" "ls"}}]
                                      "usage" usage}}))
  (setv text (+ (tool-start 1 "toolu_1" "Bash") (input-delta 1 "{\"command\"") (input-delta 1 ": \"ls\"}") done))
  (setv batch (run (deltas-of "claude" "events" text "job" 0 AT #())))
  (setv kinds (lfor item batch.frames :if (in (get item "kind") #{"tool_input_delta" "tool_use"}) (get item "kind")))
  (assert (= kinds ["tool_input_delta" "tool_use"]) kinds)
  ;; 完成の呼び出しの frame と記録は今までどおり(input は object・記録は完成した tool_use だけ)。
  (setv use (get (lfor item batch.frames :if (= (get item "kind") "tool_use") item) 0))
  (assert (= (get (get use "payload") "input") {"command" "ls"}))
  (assert (= (lfor entry batch.entries entry.kind) ["tool_use"]))
  ;; 上限(tool_use.input の文字列を切るのと同じ DELTA-INPUT-STRING-LIMIT 字)を超える連結は続きの frame に分ける —
  ;; 字は落とさない(読み手は総字数を chunk の長さの和で数える)・順は保つ・第 2 の上限を置かない。
  (setv long (* "x" (+ DELTA-INPUT-STRING-LIMIT 10)))
  (setv pieces (run (tool-input-chunks long DELTA-INPUT-STRING-LIMIT)))
  (assert (= (lfor piece pieces (len piece)) [DELTA-INPUT-STRING-LIMIT 10]))
  (assert (= (run (tool-input-chunks "" DELTA-INPUT-STRING-LIMIT)) #("")))
  (setv heavy (run (deltas-of "claude" "events" (+ (tool-start 1 "toolu_1" "Write") (input-delta 1 long) done) "job" 0 AT #())))
  (setv order (lfor item heavy.frames :if (in (get item "kind") #{"tool_input_delta" "tool_use"}) (get item "kind")))
  (assert (= order ["tool_input_delta" "tool_input_delta" "tool_use"]) order)
  (assert (= (.join "" (lfor item (drafts-of heavy) (get (get item "payload") "chunk"))) long))
  (setv seqs (lfor item heavy.frames (get item "seq")))
  (assert (= (len (set seqs)) (len seqs)) "frame の seq が重なっている"))


(deftest test-agentd-carries-open-tool-blocks-across-ticks-and-keeps-drafts-out-of-the-record
  ;; agentd を一周: 開いた block の表は拍をまたいで InFlightJob が持ち、書きかけは中継へだけ出る(行の entries にも
  ;; 記録の本文にも入らない)。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv path f"/events/{sid}.events.jsonl")
  (setv part-1 (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
                  (tool-start 1 "toolu_1" "Bash")
                  (input-delta 1 "{\"command\": \"echo ")))
  (setv (get world.local.transcripts path) part-1)
  (.tick world 1000)
  ;; 拍 2 の材料に開始の行は無い — 表が拍をまたいでいなければ、この続きは id も名も持てない。
  (setv part-2 (+ (input-delta 1 "PROBE") (input-delta 1 "\"}")))
  (setv (get world.local.transcripts path) (+ part-1 part-2))
  (.tick world 1000)
  (setv pushed (lfor [_owner _name frames] world.acp.pushes frame frames :if (= (get frame "kind") "tool_input_delta") frame))
  (assert (= (lfor frame pushed (get frame "payload"))
             [{"toolUseId" "toolu_1" "name" "Bash" "chunk" "{\"command\": \"echo "}
              {"toolUseId" "toolu_1" "name" "Bash" "chunk" "PROBE\"}"}])
          pushed)
  ;; 行の entries は init だけ(書きかけは記録に無い)。
  (assert (= (lfor entry (.record-entries world) (get entry "kind")) ["system"]))
  (assert (not-in "PROBE" (json.dumps (.record-status world))))
  (assert (not-in "PROBE" (json.dumps (lfor [_key status] world.acp.writes status)))))


;; ---------------------------------------------------------------------------
;; 行の上限の切り詰めと追記の post-image
;; ---------------------------------------------------------------------------

(defn #^ dict big-entry [#^ int seq #^ int size]
  "行に既に在る entry の JSON(旧の形の行〔text を持つ〕も上限の物差しでは同じ — 読み手は行の JSON をそのまま数える)。"
  {"seq" seq "at" (+ AT seq) "kind" "text" "text" (* "z" size)})


(defn #^ TurnEntryHeadline headline [#^ int seq #^ str text]
  "text の本文 → 見出し(seq・at = AT + seq)。"
  (run (headline-of-body (run (text-body seq (+ AT seq) text None)))))


(deftest test-entries-within-budget-drops-the-oldest-and-leaves-a-marker
  ;; 収まっていればそのまま(印も足さない)。
  (setv small #((big-entry 0 10) (big-entry 1 10)))
  (assert (= (run (entries-within-budget small 1000)) small))
  ;; 超えたら古い側から落とし、先頭に印(kind system・truncated・dropped・seq = 最古・at = 落とした最新)。
  (setv rows (tuple (lfor seq (range 6) (big-entry seq 100))))
  (setv kept (run (entries-within-budget rows 420)))
  (setv marker (get kept 0))
  (assert (run (is-drop-marker marker)))
  (assert (= (get marker "kind") "system"))
  (assert (is (get marker "truncated") True))
  (assert (= (get marker "seq") 0))
  (assert (= (set (.keys marker)) #{"seq" "at" "kind" "truncated" "dropped"}) "印は本文も bytes / sha256 も持たない")
  (assert (>= (get marker "dropped") 3))
  (assert (= (get marker "at") (+ AT (- (get marker "dropped") 1))))
  ;; 新しい側は必ず残り、順は保たれる。
  (assert (= (lfor row (cut kept 1 None) (get row "seq")) (list (range (get marker "dropped") 6))))
  (assert (<= (len (.encode (json.dumps (list kept) :ensure-ascii False :separators #("," ":")) "utf-8")) 420))
  ;; 次の拍: 印を引き継いで数える(dropped は増え、最古の seq は最初の印のまま)。
  (setv again (run (entries-within-budget (+ kept #((big-entry 6 100) (big-entry 7 100))) 420)))
  (setv marker-2 (get again 0))
  (assert (run (is-drop-marker marker-2)))
  (assert (> (get marker-2 "dropped") (get marker "dropped")))
  (assert (= (get marker-2 "seq") 0))
  (assert (= (get (get again -1) "seq") 7))
  (assert (= (len (lfor row again :if (= (get row "kind") "system") row)) 1)))


(deftest test-appended-status-keeps-the-row-entries-and-ended-appends-the-rest
  ;; 追記の post-image: 行の entries + 新しい見出し(JSON への写しは entry-json-of の 1 点)。他の欄(state・usage)は写す。
  (setv first (run (entry-json-of (headline 0 "a"))))
  (setv status {"state" "running" "entries" [first]})
  (setv appended (run (turn-record-appended-status status #((headline 1 "b")))))
  (assert (= (get appended "state") "running"))
  (assert (= (lfor entry (get appended "entries") (get entry "seq")) [0 1]))
  (assert (= (get (get appended "entries") 1) (run (entry-json-of (headline 1 "b")))))
  (assert (= (get status "entries") [first]))
  (for [entry (get appended "entries")]
    (assert (= (set (.keys entry)) #{"seq" "at" "kind" "bytes" "sha256"}) entry))
  ;; 反例(型): 見出しでない entry(JSON の dict)は追記できない。
  (setv refused False)
  (try
    (run (turn-record-appended-status status #({"seq" 1 "at" AT "kind" "text" "text" "b"})))
    (except [TypeError]
      (setv refused True)))
  (assert refused "本文を持つ dict の entry が型で落ちない")
  ;; 手番の終わり: 残りを追記した上で ended・usage。行の entries は落とさない(旧の形 = 置換 を作らない)。
  (setv error-head (run (headline-of-body {"producerSeq" 2 "at" AT "kind" "error" "text" "boom"})))
  (setv ended (run (turn-record-ended-status appended {"input" 1 "output" 2 "cacheWrite" 0 "cacheRead" 0}
                                             #(error-head))))
  (assert (= (get ended "state") "ended"))
  (assert (= (lfor entry (get ended "entries") (get entry "kind")) ["text" "text" "error"]))
  (assert (= (get ended "usage") {"input" 1 "output" 2 "cacheWrite" 0 "cacheRead" 0}))
  ;; 残りが無い終わりは entries をそのまま(空で置換しない)。
  (setv quiet (run (turn-record-ended-status ended None #())))
  (assert (= (len (get quiet "entries")) 3))
  ;; 追記は行の上限を守る(TURN-RECORD-ENTRIES-BYTE-BUDGET)— 行に既に在る大きな entries の上に見出しを積む。
  (setv flood {"state" "running" "entries" (lfor seq (range 3) (big-entry seq (// TURN-RECORD-ENTRIES-BYTE-BUDGET 2)))})
  (setv bounded (run (turn-record-appended-status flood #((headline 3 "c")))))
  (assert (run (is-drop-marker (get (get bounded "entries") 0))))
  (assert (= (get (get (get bounded "entries") -1) "seq") 3)))


(deftest test-recovered-jobs-renumber-past-the-row-sequence
  ;; 拾い直した job は seq 0 から数え直す — 行の seq と衝突すれば行の次から振り直す。
  (setv existing #({"seq" 4 "at" AT "kind" "text" "text" "a"} {"seq" 9 "at" AT "kind" "text" "text" "b"}))
  (assert (= (run (next-seq-after existing 0)) 10))
  (assert (= (run (next-seq-after #() 3)) 3))
  (assert (= (run (next-seq-after existing 20)) 20))
  (setv fresh #((headline 0 "c")
                (run (headline-of-body (run (tool-use-body 1 AT "t1" "Bash" {"command" "ls"}))))))
  (setv renumbered (run (renumbered-entries fresh 10)))
  (assert (= (lfor entry renumbered entry.seq) [10 11]))
  (assert (= (. (get renumbered 1) tool-name) "Bash"))
  (assert (= (. (get renumbered 1) sha256) (. (get fresh 1) sha256)) "振り直しは seq だけ")
  ;; 衝突しなければそのまま(同じ object)。
  (setv clear #((headline 12 "d")))
  (assert (is (run (renumbered-entries clear 10)) clear)))


;; ---------------------------------------------------------------------------
;; agentd を一周: 途中で行に在る・Conflict の積み直し・断りの持ち越し
;; ---------------------------------------------------------------------------

(deftest test-events-are-durable-mid-turn-with-per-tick-timestamps
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  ;; 手番の途中の拍 1: init と本文の前半。
  (setv usage {"input_tokens" 3 "output_tokens" 7 "cache_creation_input_tokens" 1 "cache_read_input_tokens" 2})
  (setv part-1 (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
                  (stream-line {"type" "assistant"
                                "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                           "content" [{"type" "tool_use" "id" "t1" "name" "Read"
                                                       "input" {"file_path" "/work/a.txt"}}]
                                           "usage" usage}})))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") part-1)
  (.tick world 1000)
  (setv first-at world.local.now-ms)
  (setv mid (.record-entries world))
  (assert (= (lfor entry mid (get entry "kind")) ["system" "tool_use"]) "出来事が手番の途中で行に無い")
  (assert (= (get (.record-status world) "state") "running"))
  (assert (= (lfor entry mid (get entry "at")) [first-at first-at]))
  (assert (= (get (get mid 1) "toolUseId") "t1"))
  ;; 拍 2: 結果と本文(at は拍ごと・seq は続き)。
  (setv part-2 (+ (stream-line {"type" "user"
                                "message" {"role" "user"
                                           "content" [{"type" "tool_result" "tool_use_id" "t1" "content" "alpha"}]}})
                  (stream-line {"type" "assistant"
                                "message" {"id" "msg_2" "role" "assistant" "model" "claude-opus-5"
                                           "content" [{"type" "text" "text" "alpha it is"}]
                                           "usage" usage}})))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (+ part-1 part-2))
  (.tick world 1000)
  (setv later (.record-entries world))
  (assert (= (lfor entry later (get entry "kind")) ["system" "tool_use" "tool_result" "text"]))
  (assert (= (get (get later 3) "at") world.local.now-ms))
  (assert (> (get (get later 3) "at") first-at))
  (assert (= (sorted (lfor entry later (get entry "seq"))) (lfor entry later (get entry "seq"))))
  (assert (= (get (get later 2) "toolUseId") "t1"))
  ;; 手番の終わり: 最後の result は同じ拍で読まれ、追記の上に ended・usage(重複なし)。
  (setv part-3 (stream-line {"type" "result" "subtype" "success" "is_error" False "usage" usage}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (+ part-1 part-2 part-3))
  (.finish-turn world.sessions sid (+ world.local.now-ms 100))
  (.tick world 1000)
  (setv final-status (.record-status world))
  (assert (= (get final-status "state") "ended"))
  (assert (= (lfor entry (.record-entries world) (get entry "kind")) ["system" "tool_use" "tool_result" "text"]))
  (setv usage (get final-status "usage"))
  (assert (isinstance usage dict))
  (assert (= (get usage "input") 6))
  (assert (= (len (.record-writes world)) 3) "追記 2 回 + 終わり 1 回のはず"))


(deftest test-append-conflict-rereads-and-retries-once
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1")
  ;; 最初の書きを 1 度だけ Conflict にする(誰かが行を進めた形)。
  (setv (get world.acp.conflict-once key) 7)
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  (setv entries (.record-entries world))
  (assert (= (lfor entry entries (get entry "kind")) ["system" "text" "tool_use" "tool_result"])
          "Conflict の後に読み直して積み直していない")
  (assert (= (len (.record-writes world)) 1))
  (assert (not (any (gfor line world.local.logs (in "did not land" line))))))


(deftest test-refused-append-carries-the-events-to-the-next-write
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1")
  ;; 行を一時的に消す(読めない拍)→ 出来事は持ち越し、行が戻った次の拍に乗る。
  (setv hidden (get world.acp.rows key))
  (del (get world.acp.rows key))
  (setv part-1 (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "m"}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") part-1)
  (.tick world 1000)
  (assert (= (len (.record-writes world)) 0))
  (assert (any (gfor line world.local.logs (in "keeping 1 entries" line))))
  (setv (get world.acp.rows key) hidden)
  (setv part-2 (stream-line {"type" "assistant"
                             "message" {"id" "msg_1" "role" "assistant" "model" "m"
                                        "content" [{"type" "text" "text" "late"}]
                                        "usage" {"input_tokens" 1 "output_tokens" 1
                                                 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0}}}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (+ part-1 part-2))
  (.tick world 1000)
  (setv entries (.record-entries world))
  (assert (= (lfor entry entries (get entry "kind")) ["system" "text"]) "持ち越した出来事が次の書きに乗っていない")
  (assert (< (get (get entries 0) "at") (get (get entries 1) "at")) "持ち越した出来事の at は読んだ拍のまま"))


;; ---------------------------------------------------------------------------
;; 段 9p(agora-redesign #76): 行を作れない拍の作り直し — 頭が答えない間は待って撃ち直す・記録なしで終わらない
;; ---------------------------------------------------------------------------

(defn #^ str record-key []
  f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1")


(defn #^ list job-conditions [#^ World world]
  (setv status (. (get world.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1") status))
  (assert (isinstance status dict))
  (setv conditions (.get status "conditions" []))
  (assert (isinstance conditions list))
  (list conditions))


(defn #^ dict job-status [#^ World world]
  (setv status (. (get world.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1") status))
  (assert (isinstance status dict))
  status)


(defn #^ None place-job-again [#^ World world #^ str phase #^ dict binding #^ list extra-conditions]
  "agora-redesign #519: 配置の書きの替え玉 — 行の phase と binding を書き替え(sessionHandle と既存の条件は行のまま)、
   条件を足す(退役の Unschedulable 等)。generation は 1 進む(engine の CAS と同じ)。"
  (setv key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1")
  (setv row (get world.acp.rows key))
  (setv status (dict row.status))
  (setv (get status "phase") phase)
  (setv (get status "binding") binding)
  (setv (get status "conditions") (+ (list (.get status "conditions" [])) extra-conditions))
  (.put-row world.acp (dataclasses.replace row :generation (+ row.generation 1) :status status))
  None)


(defn #^ World refused-world []
  "agora-redesign #519: 1 手番が口座の限度で断られた直後の世界(行は Running のまま・記録は running・memory は空)。"
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv said "You've hit your individual spend limit · ask your admin to raise it")
  (setv events (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
                  (stream-line {"type" "result" "subtype" "error_during_execution" "is_error" True
                                "result" said})))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") events)
  (.tick world 1000)
  (.finish world.sessions sid "failed" None {"category" "rate_limited" "reason" said})
  (.tick world 1000)
  world)


(defn #^ InFlightJob in-flight [#^ World world]
  (setv found (next (gfor job world.state.jobs :if (= job.job-id "j-1") job) None))
  (assert (isinstance found InFlightJob) "j-1 が memory に無い")
  found)


(deftest test-record-create-verdict-splits-deterministic-from-unreachable
  ;; 純関数 1 点: Written / Conflict = created・4xx(408 / 429 を除く)= given-up・0 / 5xx / 408 / 429 = 期限内は pending。
  (setv started 1000)
  (assert (= (run (record-create-verdict (Written "ev-1") started 2000 300 False)) RECORD-CREATE-CREATED))
  (assert (= (run (record-create-verdict (Conflict 3) started 2000 300 False)) RECORD-CREATE-CREATED))
  (for [status [400 403 404 413 422]]
    (assert (= (run (record-create-verdict (Refused status "no") started 2000 300 False)) RECORD-CREATE-GIVEN-UP) status))
  (for [status [0 500 502 503 504 408 429]]
    (assert (= (run (record-create-verdict (Refused status "later") started 2000 300 False)) RECORD-CREATE-PENDING) status)
    ;; 期限(started + 300 s)を越えたら given-up
    (assert (= (run (record-create-verdict (Refused status "later") started (+ started 300001) 300 False)) RECORD-CREATE-GIVEN-UP) status)
    ;; agora-redesign #537 H3: 手番の終わりの最後の 1 度(final)は期限の内でも pending にしない — 次の拍が無いので
    ;; pending のままだと条件が 1 つも乗らず『Ended・記録なし・理由なし』になる。
    (assert (= (run (record-create-verdict (Refused status "later") started 2000 300 True)) RECORD-CREATE-GIVEN-UP) status))
  ;; final でも作れた拍は created(final は断りの読み方だけを変える)。
  (assert (= (run (record-create-verdict (Written "ev-2") started 2000 300 True)) RECORD-CREATE-CREATED)))


(deftest test-turn-record-is-created-after-the-head-answers-again
  (setv world (World))
  ;; 作り直しの周期を 2 秒に(検の拍の都合)。期限は既定(300 s)。
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 2.0))
  ;; 頭が 2 度答えない(到達不能 → 503)— 3 度目で作れる。
  (setv (get world.acp.create-refusals (record-key)) [(Refused 0 "unreachable: reset") (Refused 503 "restarting")])
  (.tick world 0)
  (setv sid (.sid world))
  (assert (not-in (record-key) world.acp.rows) "受けた拍は作れていないはず")
  (setv job (in-flight world))
  (assert (= job.record-create RECORD-CREATE-PENDING))
  (assert (any (gfor line world.local.logs (in "record-create = pending" line))))
  ;; 拍 1(+1 s): 出来事は読むが、周期(2 s)の前なので create は撃たない → 持ち越し。
  (setv part-1 (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "m"}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") part-1)
  (.tick world 1000)
  (assert (= (get world.acp.creates (record-key)) 1) "周期の前に create を撃っている")
  (assert (not-in (record-key) world.acp.rows))
  (setv job (in-flight world))
  (assert (= (len job.pending-entries) 1) "持ち越していない")
  ;; 拍 2(+2 s): 周期 → create(2 度目・503)→ まだ pending。
  (.tick world 2000)
  (assert (= (get world.acp.creates (record-key)) 2))
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-PENDING))
  (assert (any (gfor line world.local.logs (in "still not created" line))))
  ;; 拍 3(+2 s): 周期 → create(3 度目・答える)→ 行が出来て、持ち越した出来事と今の拍の出来事が乗る。
  (setv part-2 (stream-line {"type" "assistant"
                             "message" {"id" "msg_1" "role" "assistant" "model" "m"
                                        "content" [{"type" "text" "text" "late"}]
                                        "usage" {"input_tokens" 1 "output_tokens" 1
                                                 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0}}}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (+ part-1 part-2))
  (.tick world 2000)
  (assert (= (get world.acp.creates (record-key)) 3))
  (assert (in (record-key) world.acp.rows) "3 度目で作れていない")
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-CREATED))
  (assert (any (gfor line world.local.logs (in "created after retry" line))))
  (assert (= (lfor entry (.record-entries world) (get entry "kind")) ["system" "text"]) "持ち越した出来事が乗っていない")
  ;; 以後は撃たない(created)。手番の終わりも作り直さない・condition は無い。
  (.tick world 1000)
  (assert (= (get world.acp.creates (record-key)) 3))
  (setv part-3 (stream-line {"type" "result" "subtype" "success" "is_error" False
                             "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0}}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (+ part-1 part-2 part-3))
  (.finish-turn world.sessions sid (+ world.local.now-ms 100))
  (.tick world 1000)
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (= (get world.acp.creates (record-key)) 3))
  (assert (not (any (gfor c (job-conditions world) (= (get c "type") "RecordUnavailable")))) "作れたのに condition"))


(deftest test-deterministic-refusal-gives-up-at-once-and-ends-with-a-condition
  (setv world (World))
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 1.0))
  ;; 400 = 契約の不備(撃ち直しても同じ)→ 受けた拍で given-up。
  (setv (get world.acp.create-refusals (record-key)) [(Refused 400 "kind turn-record is not declared")])
  (.tick world 0)
  (setv sid (.sid world))
  (setv job (in-flight world))
  (assert (= job.record-create RECORD-CREATE-GIVEN-UP))
  (assert (= (len job.pending-conditions) 1))
  (setv condition (get job.pending-conditions 0))
  (assert (= (get condition "type") "RecordUnavailable"))
  (setv reason (get condition "reason"))
  (assert (isinstance reason str))
  (assert (in "400: kind turn-record is not declared" reason))
  ;; 以後の拍も手番の終わりも create を撃たない(法 7: 決定論的な失敗は撃ち直さない)。
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1500)
  (.tick world 1500)
  (assert (= (get world.acp.creates (record-key)) 1))
  (.finish-turn world.sessions sid (+ world.local.now-ms 100))
  (.tick world 1500)
  (assert (= (get world.acp.creates (record-key)) 1))
  (assert (not-in (record-key) world.acp.rows))
  ;; Ended の conditions に理由つきで載る(記録なしで黙って終わらない)。
  (setv conditions (job-conditions world))
  (assert (= (lfor c conditions (get c "type")) ["RecordUnavailable"]) conditions)
  (assert (any (gfor line world.local.logs (in "is missing at turn end" line)))))


(deftest test-unreachable-head-past-the-deadline-gives-up-with-a-condition
  (setv world (World))
  ;; 周期 1 s・期限 3 s。頭はずっと答えない。
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 1.0
                                            :turn-record-create-deadline-seconds 3.0))
  (setv (get world.acp.create-refusals (record-key)) (lfor _ (range 20) (Refused 0 "unreachable: reset")))
  (.tick world 0)
  (setv sid (.sid world))
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-PENDING))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
        (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "m"}))
  ;; +1 s・+2 s: 期限の内 → 撃ち直して pending のまま。+3 s: 期限に達した拍の断りで given-up。
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-PENDING))
  (assert (= (get world.acp.creates (record-key)) 3))
  (.tick world 1000)
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-GIVEN-UP))
  (assert (= (get world.acp.creates (record-key)) 4))
  (assert (any (gfor line world.local.logs (in "given up" line))))
  ;; given-up の後は撃たない — 手番の終わりも(force でも腕が given-up なら触らない)。
  (.tick world 1000)
  (assert (= (get world.acp.creates (record-key)) 4))
  (.finish-turn world.sessions sid (+ world.local.now-ms 100))
  (.tick world 1000)
  (assert (= (get world.acp.creates (record-key)) 4))
  (setv conditions (job-conditions world))
  ;; この手番は本文を 1 つも出していない(init の system 行だけ)ので、7b81b077(L195・TurnProducedNothing)以降は出力 0 件の
  ;; 条件も並ぶ。**型の集合を固定する**(c3454a16 で「RecordUnavailable を列から拾って数える」形にしていたが、それでは
  ;; この手番に余計な条件が 1 つ増えても誰も気づかない — 検収 lt-EZJTXQMQQARCBP0Q1026TYBZ2A の指摘。在るべき集合を
  ;; 固定すれば、条件が増えた拍に赤になり、この検の追随が要ると分かる)。
  (assert (= (sfor c conditions (get c "type")) #{"RecordUnavailable" "TurnProducedNothing"}) conditions)
  (setv unavailable (lfor c conditions :if (= (get c "type") "RecordUnavailable") c))
  (assert (= (len unavailable) 1) conditions)
  (assert (in "after 3 s" (get (get unavailable 0) "reason")) conditions))


(deftest test-turn-end-retries-the-create-once-even-before-the-period
  (setv world (World))
  ;; 周期 60 s(拍の間に周期が来ない)— 手番の終わりは周期に依らず最後に 1 度作り直す。
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 60.0))
  (setv (get world.acp.create-refusals (record-key)) [(Refused 0 "unreachable: reset")])
  (.tick world 0)
  (setv sid (.sid world))
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-PENDING))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  (assert (= (get world.acp.creates (record-key)) 1) "周期の前に撃っている")
  (.finish-turn world.sessions sid (+ world.local.now-ms 100))
  (.tick world 1000)
  ;; 終わりの拍: force の create(2 度目・答える)→ 行が出来て、持ち越した出来事が終わりの書きに乗り ended。
  (assert (= (get world.acp.creates (record-key)) 2))
  (assert (in (record-key) world.acp.rows))
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (= (lfor entry (.record-entries world) (get entry "kind")) ["system" "text" "tool_use" "tool_result"]))
  (assert (not (any (gfor c (job-conditions world) (= (get c "type") "RecordUnavailable"))))))


;; ---------------------------------------------------------------------------
;; agora-redesign #537(段 12): 手番の記録を「1 度の書き」に預けない
;;   便 A = 終状態を読む巡回(running の取り残しを閉じる)/ 便 B = 記録なしで Ended にしない(穴 H1 / H2 / H3)
;; ---------------------------------------------------------------------------

(setv SWEEP-AHEAD-MS 301000)  ;; 巡回の周期(既定 300 s)を 1 拍で越える進み


(defn #^ str record-key-of [#^ str job-id]
  f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}")


(defn #^ str job-key-of [#^ str job-id]
  f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}")


(defn #^ AcpRow running-record-row [#^ str job-id #^ str node]
  "走っている手番の記録の行(巡回の相手)— 契約の spec(conversationId / agentJobId / node / profile / model / sessionId)。"
  (row-of AGORA-KINDS-NAMESPACE TURN-RECORD-KIND job-id
          {"conversationId" CONVERSATION "agentJobId" job-id "node" node
           "profile" "personal" "model" "claude-opus-5" "sessionId" f"sid-{job-id}"}
          {"state" "running"}))


(defn #^ AcpRow job-row-in-phase [#^ str job-id #^ str phase #^ str node]
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id
          {"subject" CONVERSATION "inputs" [] "charter" {}}
          {"phase" phase "binding" {"node" node "profile" "personal"} "conditions" []}))


(defn #^ list record-writes-of [#^ World world #^ str job-id]
  (lfor [key status] world.acp.writes :if (= key (record-key-of job-id)) status))


(deftest test-a-turn-record-left-running-by-a-refused-write-is-ended-by-the-sweep
  ;; agora-redesign #537 便 A(受入 1): 手番の終わりの 1 度の書きが断られた(頭の 5xx)記録は、その拍では running のまま
  ;; 残る(行は在るので作り直しもしない)。次の巡回の拍が終状態(対の agent-job が Ended)を読んで閉じる。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  ;; 終わりの拍の 1 度の書きを断る(engine の 5xx)。
  (setv (get world.acp.status-refusals (record-key-of "j-1")) [(Refused 503 "head restarting")])
  (.finish world.sessions sid "done" {"ok" True} None)
  (.tick world 1000)
  (assert (= (get (job-status world) "phase") PHASE-ENDED) (job-status world))
  (assert (= (get (.record-status world) "state") "running") "断られた書きで記録が閉じている(検体が弱い)")
  (assert (any (gfor line world.local.logs (in "could not be ended at turn end" line))) world.local.logs)
  (assert (= (.get world.acp.creates (record-key-of "j-1")) 1) "行は在るのに作り直した")
  (assert (= world.state.jobs #()) "手番は memory から外れている(巡回の相手になる)")
  ;; 周期の前の拍は撃たない。
  (.tick world 1000)
  (assert (= (get (.record-status world) "state") "running"))
  ;; 周期の拍: 終状態(対は Ended・node は自分)を読んで閉じる。
  (.tick world SWEEP-AHEAD-MS)
  (assert (= (get (.record-status world) "state") "ended") (.record-status world))
  (assert (not-in "usage" (.record-status world)) "巡回は usage を書かない(消費の和は手番の終わりの 1 回だけ)")
  (assert (any (gfor line world.local.logs (in "ended by the sweep" line))) world.local.logs)
  (setv ended (lfor m world.local.metrics :if (= (get m "metric") "agentd_turn_record_sweep_ended") m))
  (assert (= (lfor m ended (get m "agentJobId")) ["j-1"]) ended)
  ;; 冪等: 次の周期は 1 bit も書かない(ended の行は候補にならない)。
  (setv writes-before (len (record-writes-of world "j-1")))
  (.tick world SWEEP-AHEAD-MS)
  (assert (= (len (record-writes-of world "j-1")) writes-before) "ended の記録に二度書いた"))


(deftest test-the-sweep-closes-the-leftovers-of-a-restart-and-leaves-the-live-ones-alone
  ;; agora-redesign #537 便 A(受入 2 / 3 / 4): 再起動(memory が空)の直後の 1 拍で、終状態から読める取り残しだけを閉じる。
  ;;   閉じる = 対が Ended(j-2)・対の行ごと無い(j-3)・名乗る node が生きていない(j-6)
  ;;   触らない = 対が Pending / Bound / Running(j-4a / j-4b / j-4c)・生きている別の機体の手番(j-5)
  (setv world (World))
  ;; 生きている別の機体と、退役した機体の行。
  (.put-row world.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND "live-node"
                              {"name" "live-node" "labels" {} "capacity" 1 "streamCapability" "events"}
                              {"state" "joined"}))
  (.put-row world.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND "dead-node"
                              {"name" "dead-node" "labels" {} "capacity" 1 "streamCapability" "events"}
                              {"state" "gone"}))
  ;; 閉じる 3 本。
  (.put-row world.acp (running-record-row "j-2" NODE))
  (.put-row world.acp (job-row-in-phase "j-2" PHASE-ENDED NODE))
  (.put-row world.acp (running-record-row "j-3" NODE))          ;; 対の行ごと無い
  (.put-row world.acp (running-record-row "j-6" "dead-node"))
  (.put-row world.acp (job-row-in-phase "j-6" PHASE-ENDED "dead-node"))
  ;; 触らない 4 本(対が非終端 3 つ + 生きている別の機体 1 つ)。結びは自分でない — 受けの腕が拾わないように。
  (for [[job-id phase] [#("j-4a" PHASE-PENDING) #("j-4b" PHASE-BOUND) #("j-4c" PHASE-RUNNING)]]
    (.put-row world.acp (running-record-row job-id NODE))
    (.put-row world.acp (job-row-in-phase job-id phase "live-node")))
  (.put-row world.acp (running-record-row "j-5" "live-node"))
  (.put-row world.acp (job-row-in-phase "j-5" PHASE-ENDED "live-node"))
  ;; 起動の拍(AgentdState.last-turn-record-sweep-ms = None)で即撃つ。
  (.tick world 0)
  (assert (= world.acp.running-record-lists 1) world.acp.running-record-lists)
  (for [job-id ["j-2" "j-3" "j-6"]]
    (setv status (. (get world.acp.rows (record-key-of job-id)) status))
    (assert (isinstance status dict))
    (assert (= (get status "state") "ended") #(job-id status)))
  (for [job-id ["j-4a" "j-4b" "j-4c" "j-5"]]
    (setv status (. (get world.acp.rows (record-key-of job-id)) status))
    (assert (isinstance status dict))
    (assert (= (get status "state") "running") #(job-id status))
    (assert (= (record-writes-of world job-id) []) #(job-id (record-writes-of world job-id))))
  ;; この拍に claim した自分の手番(j-1)の記録は走っている = 触らない(memory に在るので読み直しもしない)。
  (assert (= (get (.record-status world) "state") "running"))
  (assert (= (record-writes-of world "j-1") []))
  (setv skipped (lfor m world.local.metrics :if (= (get m "metric") "agentd_turn_record_sweep_skipped") (get m "agentJobId")))
  (assert (= (sorted skipped) ["j-4a" "j-4b" "j-4c" "j-5"]) skipped)
  (assert (not-in "j-1" skipped) "memory に在る手番の記録を読み直した"))


(deftest test-turn-record-sweep-verdict-reads-the-end-state-of-the-pair-and-the-node
  ;; agora-redesign #537 便 A: 判断の 1 点(純関数)。end は 4 つが揃った時ちょうど。
  (setv mine (running-record-row "j-1" NODE))
  (setv ended-pair (job-row-in-phase "j-1" PHASE-ENDED NODE))
  (setv live (frozenset [NODE "live-node"]))
  (assert (= (run (turn-record-sweep-verdict mine ended-pair NODE live (set))) TURN-RECORD-SWEEP-END))
  ;; 1. 既に ended の記録は触らない(冪等)。
  (setv closed (dataclasses.replace mine :status {"state" "ended"}))
  (assert (= (run (turn-record-sweep-verdict closed ended-pair NODE live (set))) TURN-RECORD-SWEEP-SKIP))
  ;; 2. 自分が走らせている手番(memory)は触らない。
  (assert (= (run (turn-record-sweep-verdict mine ended-pair NODE live #{"j-1"})) TURN-RECORD-SWEEP-SKIP))
  ;; 3. 対が非終端(置き直し待ちの Running を含む)は触らない・終端(Ended / Withdrawn)と不在だけ閉じる。
  (for [phase [PHASE-PENDING PHASE-BOUND PHASE-RUNNING]]
    (assert (= (run (turn-record-sweep-verdict mine (job-row-in-phase "j-1" phase NODE) NODE live (set)))
               TURN-RECORD-SWEEP-SKIP) phase))
  (assert (= (run (turn-record-sweep-verdict mine (job-row-in-phase "j-1" PHASE-WITHDRAWN NODE) NODE live (set)))
             TURN-RECORD-SWEEP-END))
  (assert (= (run (turn-record-sweep-verdict mine None NODE live (set))) TURN-RECORD-SWEEP-END) "対の行ごと無い")
  ;; 4. 生きている別の機体の手番は持ち主に任せる / 生きていない機体の手番は誰でも閉じる。
  (setv elsewhere (running-record-row "j-9" "live-node"))
  (assert (= (run (turn-record-sweep-verdict elsewhere (job-row-in-phase "j-9" PHASE-ENDED "live-node") NODE live (set)))
             TURN-RECORD-SWEEP-SKIP))
  (setv orphan (running-record-row "j-9" "pool-pod-7"))
  (assert (= (run (turn-record-sweep-verdict orphan (job-row-in-phase "j-9" PHASE-ENDED "pool-pod-7") NODE live (set)))
             TURN-RECORD-SWEEP-END))
  ;; 生きている node の名の集合は status.state == joined の行だけ(綴りは node-row-entry-of の 1 点)。
  (setv joined (row-of AGORA-KINDS-NAMESPACE NODE-KIND "a" {"name" "a"} {"state" "joined"}))
  (setv gone (row-of AGORA-KINDS-NAMESPACE NODE-KIND "b" {"name" "b"} {"state" "gone"}))
  (setv nameless (row-of AGORA-KINDS-NAMESPACE NODE-KIND "c" {"labels" {}} {"state" "joined"}))
  (assert (= (run (live-node-names-of #(joined gone nameless))) (frozenset ["a" ""]))))


(deftest test-a-recovered-turn-without-a-record-row-re-creates-it-before-the-end
  ;; agora-redesign #537 便 B(受入 5・穴 H1): 再起動で拾い直した手番の turn-record が 404 なら、記録の腕を pending に戻して
  ;; 段 9p の網で作り直す — 「Ended・記録なし・条件なし」(郵便が agent-job-ended-without-a-turn で failed になる形)にしない。
  (setv world (World))
  ;; 作り直しの周期を 1 秒に(本番は now-ms が epoch なので record-create-last-ms = 0 は常に「周期を過ぎている」= 次の拍。
  ;; 検体の時計は 1000 ms から始まるので、同じ形を短い周期で撃つ)。
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 1.0))
  (.tick world 0)
  (setv sid (.sid world))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  ;; 再起動の再現: memory を捨て、記録の行も消す(GC / 別の incarnation が作れていない)。
  (.delete-row world.acp (record-key-of "j-1"))
  (setv world.state (initial-state))
  (setv creates-before (.get world.acp.creates (record-key-of "j-1") 0))
  (.tick world 1000)
  ;; 拾い直しの拍で腕を pending に戻し、同じ拍の観測(stream-job → ensure-turn-record)が行を作り直す
  ;; (H1 が無いと腕は created のままで、行が無いことに誰も気づかず手番の終わりまで進む)。
  (assert (any (gfor line world.local.logs (in "has no turn-record row; record-create = pending" line))) world.local.logs)
  (assert (in (record-key-of "j-1") world.acp.rows) "拾い直した手番の記録が作り直されていない")
  (assert (> (.get world.acp.creates (record-key-of "j-1")) creates-before))
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-CREATED))
  ;; 手番の終わり: 記録は Ended の**前**に在る(同じ拍の書きの順 — 記録 → agent-job)。
  (.finish world.sessions sid "done" {"ok" True} None)
  (.tick world 1000)
  (assert (= (get (.record-status world) "state") "ended") (.record-status world))
  (setv keys (lfor [key _] world.acp.writes key))
  (setv record-at (.index keys (record-key-of "j-1")))
  (setv job-at (- (len keys) 1 (.index (list (reversed keys)) (job-key-of "j-1"))))
  (assert (< record-at job-at) keys)
  (assert (= (get (job-status world) "phase") PHASE-ENDED))
  (assert (not (any (gfor c (job-conditions world) (= (get c "type") "RecordUnavailable")))) (job-conditions world)))


(deftest test-a-turn-that-ends-inside-the-deadline-still-names-the-missing-record
  ;; agora-redesign #537 便 B(受入 5・穴 H3): 短い手番(20 秒)が頭の答えない拍に当たると、期限(300 s)の内で終わるので
  ;; 記録の腕は pending のまま = 条件が 1 つも乗らない「Ended・記録なし・理由なし」だった。手番の終わりの最後の 1 度
  ;; (force)は期限に依らず given-up に倒す — 郵便の側が「本当に始まらなかった」と区別できる。
  (setv world (World))
  (setv world.settings (dataclasses.replace world.settings :record-retry-seconds 1.0))
  (setv (get world.acp.create-refusals (record-key-of "j-1")) (lfor _ (range 20) (Refused 0 "unreachable: reset")))
  (.tick world 0)
  (setv sid (.sid world))
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-PENDING))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  ;; 期限(既定 300 s)の遥か内側で手番が終わる。
  (.finish world.sessions sid "done" {"ok" True} None)
  (.tick world 1000)
  (assert (not-in (record-key-of "j-1") world.acp.rows))
  (assert (= (get (job-status world) "phase") PHASE-ENDED) (job-status world))
  (setv conditions (job-conditions world))
  (assert (= (lfor c conditions (get c "type")) ["RecordUnavailable"]) conditions)
  (assert (any (gfor line world.local.logs (in "given up" line))) world.local.logs)
  ;; 記録が 1 行も無いので巡回の相手にもならない(黙って消えない — 理由は行の条件が運ぶ)。
  (.tick world SWEEP-AHEAD-MS)
  (assert (not-in (record-key-of "j-1") world.acp.rows)))


(deftest test-a-turn-record-that-vanished-before-the-end-is-re-created-and-ended
  ;; agora-redesign #537 便 B(穴 H2): 「作れている」はずの行が終わりの拍に無い(GC・消えた)なら、その拍に 1 度だけ
  ;; 作り直して ended まで書く。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") (claude-events sid "hello"))
  (.tick world 1000)
  (assert (= (. (in-flight world) record-create) RECORD-CREATE-CREATED))
  (setv creates-before (.get world.acp.creates (record-key-of "j-1")))
  (.delete-row world.acp (record-key-of "j-1"))
  (.finish world.sessions sid "done" {"ok" True} None)
  (.tick world 1000)
  (assert (= (.get world.acp.creates (record-key-of "j-1")) (+ creates-before 1)) "終わりの拍に作り直していない")
  (assert (= (get (.record-status world) "state") "ended") (.record-status world))
  (assert (any (gfor line world.local.logs (in "was missing at turn end; re-created" line))) world.local.logs)
  (assert (= (get (job-status world) "phase") PHASE-ENDED)))
