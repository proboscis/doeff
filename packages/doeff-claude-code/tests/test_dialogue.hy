;; 状態機械(dialogue.hy)の検 — 純関数だけ。行は実物の形(claude 2.1.282 の実測 = #602 の layer2-cli-capabilities.md)。
;; 状態機械は lines.hy が分類した型を読む — 検は実物の形の行を classify-record に通してから渡す(本番の handler と同じ道)。
(require doeff-hy.macros [deftest val])
(import json)
(import doeff_claude_code.values [TurnInput ImageAttachment Allow Deny])
(import doeff_claude_code.lines [Completed Failed Interrupted BackendLost Usage classify-record])
(import doeff_claude_code.dialogue :as dialogue)
(import doeff_claude_code.dialogue [DialogueState StopSignal StopControl NoStop])

(setv SID "560828de-2992-4635-ab21-c6e06b0c6eb8")
(setv INIT {"type" "system" "subtype" "init" "session_id" SID
            "capabilities" ["interrupt_receipt_v1" "interrupt_cancel_queued_v1" "msg_lifecycle_v1"]})
;; 実測の SIGINT の result(2.1.282・#602 の claude3.jsonl の 20 行目から欄を抜いた — 値は逐語)。
(setv SIGINT-RESULT {"type" "result" "subtype" "error_during_execution" "is_error" True
                     "terminal_reason" "aborted_streaming" "num_turns" 3 "session_id" SID
                     "errors" ["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use"]
                     "user_message_uuid" "msg-sigint"})
(setv SUCCESS-RESULT {"type" "result" "subtype" "success" "is_error" False "result" "OKAPI-77"
                      "terminal_reason" "completed" "total_cost_usd" 0.04 "usage" {"output_tokens" 7}
                      "user_message_uuids" ["msg-1"] "session_id" SID})
;; 実測の額と usage(claude 2.1.283・model claude-haiku-4-5・2026-09-28・#883 — 値は逐語、欄は抜いた)。
;; 1 つの process に stream-json で user の発話を 2 回(FIRST・SECOND)、別の process で --resume して 1 回(RESUMED)。
;; total_cost_usd は会話の累積(SECOND = FIRST + 2 回目の分・RESUMED = SECOND + 3 回目の分 — 前の process の額から数え続ける)、
;; usage はその回の分だけ(単価の表で確かめた: 2 回目の分 = 0.035648 − 0.0322929 = 0.0033551 が SECOND の usage の額ちょうど)。
(val MEASURED-FIRST {"type" "result" "subtype" "success" "is_error" False "result" "1" "total_cost_usd" 0.0322929
                     "usage" {"input_tokens" 10 "cache_creation_input_tokens" 15322 "cache_read_input_tokens" 13689
                              "output_tokens" 54}})
(val MEASURED-SECOND {"type" "result" "subtype" "success" "is_error" False "result" "2" "total_cost_usd" 0.035648
                      "usage" {"input_tokens" 10 "cache_creation_input_tokens" 102 "cache_read_input_tokens" 29011
                               "output_tokens" 48}})
(val MEASURED-RESUMED {"type" "result" "subtype" "success" "is_error" False "result" "3"
                       "total_cost_usd" 0.038911299999999996
                       "usage" {"input_tokens" 10 "cache_creation_input_tokens" 96 "cache_read_input_tokens" 29113
                                "output_tokens" 30}})


(defn read-record [state #^ dict record]
  "実物の形の 1 行を分類して状態機械へ渡す(handler の on-line と同じ道)。"
  (dialogue.on-record state (classify-record record)))

(defn started [#^ (| float None) [cost-mark 0.0]]
  "init を読んだ後の手番の途中の状態。cost-mark = 手番の額の起点(既定は新しい会話の 0 — None は分からない起点)。"
  (setv begun (dialogue.begin-turn (DialogueState :cost-mark cost-mark :start-mark cost-mark) (TurnInput "hello" "msg-1")))
  (. (read-record begun.state INIT) state))

(defn lifecycle [ref state]
  {"type" "command_lifecycle" "command_uuid" ref "state" state "session_id" SID})


(deftest test-the-sigint-result-after-an-interrupt-is-interrupted-not-a-failure
  ;; #603 の直し: 2.1.282 の SIGINT は error_during_execution / aborted_streaming の result を出してから降りる。
  (setv plan (dialogue.interrupt (started) "rid-1"))
  (assert plan.signal)
  (assert (= plan.sends #()))
  (assert (isinstance plan.state.stop StopSignal))
  (setv read (read-record plan.state SIGINT-RESULT))
  (assert (= read.end (Interrupted)) (repr read.end))
  (assert read.close)
  (assert (not read.state.in-flight)))


(deftest test-an-aborted-streaming-result-without-an-interrupt-stays-a-failure
  ;; 止めるを求めていない aborted_streaming(誰かが外から SIGINT を送った)は Failed のまま、terminal_reason を運ぶ。
  (setv read (read-record (started) SIGINT-RESULT))
  (assert (isinstance read.end Failed) (repr read.end))
  (assert (= read.end.terminal-reason "aborted_streaming"))
  (assert (= read.end.detail "error_during_execution")))


(deftest test-a-success-result-completes-the-turn-and-closes-the-process
  (setv read (read-record (started) SUCCESS-RESULT))
  (assert (= read.end (Completed :result-text "OKAPI-77" :usage (Usage :output-tokens 7) :cost-usd 0.04 :input-refs #("msg-1"))))
  (assert read.close))


(deftest test-a-failed-result-carries-the-cli-text-and-the-api-status
  (setv limit {"type" "result" "subtype" "success" "is_error" True "result" "You've reached your limit."
               "api_error_status" 429 "terminal_reason" "api_error"})
  (setv read (read-record (started) limit))
  (assert (= read.end (Failed :detail "You've reached your limit." :api-error-status 429 :terminal-reason "api_error"
                              :input-refs #("msg-1")))))


(deftest test-a-failed-result-carries-the-tokens-it-used
  ;; 誤りで終えた手番も、result の行が名乗った usage を運ぶ(max_turns の打ち切りなどで消費した token を捨てない)。
  (val capped {"type" "result" "subtype" "error_max_turns" "is_error" True "result" ""
               "usage" {"input_tokens" 5 "output_tokens" 6 "cache_read_input_tokens" 7}})
  (val read (read-record (started) capped))
  (assert (= read.end.usage (Usage :input-tokens 5 :output-tokens 6 :cache-read-input-tokens 7)) read.end))


;; --- 手番の額(#883) ------------------------------------------------------------------

(deftest test-a-failed-result-carries-its-cost
  ;; 誤りで終えた手番も額を運ぶ: 累積の額 − 手番の起点(0.75 − 0.25)。
  (val capped {"type" "result" "subtype" "error_max_turns" "is_error" True "result" "" "total_cost_usd" 0.75
               "usage" {"output_tokens" 6}})
  (val read (read-record (started 0.25) capped))
  (assert (isinstance read.end Failed) (repr read.end))
  (assert (= read.end.cost-usd 0.5) (repr read.end))
  (assert (= read.end.usage (Usage :output-tokens 6)) (repr read.end))
  ;; result の行が額を名乗らなければ None(0 を発明しない)。
  (val silent (read-record (started 0.25) {"type" "result" "subtype" "error_during_execution" "is_error" True}))
  (assert (is silent.end.cost-usd None) (repr silent.end)))


(deftest test-a-fresh-session-costs-its-whole-total
  ;; 新しい会話の起点は 0: 最初の手番の額は result の行の累積ちょうど。手番を閉じた行の累積が次の起点になる。
  (val read (read-record (started 0.0) MEASURED-FIRST))
  (assert (= read.end.cost-usd 0.0322929) (repr read.end))
  (assert (= read.end.usage (Usage :input-tokens 10 :output-tokens 54 :cache-creation-input-tokens 15322
                                   :cache-read-input-tokens 13689))
          (repr read.end))
  (assert (= read.state.cost-mark 0.0322929)))


(deftest test-a-resumed-turn-costs-the-total-minus-the-mark
  ;; --resume の process の最初の result の行は前の process の額から数えた累積(実測)。手番の額はその差で、累積そのものではない。
  (val read (read-record (started 0.035648) MEASURED-RESUMED))
  (assert (isinstance read.end Completed) (repr read.end))
  (assert (< (abs (- read.end.cost-usd 0.0032633)) 1e-12) (repr read.end))
  (assert (= read.end.usage (Usage :input-tokens 10 :output-tokens 30 :cache-creation-input-tokens 96
                                   :cache-read-input-tokens 29113))
          (repr read.end)))


(deftest test-an-unknown-mark-gives-no-cost-and-the-next-turn-counts-again
  ;; 起点が分からない(handler の知らない会話の続き)手番の額は None。usage は運ぶ。閉じた行の累積から次の手番は数え直す。
  (val first (read-record (started None) MEASURED-FIRST))
  (assert (is first.end.cost-usd None) (repr first.end))
  (assert (= first.end.usage.output-tokens 54) (repr first.end))
  (val again (dialogue.begin-turn first.state (TurnInput "2 とだけ答えて" "msg-2")))
  (val second (read-record (. (read-record again.state INIT) state) MEASURED-SECOND))
  (assert (< (abs (- second.end.cost-usd 0.0033551)) 1e-12) (repr second.end))
  (assert (= second.end.usage.output-tokens 48) (repr second.end)))


(deftest test-a-total-below-the-mark-gives-no-cost
  ;; 累積が起点より小さい = CLI が起点の額から数えていない(起点の読み違い)。負の額を作らず None。
  (val read (read-record (started 0.5) SUCCESS-RESULT))
  (assert (isinstance read.end Completed) (repr read.end))
  (assert (is read.end.cost-usd None) (repr read.end)))


(deftest test-a-swallowed-result-and-the-next-result-are-one-turn
  ;; 注入を待って飲んだ result の行と、注入を走らせた CLI の手番の result の行は 1 つの host の手番: 額は最後の行の累積 − 起点、
  ;; usage は 2 行の和(額と同じ行の集まり)。
  (val injected (dialogue.inject (started 0.0) (TurnInput "late" "inj-1")))
  (val swallowed (read-record injected.state MEASURED-FIRST))
  (assert (is swallowed.end None))
  (val running (. (read-record swallowed.state (lifecycle "inj-1" "started")) state))
  (val done (read-record running MEASURED-SECOND))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (= done.end.cost-usd 0.035648) (repr done.end))
  (assert (= done.end.usage (Usage :input-tokens 20 :output-tokens 102 :cache-creation-input-tokens 15424
                                   :cache-read-input-tokens 42700))
          (repr done.end))
  (assert (= done.state.turn-usage (Usage)))
  (assert (= done.state.cost-mark 0.035648)))


(deftest test-a-cli-own-result-counts-in-the-turn
  ;; 手番の途中に CLI が自分で起こした手番の result の行は終わりではないが、その額は累積に入るので usage も手番に足す。
  (val own {"type" "result" "subtype" "success" "is_error" False "result" "" "origin" {"kind" "task-notification"}
            "total_cost_usd" 0.25 "usage" {"output_tokens" 3}})
  (val noted (read-record (started 0.0) own))
  (assert (is noted.end None))
  (val done (read-record noted.state (| SUCCESS-RESULT {"total_cost_usd" 0.75})))
  (assert (= done.end.cost-usd 0.75) (repr done.end))
  (assert (= done.end.usage (Usage :output-tokens 10)) (repr done.end)))


(deftest test-an-interrupted-turn-moves-the-mark-so-the-next-turn-costs-only-itself
  ;; 止めた手番(Interrupted — 額の欄が無い)を閉じた result の行の累積が次の手番の起点: 生き残った入力の手番の額と usage は
  ;; その手番の分だけ(止めた手番の分を混ぜない)。
  (val injected (dialogue.inject (started 0.0) (TurnInput "also this" "inj-1")))
  (val plan (dialogue.interrupt injected.state "rid-9"))
  (val answered (read-record plan.state {"type" "control_response"
                                         "response" {"subtype" "success" "request_id" "rid-9"
                                                     "response" {"still_queued" ["inj-1"]}}}))
  (val aborted (read-record answered.state {"type" "result" "subtype" "error_during_execution" "is_error" True
                                            "terminal_reason" "aborted_tools" "total_cost_usd" 0.25
                                            "usage" {"output_tokens" 5}}))
  (assert aborted.continues)
  (assert (= aborted.state.cost-mark 0.25))
  (assert (= aborted.state.turn-usage (Usage)))
  (val running (. (read-record aborted.state (lifecycle "inj-1" "started")) state))
  (val done (read-record running (| SUCCESS-RESULT {"user_message_uuids" ["inj-1"] "total_cost_usd" 0.75})))
  (assert (= done.end.cost-usd 0.5) (repr done.end))
  (assert (= done.end.usage (Usage :output-tokens 7)) (repr done.end)))


(deftest test-an-interrupt-with-unread-input-uses-control-request-and-continues
  ;; 読まれていない注入が在り interrupt_receipt_v1 を名乗った CLI: control_request → 答えの still_queued の注入が生き残り、
  ;; 手番は Interrupted で終わって同じ process の次の手番(continues)へ。次の result で本当に閉じる。
  (setv injected (dialogue.inject (started) (TurnInput "also this" "inj-1")))
  (assert (= (dialogue.queued-refs injected.state) #("inj-1")))
  (setv plan (dialogue.interrupt injected.state "rid-2"))
  (assert (not plan.signal))
  (assert (= plan.sends #((dialogue.interrupt-request-line "rid-2"))))
  (setv answered (read-record plan.state {"type" "control_response"
                                                 "response" {"subtype" "success" "request_id" "rid-2"
                                                             "response" {"still_queued" ["inj-1"]}}}))
  (assert (= answered.state.stop (StopControl "rid-2" #("inj-1"))))
  (setv aborted (read-record answered.state {"type" "result" "subtype" "error_during_execution" "is_error" True
                                                    "terminal_reason" "aborted_tools"}))
  (assert (= aborted.end (Interrupted :surviving-refs #("inj-1"))) (repr aborted.end))
  (assert aborted.continues)
  (assert (not aborted.close))
  (assert aborted.state.in-flight)
  (setv running (. (read-record aborted.state (lifecycle "inj-1" "started")) state))
  (setv finished (read-record running (| SUCCESS-RESULT {"user_message_uuids" ["inj-1"]})))
  (assert (= finished.end.input-refs #("inj-1")))
  (assert finished.close))


(deftest test-a-refused-control-request-falls-back-to-sigint
  (setv plan (dialogue.interrupt (. (dialogue.inject (started) (TurnInput "x" "inj-1")) state) "rid-3"))
  (setv refused (read-record plan.state {"type" "control_response"
                                                "response" {"subtype" "error" "request_id" "rid-3"}}))
  (assert refused.signal)
  (assert (isinstance refused.state.stop StopSignal)))


(deftest test-the-cli-own-turn-result-is-not-the-end
  ;; ResumeSession の直後に CLI が孤児の task の報せを自分の手番として走らせた result(origin task-notification)。
  (setv read (read-record (started) {"type" "result" "subtype" "success" "is_error" False "result" ""
                                            "origin" {"kind" "task-notification"}}))
  (assert (is read.end))
  (assert read.state.in-flight))


(deftest test-a-result-with-unread-input-is-swallowed-until-the-input-is-settled
  ;; result の時点で読まれていない注入 → CLI が次の手番として走らせる(中間の result)。その注入が走らずに終われば、
  ;; 飲んだ result で手番を閉じる。
  (setv injected (dialogue.inject (started) (TurnInput "late" "inj-1")))
  (setv swallowed (read-record injected.state SUCCESS-RESULT))
  (assert (is swallowed.end))
  (assert (= swallowed.state.deferred-result (classify-record SUCCESS-RESULT)))
  (setv cancelled (read-record swallowed.state (lifecycle "inj-1" "cancelled")))
  (assert (isinstance cancelled.end Completed) (repr cancelled.end))
  (assert (= cancelled.end.result-text "OKAPI-77")))


(deftest test-a-process-that-exits-mid-turn-is-backend-lost-unless-stopped
  (setv lost (dialogue.on-exit (started) -9 "killed"))
  (assert (= lost.end (BackendLost "process exited with code -9 before the turn ended: killed")))
  (setv stopped (dialogue.on-exit (. (dialogue.interrupt (started) "rid") state) 0 ""))
  (assert (= stopped.end (Interrupted)))
  (setv idle (dialogue.on-exit (DialogueState) 0 ""))
  (assert (is idle.end)))


(deftest test-a-permission-question-is-answered-once
  (setv asked (read-record (started) {"type" "control_request" "request_id" "req-1"
                               "request" {"subtype" "can_use_tool" "tool_name" "Bash" "input" {"command" "touch x"}}}))
  (assert (= (len asked.state.permissions) 1))
  (setv allowed (dialogue.answer-permission asked.state "req-1" (Allow)))
  (setv line (json.loads (get allowed.sends 0)))
  (assert (= line {"type" "control_response"
                   "response" {"subtype" "success" "request_id" "req-1"
                               "response" {"behavior" "allow" "updatedInput" {"command" "touch x"}}}}))
  (assert (is (dialogue.answer-permission allowed.state "req-1" (Allow))))
  (setv denied (dialogue.answer-permission asked.state "req-1" (Deny "no")))
  (assert (= (get (json.loads (get denied.sends 0)) "response" "response") {"behavior" "deny" "message" "no"})))


(deftest test-input-outside-a-turn-is-not-written
  (assert (is (dialogue.inject (DialogueState) (TurnInput "x" "r"))))
  (assert (is (dialogue.interrupt (DialogueState) "rid"))))


(deftest test-the-user-line-spelling
  (assert (= (json.loads (dialogue.user-line (TurnInput "hi" "r1")))
             {"type" "user" "message" {"role" "user" "content" "hi"} "uuid" "r1"}))
  (assert (= (get (json.loads (dialogue.user-line (TurnInput "look" "r2" #((ImageAttachment "image/png" "AAAA")))))
                  "message" "content")
             [{"type" "text" "text" "look"}
              {"type" "image" "source" {"type" "base64" "media_type" "image/png" "data" "AAAA"}}])))
