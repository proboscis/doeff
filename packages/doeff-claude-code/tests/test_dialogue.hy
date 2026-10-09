;; 状態機械(dialogue.hy)の検 — 純関数だけ。行は実物の形(claude 2.1.282 の実測 = #602 の layer2-cli-capabilities.md)。
;; 状態機械は lines.hy が分類した型を読む — 検は実物の形の行を classify-record に通してから渡す(本番の handler と同じ道)。
(require doeff-hy.macros [deftest val])
(import json)
(import doeff_claude_code.values [TurnInput ImageAttachment Allow Deny])
(import doeff_claude_code.lines [Completed Failed Interrupted BackendLost Usage classify-record])
(import doeff_claude_code.dialogue :as dialogue)
(import doeff_claude_code.dialogue [DialogueState StopSignal StopControl NoStop])
(import doeff_claude_code.faults [StopReason])
(import doeff_claude_code [lines])

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
  (assert (= read.end (Interrupted :process-kept False)) (repr read.end))
  ;; SIGINT の形の CLI は result の後に自分で降りるので、使い回さずに降ろす(#3672)。
  (assert (= read.retire StopReason.INTERRUPT-SIGNAL) (repr read.retire))
  (assert (not read.state.in-flight)))


(deftest test-an-aborted-streaming-result-without-an-interrupt-stays-a-failure
  ;; 止めるを求めていない aborted_streaming(誰かが外から SIGINT を送った)は Failed のまま、terminal_reason を運ぶ。
  (setv read (read-record (started) SIGINT-RESULT))
  (assert (isinstance read.end Failed) (repr read.end))
  (assert (= read.end.terminal-reason "aborted_streaming"))
  (assert (= read.end.detail "error_during_execution")))


(deftest test-a-success-result-completes-the-turn-and-keeps-the-process
  ;; 手番の終わりで process を降ろさない — 次の手番まで生きて待つ(#3672)。
  (setv read (read-record (started) SUCCESS-RESULT))
  (assert (= read.end (Completed :result-text "OKAPI-77" :usage (Usage :output-tokens 7) :cost-usd 0.04 :input-refs #("msg-1"))))
  (assert (is read.retire None) (repr read.retire)))


(deftest test-a-line-outside-the-turn-retires-the-process-except-the-protocol-lines
  ;; 守り(#3672・#517 の事故の形): host の手番の外で CLI が出した行(手番の外で起きた model の出力)を読んだら、その process を
  ;; 降ろす。host が書いた入力の運命と control の答えは作法の行なので降ろさない。
  (setv idle (. (read-record (started) SUCCESS-RESULT) state))
  (assert (not idle.in-flight))
  (setv woke (read-record idle INIT))
  (assert (= woke.retire StopReason.OUTSIDE-TURN-OUTPUT) (repr woke.retire))
  (setv spoke (read-record idle {"type" "assistant" "message" {"role" "assistant" "content" [{"type" "text" "text" "x"}]}}))
  (assert (= spoke.retire StopReason.OUTSIDE-TURN-OUTPUT) (repr spoke.retire))
  (assert (is (. (read-record idle (lifecycle "msg-1" "completed")) retire) None))
  (assert (is (. (read-record idle {"type" "control_response" "response" {"subtype" "success" "request_id" "rid-9"}}) retire)
              None)))


;; --- 入力なしで事前起動した process の、最初の入力の前の行 ---------------------------------------------------
;; SessionStart の hook の 2 行(実物の stream-json の形 — hook_started と hook_response。欄 hook_event が hook のイベント名・
;; hook_name は「イベント名:matcher」)。入力を書かずに起動した CLI が最初の入力の前に出す行はこの 2 行だけ(本物の CLI を入力なしで
;; 30 秒待たせた計測 — init・assistant・rate_limit_event の行は 0 本。init は入力の後に出る)。
(val SESSION-START-HOOK-STARTED {"type" "system" "subtype" "hook_started" "hook_id" "hook-1" "hook_name" "SessionStart:startup"
                                 "hook_event" "SessionStart" "uuid" "u-1" "session_id" SID})
(val SESSION-START-HOOK-RESPONSE {"type" "system" "subtype" "hook_response" "hook_id" "hook-1" "hook_name" "SessionStart:startup"
                                  "hook_event" "SessionStart" "output" "" "stdout" "" "stderr" "" "exit_code" 0
                                  "outcome" "success" "uuid" "u-2" "session_id" SID})

;; 条件つきルールの助言を返した PreToolUse の hook の応答の行(CLI 2.1.292 の --include-hook-events の形 — stdout は hook が書いた JSON)。
(val ADVICE-STDOUT "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"additionalContext\":\"[rulebook] advice\"}}")
(val ADVISING-HOOK-RESPONSE {"type" "system" "subtype" "hook_response" "hook_id" "hook-7" "hook_name" "PreToolUse:Bash"
                             "hook_event" "PreToolUse" "output" ADVICE-STDOUT "stdout" ADVICE-STDOUT "stderr" "" "exit_code" 0
                             "outcome" "success" "uuid" "u-7" "session_id" SID})

(deftest test-outside-a-turn-only-the-hooks-that-are-not-model-work-are-quiet
  ;; CLI に全 hook の行を出させる(--include-hook-events)と、ターンの外でも Notification・SessionEnd の hook の行が出る — どちらも CLI が
  ;; 回す物で model の動きではないので、ターンの外の出力として process を止めない(card acp:kanban-issue:ki-d8b473480303)。model の
  ;; 手番の中で走る hook(UserPromptSubmit・PreToolUse・Stop ほか)の行は、ターンの外なら今までどおり止める(model が動いた兆候)。
  (val idle (. (read-record (. (read-record (. (dialogue.begin-turn (DialogueState :session-id SID) (TurnInput "hello" "msg-1")) state)
                                            INIT) state)
                            SUCCESS-RESULT) state))
  (assert (not idle.in-flight) (repr idle))
  (for [event ["Notification" "SessionEnd"]]
    (for [record [(| SESSION-START-HOOK-STARTED {"hook_event" event "hook_name" event})
                  (| SESSION-START-HOOK-RESPONSE {"hook_event" event "hook_name" event})]]
      (val read (read-record idle record))
      (assert (is read.retire None) (repr #(record read.retire)))
      (assert (= read.state idle) (repr read.state))))
  (for [event ["UserPromptSubmit" "PreToolUse" "Stop"]]
    (assert (= (. (read-record idle (| ADVISING-HOOK-RESPONSE {"hook_event" event "hook_name" event})) retire)
               StopReason.OUTSIDE-TURN-OUTPUT)
            event)))


(deftest test-before-the-first-input-only-the-session-start-hook-lines-are-quiet
  ;; 入力なしで事前起動した process(ClaudeWarmSession)が最初の入力の前に出してよいのは、SessionStart の hook の開始と応答の行だけ。
  ;; それ以外の行(SessionStart 以外の hook の行・SessionStart の hook の途中経過の行・system/init・assistant・rate_limit_event・
  ;; result)は今までどおりターンの外の出力として停止する(背景の仕事がターンの外で model を呼ばないための守りを広げない)。最初の入力の
  ;; 後の待ち(ターンを 1 度終えた後)は SessionStart の行も停止の理由になる — 緩めるのは最初の入力の前だけ。
  (val waiting (DialogueState :session-id SID :awaiting-first-input True))
  (for [record [SESSION-START-HOOK-STARTED SESSION-START-HOOK-RESPONSE]]
    (val read (read-record waiting record))
    (assert (is read.retire None) (repr #(record read.retire)))
    (assert (= read.state waiting) (repr read.state)))
  (for [record [(| SESSION-START-HOOK-RESPONSE {"hook_event" "PreToolUse" "hook_name" "PreToolUse:Bash"})
                (| SESSION-START-HOOK-STARTED {"hook_event" "UserPromptSubmit" "hook_name" "UserPromptSubmit"})
                {"type" "system" "subtype" "hook_progress" "hook_event" "SessionStart" "hook_name" "SessionStart:startup"}
                INIT
                {"type" "assistant" "message" {"role" "assistant" "content" [{"type" "text" "text" "x"}]}}
                {"type" "rate_limit_event" "rate_limit_info" {"rateLimitType" "five_hour"}}
                SUCCESS-RESULT]]
    (assert (= (. (read-record waiting record) retire) StopReason.OUTSIDE-TURN-OUTPUT) (repr record)))
  ;; 最初の入力でマークは消える。ターンを終えた後の待ちでは SessionStart の hook の行もターンの外の出力。
  (val begun (dialogue.begin-turn waiting (TurnInput "hello" "msg-1")))
  (assert (not begun.state.awaiting-first-input) (repr begun.state))
  (val idle (. (read-record (. (read-record begun.state INIT) state) SUCCESS-RESULT) state))
  (assert (= (. (read-record idle SESSION-START-HOOK-STARTED) retire) StopReason.OUTSIDE-TURN-OUTPUT)))


(deftest test-hook-lines-are-classified-by-their-event
  ;; hook の開始と応答の行は、イベント名(hook_event — この欄の無い版では hook_name の「:」の前)と段階を持つ型 HookNotice に分ける。
  ;; 応答の行は hook の id・出力(stdout・stderr・output)・exit code・終わり方(outcome)も運ぶ(会話の画面に hook の結果を出すため・
  ;; card acp:kanban-issue:ki-d8b473480303)。途中経過の行(hook_progress)など、ほかの hook の行は語彙の外(Other)のまま。
  (assert (= (classify-record SESSION-START-HOOK-STARTED)
             (lines.HookNotice :event "SessionStart" :phase lines.HookPhase.STARTED :name "SessionStart:startup" :hook-id "hook-1")))
  (assert (= (classify-record SESSION-START-HOOK-RESPONSE)
             (lines.HookNotice :event "SessionStart" :phase lines.HookPhase.RESPONSE :name "SessionStart:startup" :hook-id "hook-1"
                               :exit-code 0 :outcome "success")))
  (assert (= (classify-record ADVISING-HOOK-RESPONSE)
             (lines.HookNotice :event "PreToolUse" :phase lines.HookPhase.RESPONSE :name "PreToolUse:Bash" :hook-id "hook-7"
                               :stdout ADVICE-STDOUT :output ADVICE-STDOUT :exit-code 0 :outcome "success")))
  (assert (= (classify-record {"type" "system" "subtype" "hook_response" "hook_id" "hook-8" "hook_name" "Stop" "hook_event" "Stop"
                               "output" "blocked" "stdout" "" "stderr" "blocked" "exit_code" 2 "outcome" "error"})
             (lines.HookNotice :event "Stop" :phase lines.HookPhase.RESPONSE :name "Stop" :hook-id "hook-8" :stderr "blocked"
                               :output "blocked" :exit-code 2 :outcome "error")))
  (assert (= (classify-record {"type" "system" "subtype" "hook_started" "hook_name" "SessionStart:resume"})
             (lines.HookNotice :event "SessionStart" :phase lines.HookPhase.STARTED :name "SessionStart:resume")))
  (assert (= (classify-record {"type" "system" "subtype" "hook_progress" "hook_event" "SessionStart"})
             (lines.Other :type "system" :subtype "hook_progress"))))


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
  (assert (= aborted.end (Interrupted :process-kept True :surviving-refs #("inj-1"))) (repr aborted.end))
  (assert aborted.continues)
  (assert (is aborted.retire None))
  (assert aborted.state.in-flight)
  (setv running (. (read-record aborted.state (lifecycle "inj-1" "started")) state))
  (setv finished (read-record running (| SUCCESS-RESULT {"user_message_uuids" ["inj-1"]})))
  (assert (= finished.end.input-refs #("inj-1")))
  (assert (is finished.retire None)))


(deftest test-an-interrupted-end-tells-whether-the-process-stays
  ;; 止めた手番の終わりは、同じ CLI の process が会話に残るかを運ぶ(#3672 の決め 6 — control の止めは手番だけを止めて process を残し、
  ;; SIGINT の形と会話を閉じる止めは process を降ろす)。使い手(層 3 の AgentTurnInterrupted.cli_kept)はこの欄で、降りた CLI の貸与を
  ;; 持ち続けない。SIGINT の形・生き残りの在る control の形・process が降りた形は上と下の検が確かめる。
  ;; control の止めで生き残りが無い: 手番は終わり、process は残る。
  (setv injected (dialogue.inject (started) (TurnInput "also this" "inj-1")))
  (setv plan (dialogue.interrupt injected.state "rid-4"))
  (setv answered (read-record plan.state {"type" "control_response"
                                                 "response" {"subtype" "success" "request_id" "rid-4"
                                                             "response" {"still_queued" []}}}))
  (setv ended (read-record answered.state {"type" "result" "subtype" "error_during_execution" "is_error" True
                                                  "terminal_reason" "aborted_tools"}))
  (assert (= ended.end (Interrupted :process-kept True :dropped-refs #("inj-1"))) (repr ended.end))
  (assert (is ended.retire None))
  ;; 会話を閉じる: 走っている手番は、降りる process の上で終わる。
  (setv closed (dialogue.close-session (started)))
  (assert (= closed.end (Interrupted :process-kept False)) (repr closed.end)))


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
  (assert (= lost.end (BackendLost "process exited with code -9 before the turn ended: killed" :exit-code -9 :stderr-tail "killed"))
          (repr lost.end))
  (setv stopped (dialogue.on-exit (. (dialogue.interrupt (started) "rid") state) 0 ""))
  (assert (= stopped.end (Interrupted :process-kept False)))
  (setv idle (dialogue.on-exit (DialogueState) 0 ""))
  (assert (is idle.end)))


;; --- 手番の途中で降りた process の終了 code と stderr の末尾(#4207)------------------------------------------------
;; 失敗ケース(前の形): 終了 code と stderr の末尾は BackendLost の文 detail の中にだけ在り、上の層は文を読み解かないと
;; 「なぜ降りたか」を知れない。

(deftest test-a-lost-turn-carries-the-exit-code-and-the-stderr-tail-as-fields
  ;; 手番の途中で process が降りた(終わりの行の前): BackendLost の欄に終了 code と stderr の末尾が載る。文 detail は今のまま。
  (val lost (dialogue.on-exit (started) 1 "boom"))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (assert (= #(lost.end.exit-code lost.end.stderr-tail) #(1 "boom")) (repr lost.end))
  (assert (= lost.end.detail "process exited with code 1 before the turn ended: boom") (repr lost.end))
  ;; stderr が空でも、降りた事実の値(空の文字列)をそのまま載せる。
  (val quiet (dialogue.on-exit (started) 137 ""))
  (assert (= #(quiet.end.exit-code quiet.end.stderr-tail) #(137 "")) (repr quiet.end)))


(deftest test-a-swallowed-failed-result-ends-with-the-exit-code-and-the-stderr-tail
  ;; 注入を待って飲んだ result が誤りの result で、注入を読む前に process が降りた: 終わりは飲んだ result の Failed で、終了 code と
  ;; stderr の末尾を欄に載せる。飲んだ result が誤りでなければ Completed のまま(Completed に欄は無い)。
  (val injected (dialogue.inject (started) (TurnInput "late" "inj-1")))
  (val swallowed (read-record injected.state {"type" "result" "subtype" "error_max_turns" "is_error" True "result" "capped"}))
  (assert (is swallowed.end None) (repr swallowed.end))
  (val exited (dialogue.on-exit swallowed.state 1 "boom"))
  (assert (isinstance exited.end Failed) (repr exited.end))
  (assert (= #(exited.end.detail exited.end.exit-code exited.end.stderr-tail) #("capped" 1 "boom")) (repr exited.end))
  (val calm (dialogue.on-exit (. (read-record injected.state SUCCESS-RESULT) state) 1 "boom"))
  (assert (= calm.end (Completed :result-text "OKAPI-77" :usage (Usage :output-tokens 7) :cost-usd 0.04 :input-refs #("msg-1")))
          (repr calm.end))
  ;; result の行で閉じた失敗(process は降りていない)は欄を持たない(None)。
  (val failed (read-record (started) {"type" "result" "subtype" "error_max_turns" "is_error" True "result" "capped"}))
  (assert (= #(failed.end.exit-code failed.end.stderr-tail) #(None None)) (repr failed.end)))


(deftest test-the-stderr-tail-on-the-end-keeps-only-the-last-chars-within-the-limit
  ;; stderr の末尾の欄は上限(lines.STDERR-TAIL-CHARS 字)の内: 越えた stderr は頭を捨てて末尾を残す。上限ちょうどは切らない。
  (val limit lines.STDERR-TAIL-CHARS)
  (val tail (* "t" limit))
  (val lost (dialogue.on-exit (started) 1 (+ (* "h" 500) tail)))
  (assert (= lost.end.stderr-tail tail) (len lost.end.stderr-tail))
  (val exact (dialogue.on-exit (started) 1 tail))
  (assert (= exact.end.stderr-tail tail) (len exact.end.stderr-tail))
  (val swallowed (read-record (. (dialogue.inject (started) (TurnInput "late" "inj-1")) state)
                              {"type" "result" "subtype" "error_max_turns" "is_error" True "result" "capped"}))
  (val exited (dialogue.on-exit swallowed.state 1 (+ (* "h" 500) tail)))
  (assert (= exited.end.stderr-tail tail) (len exited.end.stderr-tail)))


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


;; --- 本体の会話の最後の呼びの usage と model・model ごとの窓(#3744) -----------------------------------------

;; 1 つの API の呼び(message)は content の block ごとに assistant の行を出し、どの行も同じ id・同じ usage を名乗る。subagent の行は
;; parent_tool_use_id に親の呼びの id を持つ。
(val CALL-1-USAGE {"input_tokens" 4 "cache_creation_input_tokens" 300 "cache_read_input_tokens" 9000 "output_tokens" 2})
(val CALL-2-USAGE {"input_tokens" 6 "cache_creation_input_tokens" 50 "cache_read_input_tokens" 9300 "output_tokens" 11})
(val CALL-1-TEXT {"type" "assistant" "parent_tool_use_id" None
                  "message" {"id" "msg_1" "model" "claude-opus-4-5" "content" [{"type" "text" "text" "x"}] "usage" CALL-1-USAGE}})
(val CALL-1-TOOL {"type" "assistant" "parent_tool_use_id" None
                  "message" {"id" "msg_1" "model" "claude-opus-4-5"
                             "content" [{"type" "tool_use" "id" "toolu_1" "name" "Task" "input" {}}] "usage" CALL-1-USAGE}})
(val SUBAGENT-CALL {"type" "assistant" "parent_tool_use_id" "toolu_1"
                    "message" {"id" "msg_s" "model" "claude-haiku-4-5" "content" [{"type" "text" "text" "sub"}]
                               "usage" {"input_tokens" 1 "cache_read_input_tokens" 50000 "output_tokens" 900}}})
(val CALL-2-TEXT {"type" "assistant" "parent_tool_use_id" None
                  "message" {"id" "msg_2" "model" "claude-opus-4-5" "content" [{"type" "text" "text" "done"}] "usage" CALL-2-USAGE}})
(val WINDOWED-RESULT (| SUCCESS-RESULT {"modelUsage" {"claude-opus-4-5" {"contextWindow" 200000 "maxOutputTokens" 64000}
                                                      "claude-haiku-4-5" {"contextWindow" 200000 "maxOutputTokens" 32000}}}))

(deftest test-the-turn-end-carries-the-last-main-call-and-the-model-windows
  ;; 手番の終わりは、本体の会話(parent_tool_use_id が null)の最後の assistant の行の usage と model、result の行の modelUsage の
  ;; model ごとの窓を運ぶ。subagent の行(parent_tool_use_id が在る)は後に読んでも取らない。
  (var state (started))
  (for [record [CALL-1-TEXT CALL-1-TOOL SUBAGENT-CALL CALL-2-TEXT SUBAGENT-CALL]]
    (:= state (. (read-record state record) state)))
  (val read (read-record state WINDOWED-RESULT))
  (assert (isinstance read.end Completed) (repr read.end))
  (assert (= #(read.end.last-call-usage read.end.last-call-model read.end.model-windows)
             #((Usage :input-tokens 6 :cache-creation-input-tokens 50 :cache-read-input-tokens 9300 :output-tokens 11)
               "claude-opus-4-5"
               #((lines.ModelWindow "claude-opus-4-5" 200000 64000) (lines.ModelWindow "claude-haiku-4-5" 200000 32000))))
          (repr read.end)))


(deftest test-the-last-call-is-kept-by-a-lost-turn-and-forgotten-at-the-next-turn
  ;; 途中で process が消えた手番も、それまでに読んだ本体の呼びの usage と model を運ぶ(result の行が無いので窓は空)。次の手番は
  ;; 空から数え直す — 前の手番の呼びと窓を次の手番の終わりへ持ち越さない(0 も発明しない)。
  (val called (. (read-record (started) CALL-1-TEXT) state))
  (val lost (dialogue.on-exit called 137 ""))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (assert (= #(lost.end.last-call-usage lost.end.last-call-model lost.end.model-windows)
             #((Usage :input-tokens 4 :cache-creation-input-tokens 300 :cache-read-input-tokens 9000 :output-tokens 2)
               "claude-opus-4-5" #()))
          (repr lost.end))
  (val finished (read-record called WINDOWED-RESULT))
  (val again (dialogue.begin-turn finished.state (TurnInput "again" "msg-2")))
  (val second (read-record (. (read-record again.state INIT) state) SUCCESS-RESULT))
  (assert (isinstance second.end Completed) (repr second.end))
  (assert (= #(second.end.last-call-usage second.end.last-call-model second.end.model-windows) #(None None #()))
          (repr second.end)))


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


;; --- 口座の限度に当たった手番(#3983)--------------------------------------------------------------------------
;; 形の出所: Claude Code 2.1.291 の会話の記録(zeus の subagent の記録 2026-10-07 02:59Z)の答えの行 — model "<synthetic>"・本文
;; "You've hit your session limit · resets 12pm (Asia/Tokyo)"・error "rate_limit"・quotaLimits {status rejected・resetsAt・rateLimitType
;; five_hour・overageStatus rejected}。stream-json の rate_limit_event の rate_limit_info は同じ欄(status・resetsAt・rateLimitType)を
;; 運ぶ形として読む(生の stream-json の見本は手元に無い — 推定)。
(val LIMIT-TEXT "You've hit your session limit · resets 12pm (Asia/Tokyo)")
(val LIMIT-REJECTED {"type" "rate_limit_event"
                     "rate_limit_info" {"status" "rejected" "resetsAt" 1791342000 "rateLimitType" "five_hour" "overageStatus" "rejected"}})
(val LIMIT-ALLOWED {"type" "rate_limit_event"
                    "rate_limit_info" {"status" "allowed" "resetsAt" 1791342000 "rateLimitType" "five_hour"
                                       "unifiedWindows" {"five_hour" {"utilization" 0.4}}}})
(val LIMIT-ANSWER {"type" "assistant" "parent_tool_use_id" None "error" "rate_limit"
                   "message" {"id" "msg_l" "model" "<synthetic>" "content" [{"type" "text" "text" LIMIT-TEXT}]
                              "usage" {"input_tokens" 0 "output_tokens" 0}}})
(val LIMIT-RESULT {"type" "result" "subtype" "success" "is_error" True "result" LIMIT-TEXT "api_error_status" 429})


(deftest test-a-turn-that-hits-the-account-limit-says-so-in-its-end
  ;; 限度の拒みの行と限度の答え(error rate_limit)を読んだ手番の終わりは、閉じた型の欄 account-limit(どの限度・戻る刻・限度の文)を持つ。
  ;; 失敗ケース(前の形): 終わりに欄が無く、上の層が「口座が尽きた」と知れない(本番 2026-10-07 14:55 の aj-FH8KCFN6… が終わったまま)。
  (var state (started))
  (for [record [LIMIT-REJECTED LIMIT-ANSWER]]
    (:= state (. (read-record state record) state)))
  (val read (read-record state LIMIT-RESULT))
  (assert (isinstance read.end Failed) (repr read.end))
  (assert (= read.end.account-limit (lines.AccountLimitHit :window "five_hour" :resets-at 1791342000 :text LIMIT-TEXT)) (repr read.end)))


(deftest test-an-allowed-limit-line-is-not-a-limit-hit-and-the-hit-is-not-carried-to-the-next-turn
  ;; 許された限度の行(status allowed)だけの手番は account-limit を持たない。限度に当たった手番の次の手番は空から数え直す。
  (val allowed (. (read-record (started) LIMIT-ALLOWED) state))
  (val calm (read-record allowed SUCCESS-RESULT))
  (assert (and (isinstance calm.end Completed) (is calm.end.account-limit None)) (repr calm.end))
  (val hit (. (read-record (. (read-record (started) LIMIT-REJECTED) state) LIMIT-ANSWER) state))
  (val failed (read-record hit LIMIT-RESULT))
  (val again (dialogue.begin-turn failed.state (TurnInput "again" "msg-2")))
  (val second (read-record (. (read-record again.state INIT) state) SUCCESS-RESULT))
  (assert (and (isinstance second.end Completed) (is second.end.account-limit None)) (repr second.end)))


;; --- 口座の側の断りで答えなかった手番 ---------------------------------------------------------------------------
;; 形の出所: 2026-10-08 22:32 の本番(口座 cryptic-2)の CLI の stream の本体の assistant の行 — parent_tool_use_id null・model
;; "<synthetic>"・最上位の error "oauth_org_not_allowed"・isApiErrorMessage true・apiErrorStatus 403・apiErrorCode
;; "oauth_not_allowed_for_organization"・本文の文は下の REFUSAL-TEXT。続く result の行の見本は手元に無い — 限度の答えの result の行
;; (LIMIT-RESULT — subtype success・is_error true・本文・api_error_status)と同じ形と推定。authentication_failed・billing_error の行は
;; 同じ形で error の語だけが違うと推定(CLI の型 SDKAssistantMessageError の語)。
(val REFUSAL-TEXT (+ "Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key instead, "
                     "or ask your admin to enable access"))
(val REFUSAL-ANSWER {"type" "assistant" "parent_tool_use_id" None "error" "oauth_org_not_allowed" "isApiErrorMessage" True
                     "apiErrorStatus" 403 "apiErrorCode" "oauth_not_allowed_for_organization"
                     "message" {"id" "msg_r" "model" "<synthetic>" "content" [{"type" "text" "text" REFUSAL-TEXT}]
                                "usage" {"input_tokens" 0 "output_tokens" 0}}})
(val REFUSAL-RESULT {"type" "result" "subtype" "success" "is_error" True "result" REFUSAL-TEXT "api_error_status" 403})


(deftest test-a-turn-the-account-refuses-carries-the-refusal-on-its-end
  ;; 本体の assistant の行の error が口座の側の断りの語(oauth_org_not_allowed・authentication_failed・billing_error)なら、手番の終わりは
  ;; 閉じた型の欄 account-refusal(断りの語と CLI の文)を持つ。口座の限度(account-limit)とは別の欄で、限度の欄は None のまま。
  ;; 失敗ケース(前の形): 終わりに口座の断りの欄が無く、上の層(turn-host)が「この口座は使えない」と知れずに cli-failed で落とし、
  ;; 口座を替えなかった(本番 2026-10-08 22:32 の口座 cryptic-2)。
  (val errors ["oauth_org_not_allowed" "authentication_failed" "billing_error"])
  (val ends (lfor error errors
                  (. (read-record (. (read-record (started) (| REFUSAL-ANSWER {"error" error})) state) REFUSAL-RESULT) end)))
  (for [#(error end) (zip errors ends)]
    (assert (isinstance end Failed) (repr end))
    (assert (= end.account-refusal (lines.AccountRefusalHit :error error :text REFUSAL-TEXT)) (repr end))
    (assert (is end.account-limit None) (repr end))
    (assert (= #(end.detail end.api-error-status) #(REFUSAL-TEXT 403)) (repr end))))


(deftest test-a-limit-answer-is-not-an-account-refusal
  ;; 口座の限度の答え(error rate_limit)は今までどおり account-limit だけに数え、口座の断りには数えない。
  (var state (started))
  (for [record [LIMIT-REJECTED LIMIT-ANSWER]]
    (:= state (. (read-record state record) state)))
  (val read (read-record state LIMIT-RESULT))
  (assert (= read.end.account-limit (lines.AccountLimitHit :window "five_hour" :resets-at 1791342000 :text LIMIT-TEXT)) (repr read.end))
  (assert (is read.end.account-refusal None) (repr read.end)))


(deftest test-a-subagent-refusal-is-not-counted-and-a-refusal-is-not-carried-to-the-next-turn
  ;; subagent の行(parent_tool_use_id が null でない)の error は本体の会話の断りではないので数えない。口座に断られた手番の次の手番は
  ;; 空から数え直す。
  (val sub (. (read-record (started) (| REFUSAL-ANSWER {"parent_tool_use_id" "toolu_sub"})) state))
  (val calm (read-record sub SUCCESS-RESULT))
  (assert (and (isinstance calm.end Completed) (is calm.end.account-refusal None)) (repr calm.end))
  (val refused (read-record (. (read-record (started) REFUSAL-ANSWER) state) REFUSAL-RESULT))
  (assert (is-not refused.end.account-refusal None) (repr refused.end))
  (val again (dialogue.begin-turn refused.state (TurnInput "again" "msg-2")))
  (val second (read-record (. (read-record again.state INIT) state) SUCCESS-RESULT))
  (assert (and (isinstance second.end Completed) (is second.end.account-refusal None)) (repr second.end)))
