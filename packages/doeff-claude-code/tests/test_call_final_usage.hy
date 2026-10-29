;;; 呼びの最終の usage(card ki-290ec664737d)の検: CLI の stdout の assistant の行の usage は呼びの途中の値(出力 16 など)なので、
;;; 同じ親の message_start と message_delta の対を 1 つの閉じた呼び(CallClosed)へ組み、上の層が呼びごとの使用量を最終の値で記録できる
;;; ようにする。重ね方(closed-usage)は CLI 2.1.292 が自分の記録に使う規則と同じ。
;;;
;;; 行の形の出所(実測 2026-10-22・本物の CLI 2.1.292 を 127.0.0.1 の偽の Messages API へ向けた計測): message_start の行と、その呼びの
;;; assistant の行(content の block ごと)は出力 16 を名乗り、message_delta の行が最終の出力と入力の側・cache の寿命の内訳・
;;; server_tool_use を名乗る。result の行の usage は本体の呼びの最終の値の和で、subagent の呼びを入れない。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import sys)
(import uuid)
(import pathlib [Path])
(import doeff [with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession TurnInput])
(import doeff_claude_code.lines [AssistantMessage CallClosed CallOpened CallStep Completed Interrupted PartialMessage TurnResult Usage
                                 call-step classify-record closed-usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInterruptTurn])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.clock [clock-of])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [STREAMED-CALLS-PHRASE sleep-prompt])
(import tests.scenario_steps [TurnRecord read-to-end read-to-tool-start new-id typed])

;; 実測の行(本物の CLI 2.1.292 の stdout — 偽の API が始まりに出力 16、終わりに出力 501 と cache の寿命の内訳を返した呼び)。
(val MEASURED-START
     {"type" "stream_event" "parent_tool_use_id" None "session_id" "s"
      "event" {"type" "message_start"
               "message" {"id" "msg_p1" "type" "message" "role" "assistant" "model" "claude-opus-5-5" "content" []
                          "usage" {"input_tokens" 11 "cache_creation_input_tokens" 1001 "cache_read_input_tokens" 20001
                                   "cache_creation" {"ephemeral_5m_input_tokens" 400 "ephemeral_1h_input_tokens" 601}
                                   "output_tokens" 16 "service_tier" "standard"}}}})
(val MEASURED-ASSISTANT
     {"type" "assistant" "parent_tool_use_id" None "session_id" "s"
      "message" {"id" "msg_p1" "role" "assistant" "model" "claude-opus-5-5" "content" [{"type" "text" "text" "answer 1"}]
                 "usage" {"input_tokens" 11 "cache_creation_input_tokens" 1001 "cache_read_input_tokens" 20001
                          "cache_creation" {"ephemeral_5m_input_tokens" 400 "ephemeral_1h_input_tokens" 601}
                          "output_tokens" 16 "service_tier" "standard"}}})
(val MEASURED-DELTA
     {"type" "stream_event" "parent_tool_use_id" None "session_id" "s"
      "event" {"type" "message_delta" "delta" {"stop_reason" "tool_use" "stop_sequence" None}
               "usage" {"input_tokens" 11 "cache_creation_input_tokens" 1001 "cache_read_input_tokens" 20001
                        "cache_creation" {"ephemeral_5m_input_tokens" 400 "ephemeral_1h_input_tokens" 601}
                        "output_tokens" 501 "service_tier" "standard"
                        "server_tool_use" {"web_search_requests" 2 "web_fetch_requests" 3}}}})
(val MEASURED-FINAL (Usage :input-tokens 11 :output-tokens 501 :cache-creation-input-tokens 1001 :cache-read-input-tokens 20001
                           :cache-creation-5m-input-tokens 400 :cache-creation-1h-input-tokens 601 :web-search-requests 2
                           :service-tier "standard"))


(defk steps-over [#^ list records]
  {:pre [(: records list)] :post [(: % list)] :tags {:context "claude-code-test" :role "program"}}
  "行の列(JSON の object)を分類して順に call-step へ通し、閉じた呼びを出た順に返すため(handler の on-line と同じ通し方)。"
  (var open #())
  (var closed [])
  (for [record records]
    (<- step CallStep (call-step open (classify-record record)))
    (:= open step.open)
    (when (is-not step.closed None)
      (:= closed (+ closed [step.closed]))))
  closed)


(deftest test-a-call-closes-with-the-message-delta-usage-not-the-assistant-line-value
  ;; 実測の 3 行(始まり・途中の値の assistant の行・終わり)から、閉じた呼びは 1 つで、出力は終わりの 501(assistant の行の 16 ではない)、
  ;; 入力の側・cache の寿命の内訳・web 検索の数も終わりの値。失敗ケース = assistant の行の usage を呼びの値として残す読み(出力 16)。
  (val assistant (classify-record MEASURED-ASSISTANT))
  (assert (= assistant.usage.output-tokens 16) (repr assistant))
  (<- closed (steps-over [MEASURED-START MEASURED-ASSISTANT MEASURED-ASSISTANT MEASURED-DELTA]))
  (assert (= closed [(CallClosed :message-id "msg_p1" :model "claude-opus-5-5" :parent-tool-use-id None :usage MEASURED-FINAL
                                 :stopped True)])
          (repr closed)))


;; 実測の行(2026-10-24・本物の CLI 2.1.292 を偽の Messages API へ向け、本体の呼びが thinking を流している間に利用者の入力を足して
;; control_request interrupt で止めた計測 — card ki-7498a039250d)。CLI は「[Request interrupted by user]」の後に、止めた呼びの
;; 終わりの行を自分で作る: message_delta は止まった訳を名乗らず(stop_reason null)、usage は始まりの値のまま(出力 1)。その手番の
;; result の total_cost_usd はこの呼びを数えない(前の呼びの額だけ)。
(val INTERRUPTED-START
     {"type" "stream_event" "parent_tool_use_id" None "session_id" "s"
      "event" {"type" "message_start"
               "message" {"id" "msg_main2" "type" "message" "role" "assistant" "model" "claude-opus-5-5" "content" [] "stop_reason" None
                          "usage" {"input_tokens" 2 "cache_read_input_tokens" 2000 "cache_creation_input_tokens" 200
                                   "cache_creation" {"ephemeral_5m_input_tokens" 0 "ephemeral_1h_input_tokens" 200} "output_tokens" 1}}}})
(val INTERRUPTED-THINKING
     {"type" "stream_event" "parent_tool_use_id" None "session_id" "s"
      "event" {"type" "content_block_delta" "index" 0 "delta" {"type" "thinking_delta" "thinking" "hmm "}}})
(val INTERRUPTED-NOTE
     {"type" "user" "parent_tool_use_id" None "session_id" "s"
      "message" {"role" "user" "content" [{"type" "text" "text" "[Request interrupted by user]"}]}})
(val INTERRUPTED-DELTA
     {"type" "stream_event" "parent_tool_use_id" None "session_id" "s"
      "event" {"type" "message_delta" "context_management" None
               "delta" {"container" None "stop_details" None "stop_reason" None "stop_sequence" None}
               "usage" {"output_tokens_details" {"thinking_tokens" 0} "cache_creation_input_tokens" 200 "cache_read_input_tokens" 2000
                        "input_tokens" 2 "iterations" [] "output_tokens" 1
                        "server_tool_use" {"web_search_requests" 0 "web_fetch_requests" 0} "fallback_credit" None}}})


(deftest test-a-call-the-cli-stopped-closes-as-stopped-not-as-a-final-call
  ;; card ki-7498a039250d: 止めた呼びの CLI が作った終わり(止まった訳を名乗らない message_delta)は、呼びを止めた呼び(stopped
  ;; 偽・usage は始まりの値)として閉じ、API の終わり(止まった訳を名乗る)は最終の呼び(stopped 真)として閉じる。失敗ケース = 止まった
  ;; 訳を読まずに閉じる読み(本番 aj-P8G6EKP31ZKWS761MSKBCXK5PD — 止めた呼びが最終の値として記録され、CLI の額に無い呼びが 1 本増えた)。
  (<- closed (steps-over [INTERRUPTED-START INTERRUPTED-THINKING INTERRUPTED-NOTE INTERRUPTED-DELTA
                          MEASURED-START MEASURED-ASSISTANT MEASURED-DELTA]))
  (assert (= (lfor call closed #(call.message-id call.stopped call.usage.output-tokens call.usage.cache-read-input-tokens))
             [#("msg_main2" False 1 2000) #("msg_p1" True 501 20001)])
          (repr closed)))


(deftest test-the-end-usage-overlays-the-start-the-way-the-cli-merges-them
  ;; CLI 2.1.292 の重ね方: 入力の側は終わりが 0 より大きい数を名乗った時だけ終わりの値・出力は終わりが名乗れば 0 でも終わりの値・
  ;; cache の寿命の内訳は終わりが内訳を名乗った時だけ終わりの値(総数を名乗らず内訳だけなら和)。
  (val start (Usage :input-tokens 7 :output-tokens 16 :cache-creation-input-tokens 900 :cache-read-input-tokens 5000
                    :cache-creation-5m-input-tokens 0 :cache-creation-1h-input-tokens 900 :service-tier "standard"))
  ;; 終わりが出力だけを名乗る(古い API の形): 入力の側と寿命は始まりのまま。
  (<- only-output (closed-usage start (Usage :output-tokens 340)))
  (assert (= only-output (Usage :input-tokens 7 :output-tokens 340 :cache-creation-input-tokens 900 :cache-read-input-tokens 5000
                                :cache-creation-5m-input-tokens 0 :cache-creation-1h-input-tokens 900 :service-tier "standard"))
          (repr only-output))
  ;; 終わりが入力の側を 0 で名乗る: 0 は採らない(始まりの値)。出力の 0 は採る。
  (<- zeros (closed-usage start (Usage :input-tokens 0 :output-tokens 0 :cache-creation-input-tokens 0 :cache-read-input-tokens 0)))
  (assert (= #(zeros.input-tokens zeros.output-tokens zeros.cache-creation-input-tokens zeros.cache-read-input-tokens)
             #(7 0 900 5000))
          (repr zeros))
  ;; 終わりが寿命の内訳だけを名乗る: 書きの総数は内訳の和・内訳は終わりの値。
  (<- lifetimes (closed-usage start (Usage :output-tokens 20 :cache-creation-5m-input-tokens 300 :cache-creation-1h-input-tokens 0)))
  (assert (= #(lifetimes.cache-creation-input-tokens lifetimes.cache-creation-5m-input-tokens lifetimes.cache-creation-1h-input-tokens)
             #(300 300 0))
          (repr lifetimes))
  ;; 始まりか終わりの無い呼びは閉じない。
  (<- no-start (closed-usage None (Usage :output-tokens 3)))
  (<- no-end (closed-usage start None))
  (assert (= #(no-start no-end) #(None None)) (repr #(no-start no-end))))


(deftest test-a-resent-call-and-an-unended-call-do-not-close-and-parents-close-apart
  ;; 終わりの来なかった呼び(stream が切れて CLI が送り直した — 次の始まりが先に来る)は閉じず、送り直した呼びだけが閉じる・始まりを
  ;; 読まなかった終わりは何も閉じない(usage を発明しない)・本体と subagent の呼びの行が交互に来ても親ごとに閉じる・同じ呼びの終わりが
  ;; 2 度来ても閉じるのは 1 度。失敗ケース = 親を見ずに最後の始まりと組む読み(subagent の終わりが本体の呼びを閉じる)。
  (val main-start (fn [id output] {"type" "stream_event" "parent_tool_use_id" None
                                   "event" {"type" "message_start"
                                            "message" {"id" id "model" "m" "usage" {"input_tokens" 1 "output_tokens" output}}}}))
  (val sub-start {"type" "stream_event" "parent_tool_use_id" "toolu_1"
                  "event" {"type" "message_start" "message" {"id" "msg_sub" "model" "s" "usage" {"input_tokens" 2 "output_tokens" 1}}}})
  (val delta (fn [parent output] {"type" "stream_event" "parent_tool_use_id" parent
                                  "event" {"type" "message_delta" "delta" {"stop_reason" "end_turn"} "usage" {"output_tokens" output}}}))
  (<- closed (steps-over [(delta None 99)
                          (main-start "msg_lost" 16) (main-start "msg_resent" 16)
                          sub-start (delta "toolu_1" 40) (delta None 70) (delta None 71)]))
  (assert (= (lfor call closed #(call.message-id call.parent-tool-use-id call.usage.output-tokens))
             [#("msg_sub" "toolu_1" 40) #("msg_resent" None 70)])
          (repr closed)))


;; --- 本物の handler + 替え玉の CLI ------------------------------------------------------------------

(defn host-of []
  (ClaudeCodeHost #(sys.executable "-m" "hy" STUB-PATH) (clock-of (sync-time-handler)) 8 7200.0 :launch-timeout 30.0))

(defn spec-in [#^ Path tmp-path]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env "")) :cwd (str work) :settings {"disableAllHooks" True}))

(defk streamed-tool-turn [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % TurnRecord)] :tags {:context "claude-code-test" :role "program"}}
  "呼びを 3 段で流す道具の手番(本体の呼び 2 つ — 道具の呼びと最後の本文)を終わりまで読むため。"
  (<- started (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) spec
                               (TurnInput (+ (sleep-prompt 1 "TOOLED") " . " STREAMED-CALLS-PHRASE) (str (uuid.uuid4)))))
  (<- record (read-to-end started.turn 30.0))
  record)

(deftest test-the-handler-adds-each-main-call-final-usage-and-their-outputs-sum-to-the-cli-turn-output [tmp-path]
  ;; 受入(card ki-290ec664737d の条 c1154d7c3 の doeff の側): 道具の手番で、閉じた呼びは本体の呼びごとに 1 つ・呼びの id は
  ;; assistant の行と同じ・出力は最終の値で、その和が CLI の result の出力と一致する。assistant の行の出力は途中の値のまま。失敗
  ;; ケース = assistant の行の usage を呼びの値にする記録(和が 2 で result の 802 と合わない)。
  (val host (host-of))
  (<- record TurnRecord (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)]
                          (streamed-tool-turn (spec-in tmp-path))))
  (assert (isinstance record.end Completed) (repr record.end))
  (val assistants (lfor line record.lines :if (and (isinstance line.kind AssistantMessage) line.kind.usage) line.kind))
  (val closed (lfor line record.lines :if (isinstance line.kind CallClosed) line.kind))
  (assert (= (len closed) 2) (repr closed))
  (assert (= (lfor call closed call.message-id) (lfor message assistants message.message-id)) (repr #(closed assistants)))
  (assert (= (lfor message assistants message.usage.output-tokens) [1 1]) (repr assistants))
  (assert (= (lfor call closed call.usage.output-tokens) [405 401]) (repr closed))
  (assert (= (sum (gfor call closed call.usage.output-tokens)) record.end.usage.output-tokens) (repr record.end))
  ;; 閉じた呼びの行は、その呼びの終わり(message_delta)の行の直後に在る。
  (val kinds (lfor line record.lines line.kind))
  (for [call closed]
    (val before (get kinds (- (.index kinds call) 1)))
    (assert (and (isinstance before PartialMessage) (is-not before.call-end-usage None)) (repr before))))


;; --- fake ---------------------------------------------------------------------------------------

(val FAKE-SPEC (ClaudeSessionSpec :home (ClaudeHome "fake-home") :cwd "/work"))
(val CALL-USAGE (Usage :input-tokens 5 :output-tokens 230 :cache-creation-input-tokens 40 :cache-read-input-tokens 9000))

(defn fake-reply [#^ str text #^ tuple memory]  ; defk にできない: fake の世界が呼ぶ callback
  "検の返事: 本体の呼びの usage を名乗る返事(long は道具で 30 秒走る — 止める検のため)。"
  (FakeReply text :tool-seconds (if (= text "long") 30.0 0.0) :last-call-usage CALL-USAGE :last-call-model "claude-fake"))

(defk fake-turns []
  {:pre [] :post [(: % tuple)] :tags {:context "claude-code-test" :role "program"}}
  "fake の世界で、終わりまで走る手番と、道具の途中で止める手番を読むため。答え = 2 つの手番の読み。"
  (<- done-start (ClaudeStartTurn (FreshSession (new-id)) FAKE-SPEC (typed "short")))
  (<- done TurnRecord (read-to-end done-start.turn 120.0))
  (<- stop-start (ClaudeStartTurn (FreshSession (new-id)) FAKE-SPEC (typed "long")))
  (<- _ (read-to-tool-start stop-start.turn 120.0))
  (<- _ (ClaudeInterruptTurn stop-start.turn))
  (<- stopped TurnRecord (read-to-end stop-start.turn 120.0))
  #(done stopped))

(deftest test-the-fake-closes-the-main-call-before-the-result-and-not-when-stopped
  ;; fake も本番の handler と同じ行の種類を出す: 終わりまで走った手番は result の行の前に本体の呼びを 1 度閉じる(呼びの id は assistant の
  ;; 行と同じ)・止めた手番は閉じない(本物の CLI は終わりを受けずに止める — 出力は途中の値のまま)。
  (<- turns (with_handlers [(sim-time-handler :clock (SimClock)) (fake-claude-code-handler (FakeClaudeWorld fake-reply))] (fake-turns)))
  (val done (get turns 0))
  (val stopped (get turns 1))
  (assert (isinstance done.end Completed) (repr done.end))
  (val kinds (lfor line done.lines line.kind))
  (val closed (lfor kind kinds :if (isinstance kind CallClosed) kind))
  (val ids (sfor kind kinds :if (and (isinstance kind AssistantMessage) (is-not kind.usage None)) kind.message-id))
  (assert (= (len closed) 1) (repr kinds))
  (assert (= ids #{(. (get closed 0) message-id)}) (repr #(ids closed)))
  (assert (= (. (get closed 0) usage) CALL-USAGE) (repr closed))
  (assert (isinstance (get kinds (+ (.index kinds (get closed 0)) 1)) TurnResult) (repr kinds))
  (assert (isinstance stopped.end Interrupted) (repr stopped.end))
  (assert (not (any (gfor line stopped.lines (isinstance line.kind CallClosed)))) (repr stopped.lines)))
