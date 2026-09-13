;;; 手番の出来事(turn-record の status.entries)の耐久化の焦点の検(段 8 lane 4u・agora-redesign #49・
;;; ADR-DOE-AGENTS-012 R19)。
;;;
;;; 既知の形 = event-sourced の出来事の列(append-only・durable): runner(agentd)が出来事の書き手、
;;; control plane(ACP の行)が正本、画面は read model。ここで撃つのは
;;;   * 出来事 → entry の純関数(text / tool_use / tool_result / system / error・toolUseId・上限と truncated)
;;;   * 行の上限(byte)の切り詰め: 古い出来事から落とし、先頭に印(kind system・dropped)を残す・印を引き継ぐ
;;;   * 追記の post-image(行の entries + 新しい出来事)と手番の終わりの書き(追記の上に ended・usage)
;;;   * 採番の衝突(拾い直した job の seq 0)は行の次から振り直す
;;;   * fake の handler で agentd を一周: 出来事は手番の**途中**で行に在る・Conflict は読み直して積み直す・
;;;     断られた出来事は持ち越して手番の終わりに乗る
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  ENTRY-SUMMARY-MAX-CHARS
  ENTRY-TEXT-MAX-CHARS
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  TURN-RECORD-ENTRIES-BYTE-BUDGET
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  claude-result-error
  claude-system-note
  deltas-of
  entries-within-budget
  is-drop-marker
  next-seq-after
  renumbered-entries
  text-entry
  tool-result-entry
  tool-use-entry
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

(deftest test-entries-carry-tool-use-id-and-truncate-at-the-declared-limits
  ;; tool_use: toolName・toolUseId・summary(入力の JSON)— 上限で切れば truncated。
  (setv use (run (tool-use-entry 3 AT "t1" "Bash" {"command" "ls"})))
  (assert (= use {"seq" 3 "at" AT "kind" "tool_use" "toolName" "Bash" "toolUseId" "t1"
                  "summary" "{\"command\": \"ls\"}"}))
  (setv long-input {"command" (* "x" (+ ENTRY-SUMMARY-MAX-CHARS 10))})
  (setv clipped-use (run (tool-use-entry 4 AT "t2" "Bash" long-input)))
  (assert (= (len (get clipped-use "summary")) ENTRY-SUMMARY-MAX-CHARS))
  (assert (is (get clipped-use "truncated") True))
  ;; tool_result: toolUseId・summary(出力)・isError は誤りの時だけ名乗る。
  (setv result (run (tool-result-entry 5 AT "t1" "a\nb" False)))
  (assert (= result {"seq" 5 "at" AT "kind" "tool_result" "toolUseId" "t1" "summary" "a\nb"}))
  (setv failed (run (tool-result-entry 6 AT "t1" [{"type" "text" "text" "boom"}] True)))
  (assert (is (get failed "isError") True))
  (assert (in "boom" (get failed "summary")))
  ;; text: model つき・上限で切れば truncated(切らなければ欄が無い — 発明しない)。
  (setv text (run (text-entry 7 AT "hello" "claude-opus-5")))
  (assert (= text {"seq" 7 "at" AT "kind" "text" "text" "hello" "model" "claude-opus-5"}))
  (setv long-text (run (text-entry 8 AT (* "y" (+ ENTRY-TEXT-MAX-CHARS 1)) None)))
  (assert (= (len (get long-text "text")) ENTRY-TEXT-MAX-CHARS))
  (assert (is (get long-text "truncated") True))
  (assert (not (in "model" long-text))))


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
  (setv batch (run (deltas-of "claude" "events" text "job" 0 AT)))
  (assert (= (lfor entry batch.entries (get entry "kind")) ["system" "error"]))
  (assert (= (lfor entry batch.entries (get entry "seq")) [0 1]))
  (assert (= (get (get batch.entries 1) "text") "boom"))
  (assert (= batch.frames #()))
  ;; transcript(tui)の行は system / result を読まない(従来どおり)。
  (setv quiet (run (deltas-of "claude" "transcript" text "job" 0 AT)))
  (assert (= quiet.entries #())))


;; ---------------------------------------------------------------------------
;; 行の上限の切り詰めと追記の post-image
;; ---------------------------------------------------------------------------

(defn #^ dict big-entry [#^ int seq #^ int size]
  {"seq" seq "at" (+ AT seq) "kind" "text" "text" (* "z" size)})


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
  ;; 追記の post-image: 行の entries + 新しい出来事。他の欄(state・usage)は写す。
  (setv status {"state" "running" "entries" [{"seq" 0 "at" AT "kind" "text" "text" "a"}]})
  (setv appended (run (turn-record-appended-status status #({"seq" 1 "at" AT "kind" "text" "text" "b"}))))
  (assert (= (get appended "state") "running"))
  (assert (= (lfor entry (get appended "entries") (get entry "text")) ["a" "b"]))
  (assert (= (get status "entries") [{"seq" 0 "at" AT "kind" "text" "text" "a"}]))
  ;; 手番の終わり: 残りを追記した上で ended・usage。行の entries は落とさない(旧の形 = 置換 を作らない)。
  (setv ended (run (turn-record-ended-status appended {"input" 1 "output" 2 "cacheWrite" 0 "cacheRead" 0}
                                             #({"seq" 2 "at" AT "kind" "error" "text" "boom"}))))
  (assert (= (get ended "state") "ended"))
  (assert (= (lfor entry (get ended "entries") (get entry "kind")) ["text" "text" "error"]))
  (assert (= (get ended "usage") {"input" 1 "output" 2 "cacheWrite" 0 "cacheRead" 0}))
  ;; 残りが無い終わりは entries をそのまま(空で置換しない)。
  (setv quiet (run (turn-record-ended-status ended None #())))
  (assert (= (len (get quiet "entries")) 3))
  ;; 追記は行の上限を守る(TURN-RECORD-ENTRIES-BYTE-BUDGET)。
  (setv flood (tuple (lfor seq (range 3) (big-entry seq (// TURN-RECORD-ENTRIES-BYTE-BUDGET 2)))))
  (setv bounded (run (turn-record-appended-status {"state" "running"} flood)))
  (assert (run (is-drop-marker (get (get bounded "entries") 0))))
  (assert (= (get (get (get bounded "entries") -1) "seq") 2)))


(deftest test-recovered-jobs-renumber-past-the-row-sequence
  ;; 拾い直した job は seq 0 から数え直す — 行の seq と衝突すれば行の次から振り直す。
  (setv existing #({"seq" 4 "at" AT "kind" "text" "text" "a"} {"seq" 9 "at" AT "kind" "text" "text" "b"}))
  (assert (= (run (next-seq-after existing 0)) 10))
  (assert (= (run (next-seq-after #() 3)) 3))
  (assert (= (run (next-seq-after existing 20)) 20))
  (setv fresh #({"seq" 0 "at" AT "kind" "text" "text" "c"} {"seq" 1 "at" AT "kind" "tool_use" "toolName" "Bash"}))
  (setv renumbered (run (renumbered-entries fresh 10)))
  (assert (= (lfor entry renumbered (get entry "seq")) [10 11]))
  (assert (= (get (get renumbered 1) "toolName") "Bash"))
  ;; 衝突しなければそのまま(同じ object)。
  (setv clear #({"seq" 12 "at" AT "kind" "text" "text" "d"}))
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
