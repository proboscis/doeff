;; 状態機械(dialogue.hy)の検 — 純関数だけ。行は実物の形(claude 2.1.282 の実測 = #602 の layer2-cli-capabilities.md)。
(require doeff-hy.macros [deftest])
(import json)
(import doeff_claude_code.values [TurnInput ImageAttachment Allow Deny])
(import doeff_claude_code.lines [Completed Failed Interrupted BackendLost PermissionRequested])
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


(defn started []
  "init を読んだ後の手番の途中の状態。"
  (setv begun (dialogue.begin-turn (DialogueState) (TurnInput "hello" "msg-1")))
  (. (dialogue.on-record begun.state INIT None) state))

(defn lifecycle [ref state]
  {"type" "command_lifecycle" "command_uuid" ref "state" state "session_id" SID})


(deftest test-the-sigint-result-after-an-interrupt-is-interrupted-not-a-failure
  ;; #603 の直し: 2.1.282 の SIGINT は error_during_execution / aborted_streaming の result を出してから降りる。
  (setv plan (dialogue.interrupt (started) "rid-1"))
  (assert plan.signal)
  (assert (= plan.sends #()))
  (assert (isinstance plan.state.stop StopSignal))
  (setv read (dialogue.on-record plan.state SIGINT-RESULT None))
  (assert (= read.end (Interrupted)) (repr read.end))
  (assert read.close)
  (assert (not read.state.in-flight)))


(deftest test-an-aborted-streaming-result-without-an-interrupt-stays-a-failure
  ;; 止めるを求めていない aborted_streaming(誰かが外から SIGINT を送った)は Failed のまま、terminal_reason を運ぶ。
  (setv read (dialogue.on-record (started) SIGINT-RESULT None))
  (assert (isinstance read.end Failed) (repr read.end))
  (assert (= read.end.terminal-reason "aborted_streaming"))
  (assert (= read.end.detail "error_during_execution")))


(deftest test-a-success-result-completes-the-turn-and-closes-the-process
  (setv read (dialogue.on-record (started) SUCCESS-RESULT None))
  (assert (= read.end (Completed :result-text "OKAPI-77" :usage {"output_tokens" 7} :cost-usd 0.04 :input-refs #("msg-1"))))
  (assert read.close))


(deftest test-a-failed-result-carries-the-cli-text-and-the-api-status
  (setv limit {"type" "result" "subtype" "success" "is_error" True "result" "You've reached your limit."
               "api_error_status" 429 "terminal_reason" "api_error"})
  (setv read (dialogue.on-record (started) limit None))
  (assert (= read.end (Failed :detail "You've reached your limit." :api-error-status 429 :terminal-reason "api_error"
                              :input-refs #("msg-1")))))


(deftest test-an-interrupt-with-unread-input-uses-control-request-and-continues
  ;; 読まれていない注入が在り interrupt_receipt_v1 を名乗った CLI: control_request → 答えの still_queued の注入が生き残り、
  ;; 手番は Interrupted で終わって同じ process の次の手番(continues)へ。次の result で本当に閉じる。
  (setv injected (dialogue.inject (started) (TurnInput "also this" "inj-1")))
  (assert (= (dialogue.queued-refs injected.state) #("inj-1")))
  (setv plan (dialogue.interrupt injected.state "rid-2"))
  (assert (not plan.signal))
  (assert (= plan.sends #((dialogue.interrupt-request-line "rid-2"))))
  (setv answered (dialogue.on-record plan.state {"type" "control_response"
                                                 "response" {"subtype" "success" "request_id" "rid-2"
                                                             "response" {"still_queued" ["inj-1"]}}} None))
  (assert (= answered.state.stop (StopControl "rid-2" #("inj-1"))))
  (setv aborted (dialogue.on-record answered.state {"type" "result" "subtype" "error_during_execution" "is_error" True
                                                    "terminal_reason" "aborted_tools"} None))
  (assert (= aborted.end (Interrupted :surviving-refs #("inj-1"))) (repr aborted.end))
  (assert aborted.continues)
  (assert (not aborted.close))
  (assert aborted.state.in-flight)
  (setv running (. (dialogue.on-record aborted.state (lifecycle "inj-1" "started") None) state))
  (setv finished (dialogue.on-record running (| SUCCESS-RESULT {"user_message_uuids" ["inj-1"]}) None))
  (assert (= finished.end.input-refs #("inj-1")))
  (assert finished.close))


(deftest test-a-refused-control-request-falls-back-to-sigint
  (setv plan (dialogue.interrupt (. (dialogue.inject (started) (TurnInput "x" "inj-1")) state) "rid-3"))
  (setv refused (dialogue.on-record plan.state {"type" "control_response"
                                                "response" {"subtype" "error" "request_id" "rid-3"}} None))
  (assert refused.signal)
  (assert (isinstance refused.state.stop StopSignal)))


(deftest test-the-cli-own-turn-result-is-not-the-end
  ;; ResumeSession の直後に CLI が孤児の task の報せを自分の手番として走らせた result(origin task-notification)。
  (setv read (dialogue.on-record (started) {"type" "result" "subtype" "success" "is_error" False "result" ""
                                            "origin" {"kind" "task-notification"}} None))
  (assert (is read.end None))
  (assert read.state.in-flight))


(deftest test-a-result-with-unread-input-is-swallowed-until-the-input-is-settled
  ;; result の時点で読まれていない注入 → CLI が次の手番として走らせる(中間の result)。その注入が走らずに終われば、
  ;; 飲んだ result で手番を閉じる。
  (setv injected (dialogue.inject (started) (TurnInput "late" "inj-1")))
  (setv swallowed (dialogue.on-record injected.state SUCCESS-RESULT None))
  (assert (is swallowed.end None))
  (assert (= swallowed.state.deferred-result SUCCESS-RESULT))
  (setv cancelled (dialogue.on-record swallowed.state (lifecycle "inj-1" "cancelled") None))
  (assert (isinstance cancelled.end Completed) (repr cancelled.end))
  (assert (= cancelled.end.result-text "OKAPI-77")))


(deftest test-a-process-that-exits-mid-turn-is-backend-lost-unless-stopped
  (setv lost (dialogue.on-exit (started) -9 "killed"))
  (assert (= lost.end (BackendLost "process exited with code -9 before the turn ended: killed")))
  (setv stopped (dialogue.on-exit (. (dialogue.interrupt (started) "rid") state) 0 ""))
  (assert (= stopped.end (Interrupted)))
  (setv idle (dialogue.on-exit (DialogueState) 0 ""))
  (assert (is idle.end None)))


(deftest test-a-permission-question-is-answered-once
  (setv request (PermissionRequested "req-1" "Bash" {"command" "touch x"}))
  (setv asked (dialogue.on-record (started) {"type" "control_request" "request_id" "req-1"} request))
  (setv allowed (dialogue.answer-permission asked.state "req-1" (Allow)))
  (setv line (json.loads (get allowed.sends 0)))
  (assert (= line {"type" "control_response"
                   "response" {"subtype" "success" "request_id" "req-1"
                               "response" {"behavior" "allow" "updatedInput" {"command" "touch x"}}}}))
  (assert (is (dialogue.answer-permission allowed.state "req-1" (Allow)) None))
  (setv denied (dialogue.answer-permission asked.state "req-1" (Deny "no")))
  (assert (= (get (json.loads (get denied.sends 0)) "response" "response") {"behavior" "deny" "message" "no"})))


(deftest test-input-outside-a-turn-is-not-written
  (assert (is (dialogue.inject (DialogueState) (TurnInput "x" "r")) None))
  (assert (is (dialogue.interrupt (DialogueState) "rid") None)))


(deftest test-the-user-line-spelling
  (assert (= (json.loads (dialogue.user-line (TurnInput "hi" "r1")))
             {"type" "user" "message" {"role" "user" "content" "hi"} "uuid" "r1"}))
  (assert (= (get (json.loads (dialogue.user-line (TurnInput "look" "r2" #((ImageAttachment "image/png" "AAAA")))))
                  "message" "content")
             [{"type" "text" "text" "look"}
              {"type" "image" "source" {"type" "base64" "media_type" "image/png" "data" "AAAA"}}])))
