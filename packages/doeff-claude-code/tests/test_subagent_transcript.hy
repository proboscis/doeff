;; subagent の transcript から subagent の呼びごとの消費を読む検(card ki-e798888dce4e)。
;;
;; 背景の task を止めた CLI(本番の起こし方)は subagent の呼びの行を stdout に出さないので、handler は subagent の task の報せの行で
;; その subagent の transcript を読み、呼びごとに畳んだ SubagentTranscript の行を手番の記録へ足す。読めない transcript は呼びを
;; 載せずに訳を載せ、log の行に残す。前半は transcript の読みの純関数、後半は替え玉の CLI(実物と同じく transcript を書く)で本番の
;; handler を回す。
(require doeff-hy.macros [deftest defk <- val])
(import datetime [datetime timezone])
(import json)
(import sys)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects [Listen SlogEffect])
(import doeff_core_effects [listen-handler slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code [ClaudeHome ClaudeSessionSpec FreshSession TurnInput])
(import doeff_claude_code [AgentToolResult BackendLost Completed SubagentTranscript SubagentTranscriptProblem TaskEvent ToolResult TurnResult]
        doeff_claude_code._lines [classify-record recorded-cost subagent-transcript-of usage-of])
(import doeff_hy.json_lines [jsonl-lines whole-lines-end])
(import doeff_claude_code [ClaudeStartTurn])
(import doeff_claude_code [ClaudeDropProcess])
(import doeff_claude_code._clock [clock-of])
(import doeff_claude_code [ClaudeCodeHost claude-code-handler]
        doeff_claude_code._handler [SUBAGENT-TRANSCRIPT-LOG])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [sleep-prompt])
(import tests.scenario_steps [TurnRecord read-to-end read-to-tool-start])


;; --- transcript の読み(純関数) ---------------------------------------------------------------------

(val AT "2026-10-21T09:34:23.797Z")
(val LATER "2026-10-21T09:34:24.100Z")

(defn line-of [record]
  ;; 実物の CLI(JSON.stringify)と同じく、ASCII の外の字を escape せずに書く(U+2028・U+2029・U+0085 も生のまま)。
  (+ (json.dumps record :ensure-ascii False) "\n"))

(defn assistant [message-id model usage at [content None] [stop-reason None]]
  ;; 実物(CLI 2.1.292 の subagent の transcript)と同じ形の assistant の行。stop-reason = message.stop_reason(実物は呼びの終わりの
  ;; 後に書く行で end_turn・tool_use などを名乗る)。
  {"type" "assistant" "isSidechain" True "agentId" "a1" "timestamp" at
   "message" {"id" message-id "model" model "role" "assistant" "content" (or content [{"type" "text" "text" "x"}])
              "stop_reason" stop-reason "usage" usage}})

(val FIRST-USAGE {"input_tokens" 2 "cache_creation_input_tokens" 23566 "cache_read_input_tokens" 0 "output_tokens" 5})
(val FINAL-USAGE {"input_tokens" 2 "cache_creation_input_tokens" 23566 "cache_read_input_tokens" 0 "output_tokens" 209})
(val OTHER-USAGE {"input_tokens" 3 "cache_creation_input_tokens" 10 "cache_read_input_tokens" 23566 "output_tokens" 40})

(defn read-of [#^ bytes data [answered False] [result None]]
  (run (subagent-transcript-of "a1" "toolu_parent" data answered result)))


(deftest test-the-calls-of-a-subagent-transcript-are-folded-per-api-call
  ;; 1 つの API の呼びは content の block ごとに同じ message.id の行を書き、出力の token は呼びの途中の値(実測 — zeus の本番の
  ;; transcript の 1 本目は output 5・後の行は 209)。呼びごとに 1 つへ畳み、usage と model は最後の行・刻は最初の行の物にする。
  ;; CLI が呼ばずに書いた行(model <synthetic>)・usage の無い行・assistant でない行は呼びに数えない。書かれた順を保つ。
  (val data (.encode (.join "" [(line-of {"type" "user" "timestamp" AT "message" {"role" "user" "content" "task"}})
                                (line-of (assistant "msg_1" "claude-opus-5-5" FIRST-USAGE AT))
                                (line-of (assistant "msg_1" "claude-opus-5-5" FINAL-USAGE LATER))
                                (line-of {"type" "attachment" "timestamp" LATER})
                                (line-of (assistant "msg_synthetic" "<synthetic>" {"input_tokens" 0 "output_tokens" 0} LATER))
                                (line-of (assistant "msg_2" "claude-haiku-5-5" OTHER-USAGE LATER))])
                     "utf-8"))
  (val read (read-of data))
  (assert (is read.problem None) (repr read))
  (assert (= #(read.agent-id read.parent-tool-use-id) #("a1" "toolu_parent")) (repr read))
  (assert (= (lfor call read.calls #(call.call-id call.model call.at))
             [#("msg_1" "claude-opus-5-5" (datetime.fromisoformat AT))
              #("msg_2" "claude-haiku-5-5" (datetime.fromisoformat LATER))])
          (repr read.calls))
  (val first (get read.calls 0))
  (assert (= #(first.usage.input-tokens first.usage.output-tokens first.usage.cache-creation-input-tokens
               first.usage.cache-read-input-tokens)
             #(2 209 23566 0))
          (repr first))
  (assert (is-not first.at.tzinfo None) (repr first)))

(deftest test-a-subagent-call-names-its-request-id
  ;; card ki-b99ace070bf0: transcript の行は requestId(API の呼びの要求の id — CLI の telemetry の api_request の request_id と
  ;; 同じ値)を持つ。呼びに畳んでも残す。無い行の呼びは None。
  (val data (.encode (.join "" [(line-of (| (assistant "msg_1" "claude-opus-5-5" FIRST-USAGE AT) {"requestId" "req_1"}))
                                (line-of (| (assistant "msg_1" "claude-opus-5-5" FINAL-USAGE LATER) {"requestId" "req_1"}))
                                (line-of (assistant "msg_2" "claude-haiku-5-5" OTHER-USAGE LATER))])
                     "utf-8"))
  (val read (read-of data))
  (assert (= (lfor call read.calls #(call.call-id call.request-id)) [#("msg_1" "req_1") #("msg_2" None)]) (repr read.calls)))

(deftest test-a-fork-transcript-does-not-count-the-copied-main-call-that-started-it
  ;; 本番の形(CLI 2.1.306・2026-10-24 の session 04dbe41b — 1 つの本体の呼び req_011CgMYFv… で 3 つの fork を起こした): fork の
  ;; transcript は fork-context-ref の行の次に、本体の呼びの自分の tool_use の block の行(parentUuid null・本体の requestId)を写して書く。
  ;; telemetry の api_request はその request_id を本体(query_source sdk)の 1 回と数えるので、subagent の呼びに数えない。失敗ケース =
  ;; 変更前は本体の呼びを fork ごとに subagent の呼びとして数え、本体の記録と合わせて 4 度数えた。fork の呼びは全部残す。
  (val copied (| (assistant "msg_main" "claude-opus-5-5" OTHER-USAGE AT
                            :content [{"type" "tool_use" "id" "toolu_parent" "name" "Agent" "input" {"subagent_type" "fork"}}]
                            :stop-reason "tool_use")
                 {"requestId" "req_main" "parentUuid" None}))
  (val data (.encode (.join "" [(line-of {"type" "fork-context-ref" "agentId" "a1" "parentSessionId" "s1" "contextLength" 475})
                                (line-of copied)
                                (line-of {"type" "user" "timestamp" AT
                                          "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" "toolu_parent"
                                                                              "content" "fork started"}]}})
                                (line-of (| (assistant "msg_1" "claude-opus-5-5" FINAL-USAGE LATER :stop-reason "end_turn")
                                            {"requestId" "req_1"}))])
                     "utf-8"))
  (val read (read-of data True))
  (assert (= (lfor call read.calls call.request-id) ["req_1"]) (repr read.calls))
  (assert (is read.problem None) (repr read))
  ;; 外すのは fork-context-ref で始まる読みの、最初の user の行より前の行だけ — fork-context-ref で始まらない読み(前の読みの続き・
  ;; fork でない subagent)の呼びは、道具の呼びを持っていても残す。
  (val own (| (assistant "msg_2" "claude-opus-5-5" OTHER-USAGE AT
                         :content [{"type" "tool_use" "id" "toolu_parent" "name" "Bash" "input" {}}] :stop-reason "tool_use")
              {"requestId" "req_2"}))
  (assert (= (lfor call (. (read-of (.encode (line-of own) "utf-8")) calls) call.request-id) ["req_2"])))

(deftest test-a-fork-continued-by-send-message-does-not-count-the-copied-main-call
  ;; 本番の形(CLI 2.1.306・2026-10-25 の session e2729ddb の agent abc33f21e894a978f・ae5cacf2a03239b31 — card ki-603188dbe54d): 道具
  ;; Agent の呼び toolu_01L9NGY… で起こした fork を、本体が道具 SendMessage の呼び toolu_01UgfKk… で続けた。終わりの報せは続けた道具の
  ;; 呼びの id を名乗るので、読み手の親の道具の呼びの id(read-of の toolu_parent)は、頭の写しの tool_use の id(toolu_started)と
  ;; 合わない。失敗ケース = 変更前は道具の呼びの id で写しを見分けたので、続けた fork の写し(本体の呼び req_main)を subagent の呼びに
  ;; 数え、turn の呼びの見積りが CLI の額を越えた(2 つの fork で 0.3709 USD)。
  (val copied (| (assistant "msg_main" "claude-opus-5-5" OTHER-USAGE AT
                            :content [{"type" "tool_use" "id" "toolu_started" "name" "Agent" "input" {"subagent_type" "fork"}}]
                            :stop-reason "tool_use")
                 {"requestId" "req_main" "parentUuid" None}))
  (val data (.encode (.join "" [(line-of {"type" "fork-context-ref" "agentId" "a1" "parentSessionId" "s1" "contextLength" 388})
                                (line-of copied)
                                (line-of {"type" "user" "timestamp" AT
                                          "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" "toolu_started"
                                                                              "content" "fork started"}]}})
                                (line-of (| (assistant "msg_1" "claude-opus-5-5" FIRST-USAGE AT :stop-reason "end_turn")
                                            {"requestId" "req_1"}))
                                (line-of {"type" "user" "timestamp" LATER "message" {"role" "user" "content" "continue"}})
                                (line-of (| (assistant "msg_2" "claude-opus-5-5" FINAL-USAGE LATER :stop-reason "end_turn")
                                            {"requestId" "req_2"}))])
                     "utf-8"))
  (val read (read-of data True))
  (assert (= (lfor call read.calls call.request-id) ["req_1" "req_2"]) (repr read.calls))
  (assert (is read.problem None) (repr read)))

(deftest test-a-call-with-no-closed-line-is-named-as-a-mid-call-output
  ;; 本番の形(CLI 2.1.306・2026-10-24 の session 2dd12560 の agent aa9ff76941f4f2afe の写し — card ki-edb2b5947d6a): 道具の呼びを
  ;; 2 つ並べた途中の呼び req_011CgMywu8G1HrbmLVkxTaog は、thinking と 2 つの tool_use の block の行(transcript の行 86・87・89)を
  ;; どれも stop_reason null・出力 6 のまま書き、message_delta の値(stop_reason tool_use・最終の出力 441 — OTel の api_request の値)を
  ;; 載せた行が来なかった。読み手はその呼びを「出力が途中の値」と名指す(上の層が telemetry の同じ request_id の値で記録する)。閉じた
  ;; 行で終わる呼び(最後の行が stop_reason を名乗る)と、道具 Agent の結果の最終の usage で直した最後の呼びは名指さない。失敗ケース =
  ;; 変更前の読み手は印を持たず、出力 6 を最終の値として記録した(OTel より 435 token 少ない)。
  (val partial {"input_tokens" 1 "cache_creation_input_tokens" 2310 "cache_read_input_tokens" 48211 "output_tokens" 6})
  (val open-lines (lfor #(block at) [#({"type" "thinking" "thinking" "t"} AT)
                                     #({"type" "tool_use" "id" "toolu_a" "name" "Read" "input" {}} AT)
                                     #({"type" "tool_use" "id" "toolu_b" "name" "Grep" "input" {}} LATER)]
                        (line-of (| (assistant "msg_open" "claude-opus-5-5" partial at [block])
                                    {"requestId" "req_011CgMywu8G1HrbmLVkxTaog"}))))
  (val answers (lfor tool-use-id ["toolu_a" "toolu_b"]
                     (line-of {"type" "user" "timestamp" LATER
                               "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" tool-use-id "content" "x"}]}})))
  (val closed-usage {"input_tokens" 1 "cache_creation_input_tokens" 900 "cache_read_input_tokens" 50521 "output_tokens" 120})
  (val data (.encode (.join "" [(get open-lines 0) (get open-lines 1) (get answers 0) (get open-lines 2) (get answers 1)
                                (line-of (| (assistant "msg_closed" "claude-opus-5-5" (| closed-usage {"output_tokens" 3}) LATER)
                                            {"requestId" "req_closed"}))
                                (line-of (| (assistant "msg_closed" "claude-opus-5-5" closed-usage LATER :stop-reason "end_turn")
                                            {"requestId" "req_closed"}))])
                     "utf-8"))
  (val read (read-of data True))
  (assert (is read.problem None) (repr read))
  (assert (= (lfor call read.calls #(call.request-id call.usage.output-tokens call.output-partial))
             [#("req_011CgMywu8G1HrbmLVkxTaog" 6 True) #("req_closed" 120 False)])
          (repr read.calls))
  ;; 名指すのは出力だけ — 入力の側 3 種は transcript の値のまま(最終の値と同じ)。
  (val named (get read.calls 0))
  (assert (= #(named.usage.input-tokens named.usage.cache-creation-input-tokens named.usage.cache-read-input-tokens) #(1 2310 48211))
          (repr named))
  ;; 最後の呼びが閉じた行を持たなくても、道具 Agent の結果の名乗る最終の usage で直した呼びは途中の値ではない。
  (val last-open (.encode (line-of (| (assistant "msg_last" "claude-opus-5-5" partial AT [{"type" "text" "text" "report"}])
                                      {"requestId" "req_last"}))
                          "utf-8"))
  (val reported (read-of last-open True (AgentToolResult :agent-id "a1" :status "completed" :usage (usage-of (| partial {"output_tokens" 441})))))
  (assert (= (lfor call reported.calls #(call.usage.output-tokens call.output-partial)) [#(441 False)]) (repr reported.calls))
  (assert (= (lfor call (. (read-of last-open True) calls) call.output-partial) [True])))

(deftest test-lines-without-a-call-id-are-each-their-own-call
  ;; message.id の無い行は畳まない(どの呼びと同じか分からない — 1 行を 1 回と数える)。
  (val bare (assistant "" "m" OTHER-USAGE AT))
  (val read (read-of (.encode (+ (line-of bare) (line-of bare)) "utf-8")))
  (assert (= (lfor call read.calls call.call-id) [None None]) (repr read)))

(deftest test-a-finished-subagent-transcript-is-whole-when-its-last-call-is-closed
  ;; 答え終えた subagent(報せの status completed)の transcript は、最後の呼びが答えを閉じた(stop_reason が end_turn など)所で書き
  ;; 切った読み — 報せの行の total_tokens とは比べない(card ki-3d45f5857239)。最後の呼びが道具の答えを待つ(tool_use)・stop_reason を
  ;; 名乗らない・呼びがまだ無い読みは TRUNCATED(CLI は報せの後に書き足す)で、それでも読めた呼びは載せる(捨てて 0 にしない)。
  (val first-call (line-of (assistant "msg_1" "m" FINAL-USAGE AT :stop-reason "tool_use")))
  (val last-call (line-of (assistant "msg_2" "m" OTHER-USAGE LATER :stop-reason "end_turn")))
  (val whole (read-of (.encode (+ first-call last-call) "utf-8") True))
  (assert (= #((lfor call whole.calls call.call-id) whole.problem whole.detail) #(["msg_1" "msg_2"] None "")) (repr whole))
  (val waiting (read-of (.encode first-call "utf-8") True))
  (assert (= #((lfor call waiting.calls call.call-id) waiting.problem) #(["msg_1"] SubagentTranscriptProblem.TRUNCATED))
          (repr waiting))
  (assert (in "closed call" waiting.detail) (repr waiting))
  (val unnamed (read-of (.encode (line-of (assistant "msg_1" "m" FINAL-USAGE AT)) "utf-8") True))
  (assert (= #((len unnamed.calls) unnamed.problem) #(1 SubagentTranscriptProblem.TRUNCATED)) (repr unnamed))
  (val nothing-yet (read-of b"" True))
  (assert (= #(nothing-yet.calls nothing-yet.problem) #(#() SubagentTranscriptProblem.TRUNCATED)) (repr nothing-yet))
  ;; CLI が API の誤りで閉じた答え(<synthetic> の行・stop_sequence)も閉じた呼び(呼びには数えない)。
  (val synthetic (line-of (assistant "msg_s" "<synthetic>" {"input_tokens" 0 "output_tokens" 0} LATER :stop-reason "stop_sequence")))
  (val failed (read-of (.encode (+ first-call synthetic) "utf-8") True))
  (assert (= #((len failed.calls) failed.problem) #(1 None)) (repr failed))
  ;; 答えずに終わった task・報せの前に降りた process の読み(answered 偽)は、閉じていなくても書き手が止めた所までが全部。
  (val stopped (read-of (.encode first-call "utf-8")))
  (assert (= #((len stopped.calls) stopped.problem) #(1 None)) (repr stopped)))

(deftest test-a-whole-transcript-keeps-every-call-whatever-total-the-notification-reported
  ;; 本番の実測の形(2026-10-22 16:24 UTC・session e088fc87…・aj-GWH568QK64FWFDB9G08RG51HFK — 書き切った transcript の最後の行の usage は
  ;; OTel の api_request と一致したのに、報せの total_tokens は最後の呼びの和より +251〜+1,801 多かった): 読みは報せの値を受けないので、
  ;; 道具を挟んで呼ぶ subagent の呼びを全部載せる。失敗ケース = 変更前は報せの値と和が合わず TRUNCATED で呼びを全部捨てた。
  (val usages (lfor n (range 1 14) {"input_tokens" n "cache_creation_input_tokens" (* 100 n) "cache_read_input_tokens" (* 20000 n)
                                    "output_tokens" (* 7 n)}))
  (val lines (lfor #(index usage) (enumerate usages)
                   (line-of (assistant (.format "msg_{}" index) "claude-sonnet-5-5" usage AT
                                       :stop-reason (if (= index (- (len usages) 1)) "end_turn" "tool_use")))))
  (val read (read-of (.encode (.join "" lines) "utf-8") True))
  (assert (= #((len read.calls) read.problem) #(13 None)) (repr read))
  (assert (= (lfor call read.calls call.usage.cache-read-input-tokens) (lfor usage usages (get usage "cache_read_input_tokens")))
          (repr read.calls)))

(defn agent-result [usage [status "completed"]]
  ;; 道具 Agent の結果(user の行の tool_use_result を読んだ物)。
  (AgentToolResult :agent-id "a1" :status status :usage (usage-of usage)))

(deftest test-a-finished-answer-written-without-a-stop-reason-is-whole-by-the-agent-tool-result
  ;; 本番の形 (A)(CLI 2.1.292・10-23 の agent-adfb8e3a4fee8440a): 答え終えた subagent の最後の答えの呼び(thinking と text の 2 行)は
  ;; stop_reason null で、出力の token は途中の値 3 のまま書かれ、最終の usage(出力 10,060)は道具 Agent の結果にだけ在る。結果の
  ;; 名乗る最後の呼び(入力の側 3 種が同じ)で終わる読みは書き切った読みで、最後の呼びの usage を結果の物にする。失敗ケース = 変更前は
  ;; TRUNCATED(log「claude CLI の subagent の transcript の読み」の truncated・5 件中 2 件)で、出力を 3 と記録した。
  (val tool-call (line-of (assistant "msg_1" "claude-opus-5-5" {"input_tokens" 1 "cache_creation_input_tokens" 900
                                                                "cache_read_input_tokens" 150000 "output_tokens" 400}
                                     AT [{"type" "tool_use" "id" "toolu_x" "name" "Read" "input" {}}] "tool_use")))
  (val partial {"input_tokens" 2 "cache_creation_input_tokens" 1439 "cache_read_input_tokens" 161179 "output_tokens" 3})
  (val answer (+ (line-of (assistant "msg_2" "claude-opus-5-5" partial LATER [{"type" "thinking" "thinking" "t"}]))
                 (line-of (assistant "msg_2" "claude-opus-5-5" partial LATER [{"type" "text" "text" "report"}]))))
  (val data (.encode (+ tool-call answer) "utf-8"))
  (val reported (agent-result (| partial {"output_tokens" 10060})))
  (val read (read-of data True reported))
  (assert (= #((lfor call read.calls call.call-id) read.problem read.detail) #(["msg_1" "msg_2"] None "")) (repr read))
  (assert (= (lfor call read.calls call.usage.output-tokens) [400 10060]) (repr read.calls))
  (assert (= (. (get read.calls -1) model) "claude-opus-5-5") (repr read.calls))
  ;; 結果が無ければ(結果の前の読み)前と同じく stop_reason で判じる。
  (val unreported (read-of data True))
  (assert (= #((lfor call unreported.calls call.usage.output-tokens) unreported.problem) #([400 3] SubagentTranscriptProblem.TRUNCATED))
          (repr unreported))
  ;; 最後の呼びの行がまだ書かれていない読み(入力の側が結果と合わない)は TRUNCATED のまま(書き手を待つ)で、usage を直さない。
  (val early (read-of (.encode tool-call "utf-8") True reported))
  (assert (= #((lfor call early.calls call.usage.output-tokens) early.problem) #([400] SubagentTranscriptProblem.TRUNCATED))
          (repr early))
  (assert (in "Agent tool result" early.detail) (repr early))
  ;; 答え終えていない結果(status が completed でない)は書き切りの印にしない。
  (val killed (read-of data True (agent-result (| partial {"output_tokens" 10060}) "killed")))
  (assert (= killed.problem SubagentTranscriptProblem.TRUNCATED) (repr killed)))

(deftest test-a-subagent-stopped-by-a-hook-after-a-tool-call-is-whole-by-the-agent-tool-result
  ;; 本番の形 (B)(10-23 の agent-ad04402d3c4863166 ほか 3 件): 子の Bash の後に PostToolUse の hook が続きを止め、transcript は
  ;; stop_reason tool_use の呼びと hook の attachment の行で終わる。道具 Agent の結果は completed で、usage は最後の呼びと同じ。書き
  ;; 切った読み。失敗ケース = 変更前は TRUNCATED。
  (val usage {"input_tokens" 2 "cache_creation_input_tokens" 767 "cache_read_input_tokens" 102555 "output_tokens" 265})
  (val data (.encode (+ (line-of (assistant "msg_1" "claude-opus-5-5" usage AT [{"type" "tool_use" "id" "toolu_b" "name" "Bash"
                                                                                  "input" {}}] "tool_use"))
                        (line-of {"type" "user" "timestamp" LATER "message" {"role" "user" "content" [{"type" "tool_result"
                                                                                                         "tool_use_id" "toolu_b"
                                                                                                         "content" "x"}]}})
                        (line-of {"type" "attachment" "timestamp" LATER
                                  "attachment" {"type" "hook_stopped_continuation" "hookName" "PostToolUse:Bash"}}))
                     "utf-8"))
  (val read (read-of data True (agent-result usage)))
  (assert (= #((lfor call read.calls call.usage.output-tokens) read.problem) #([265] None)) (repr read))
  (assert (= (. (read-of data True) problem) SubagentTranscriptProblem.TRUNCATED)))

(deftest test-the-agent-tool-result-line-names-the-subagent-and-its-final-usage
  ;; 実物(CLI 2.1.306・偽の API へ向けた実測)の道具 Agent の結果の行: 最上位の tool_use_result が subagent の task の id・終わり方・
  ;; 最後の呼びの最終の usage を名乗る。ほかの道具の結果(tool_use_result が文字列・agentId が無い)は agent-result が None。
  (val line {"type" "user" "parent_tool_use_id" None
             "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" "toolu_fake_agent"
                                                   "content" [{"type" "text" "text" "sub done"}]}]}
             "tool_use_result" {"status" "completed" "agentId" "a24f7431470b9215b" "agentType" "general-purpose" "totalTokens" 1133
                                "usage" {"input_tokens" 102 "cache_creation_input_tokens" 20 "cache_read_input_tokens" 1002
                                         "output_tokens" 9 "cache_creation" {"ephemeral_1h_input_tokens" 0
                                                                              "ephemeral_5m_input_tokens" 0}}}})
  (val kind (classify-record line))
  (assert (isinstance kind ToolResult) (repr kind))
  (val result kind.agent-result)
  (assert (= #(result.agent-id result.status result.usage.output-tokens result.usage.cache-read-input-tokens)
             #("a24f7431470b9215b" "completed" 9 1002))
          (repr result))
  (val bash (classify-record (| line {"tool_use_result" {"stdout" "x" "stderr" ""}})))
  (assert (is bash.agent-result None) (repr bash))
  (assert (is (. (classify-record (| line {"tool_use_result" "Error: x"})) agent-result) None)))

(deftest test-a-line-with-a-line-separator-inside-a-json-string-is-one-line
  ;; JSON の文字列の中に生の U+2028・U+2029・U+0085(str.splitlines が割る字 — 実物の CLI は escape しない)を持つ行も 1 行として読む
  ;; (失敗ケース = 変更前は splitlines で割れて「a line of the transcript is not a JSON object」の UNREADABLE になり、呼びを全部捨てた —
  ;; 本番 agent-a20aa06… の U+2028 4 つ)。
  (val web [{"type" "text" "text" "web の本文 次の段 終わり\u0085"}])
  (val data (.encode (+ (line-of {"type" "user" "timestamp" AT "message" {"role" "user" "content" web}})
                        (line-of (assistant "msg_1" "m" FINAL-USAGE AT web "end_turn")))
                     "utf-8"))
  (assert (in (.encode " " "utf-8") data))
  (val read (read-of data True))
  (assert (= #((lfor call read.calls call.call-id) read.problem) #(["msg_1"] None)) (repr read))
  ;; 割りの 1 か所(jsonl-lines)は改行だけで割り、空白だけの行を落とす。本体の transcript(recorded-cost の読み)も同じ割り。
  (val text (.decode data "utf-8"))
  (assert (= (lfor line (run (jsonl-lines (+ text "\n  \n"))) (get (json.loads line) "type")) ["user" "assistant"]))
  (val main (+ text (line-of {"type" "cost-state" "totalCostUSD" 1.5 "note" "a b"})))
  (assert (= (. (run (recorded-cost main True)) total-usd) 1.5)))

(deftest test-the-reader-stops-at-the-end-of-the-last-whole-line
  ;; 読み終えた所は改行で閉じた行の終わり(書きの途中の行は次の読みで頭から読む — 載せた呼びを 2 度数えない)。
  (assert (= (lfor data [b"" b"ab" b"ab\n" b"ab\ncd" (.encode "あ\nい" "utf-8")] (run (whole-lines-end data))) [0 0 3 3 4])))

(deftest test-a-read-with-a-gap-keeps-the-calls-it-read-and-names-the-gap
  ;; 失敗ケース: 最後の行が途中で切れた・途中の行が JSON でない・UTF-8 でない・呼びの行が時差つきの刻を名乗らない transcript も、
  ;; 読めた行の呼びは載せ(捨てて 0 にしない — card ki-3d45f5857239)、欠けの訳と手がかりを添える。読めない行の呼びは発明しない。
  (val good (line-of (assistant "msg_1" "m" FINAL-USAGE AT)))
  (val other (line-of (assistant "msg_2" "m" OTHER-USAGE LATER)))
  (val cases [#((.encode (+ good (.rstrip other "\n")) "utf-8") SubagentTranscriptProblem.TRUNCATED ["msg_1"])
              #((.encode (+ good "{\"type\": \"assistant\", \"cut\n" other) "utf-8") SubagentTranscriptProblem.UNREADABLE ["msg_1" "msg_2"])
              #((+ (.encode good "utf-8") b"\xff\xfe\n" (.encode other "utf-8")) SubagentTranscriptProblem.UNREADABLE ["msg_1" "msg_2"])
              #((.encode (+ good (line-of (assistant "msg_2" "m" FINAL-USAGE "2026-10-21T09:34:23"))) "utf-8")
                SubagentTranscriptProblem.UNREADABLE ["msg_1"])
              #((.encode (line-of (assistant "msg_1" "m" FINAL-USAGE "")) "utf-8") SubagentTranscriptProblem.UNREADABLE [])])
  (for [#(data problem ids) cases]
    (val read (read-of data))
    (assert (= #((lfor call read.calls call.call-id) read.problem) #(ids problem)) (repr #(data read)))
    (assert read.detail (repr read))))

(deftest test-an-empty-read-has-no-calls-and-no-problem
  ;; 前の報せの後に伸びた分が無い読み(続きの報せ)は、呼びの無い読み(読めなかった事ではない)。
  (val read (read-of b""))
  (assert (= #(read.calls read.problem) #(#() None)) (repr read)))

(deftest test-the-task-lines-name-the-subagent-and-its-parent-call
  ;; 実物(CLI 2.1.292・本番と同じ引数で偽の API へ向けた実測)の subagent の task の始まりと報せの行: task の id(= transcript の file の
  ;; 名の id)・親の道具の呼びの id・始まりの task_type local_agent を読む。
  (val started (classify-record {"type" "system" "subtype" "task_started" "task_id" "a6f7c8296c8db9f1e" "tool_use_id" "toolu_main1"
                                 "description" "probe" "subagent_type" "general-purpose" "is_backgrounded" False "spawn_depth" 1
                                 "task_type" "local_agent" "prompt" "say hi"}))
  (assert (= started (TaskEvent "a6f7c8296c8db9f1e" "started" :tool-use-id "toolu_main1" :task-type "local_agent")) (repr started))
  (val notified (classify-record {"type" "system" "subtype" "task_notification" "task_id" "a6f7c8296c8db9f1e"
                                  "tool_use_id" "toolu_main1" "status" "completed" "summary" "hi"
                                  "usage" {"total_tokens" 1133 "tool_uses" 0 "duration_ms" 156}}))
  (assert (= notified (TaskEvent "a6f7c8296c8db9f1e" "completed" :tool-use-id "toolu_main1" :total-tokens 1133)) (repr notified)))


;; --- 本番の handler(替え玉の CLI) ------------------------------------------------------------------

(val STUB-COMMAND #(sys.executable "-m" "hy" STUB-PATH))

(defn spec-with [tmp-path [shape None]]
  ;; 替え玉の CLI の会話の宣言。shape = 替え玉に書かせる読めない transcript の形(env STUB_CLI_SUBAGENT_TRANSCRIPT・None = 読める)。
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv env (| (child-env "") (if (is shape None) {} {"STUB_CLI_SUBAGENT_TRANSCRIPT" shape})))
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) env) :cwd (str work) :settings {"disableAllHooks" True}))

(defn host-of []
  (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) 8 7200.0 :launch-timeout 30.0))

(defk tool-turn [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "道具の途中に subagent を 1 つ走らせる手番を最後まで読むため。"
  (<- started (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) spec (TurnInput (sleep-prompt 1 "TOOLED") (str (uuid.uuid4)))))
  (<- done (read-to-end started.turn 30.0))
  done)

(defn subagent-lines [#^ TurnRecord record]
  (lfor line record.lines :if (isinstance line.kind SubagentTranscript) line))


(deftest test-a-subagent-call-is-read-from-its-transcript-before-the-turn-result [tmp-path]
  ;; 替え玉の CLI は本番の CLI と同じく subagent の呼びの行を stdout に出さず、transcript に書く。handler は subagent の task の報せの
  ;; 後の CLI の手番の終わり(result の行)の直前に SubagentTranscript の行を 1 つ足し、その呼び(実 model・4 種 token — 出力は最後の
  ;; 値・呼びの id・親の道具の呼び・呼びの刻)を運ぶ。失敗ケース = 変更前は subagent の呼びがどの行にも無い。替え玉の報せの total_tokens
  ;; は本番の実測の形で最後の呼びの和より多く(+1,801)、答えの本文は JSON の文字列の中に生の U+2028 を持つ — どちらでも呼びを捨てない
  ;; (失敗ケース = card ki-3d45f5857239 の前は、報せの値と合わない TRUNCATED・U+2028 で割れた UNREADABLE で呼びを全部捨てた)。替え玉の
  ;; transcript は本番の CLI 2.1.292 と同じく最後の答えの呼びを stop_reason null・出力 3 で書き、最終の出力 900 は道具の結果の行の
  ;; tool_use_result にだけ在る — handler はそれで書き切りを判じ、出力を 900 で運ぶ(失敗ケース = stop_reason だけで判じた版は、
  ;; 書き切った transcript を TRUNCATED と誤り、出力を 3 で運んだ — 本番 10-23 の 5 件)。
  (<- done (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler (host-of))] (tool-turn (spec-with tmp-path))))
  (assert (isinstance done.end Completed) (repr done.end))
  (val found (subagent-lines done))
  (assert (= (len found) 1) (repr done.lines))
  (val line (get found 0))
  (val kinds (lfor other done.lines other.kind))
  (val at (.index kinds line.kind))
  (assert (isinstance (get kinds (+ at 1)) TurnResult) (repr kinds))
  (val notified (lfor #(index kind) (enumerate kinds) :if (and (isinstance kind TaskEvent) (= kind.status "completed")) index))
  (assert (and notified (< (get notified 0) at)) (repr kinds))
  (assert (> (. (get kinds (get notified 0)) total-tokens) (+ 1 0 50000 900)) (repr kinds))
  (assert (any (gfor kind (cut kinds 0 at) (isinstance kind ToolResult))) (repr kinds))
  (val transcript line.kind)
  (assert (= #(transcript.parent-tool-use-id transcript.problem line.raw) #("toolu_stub" None "")) (repr transcript))
  (assert (= (len transcript.calls) 1) (repr transcript))
  (val call (get transcript.calls 0))
  (assert (= #(call.model call.usage.input-tokens call.usage.output-tokens call.usage.cache-creation-input-tokens
               call.usage.cache-read-input-tokens)
             #("claude-stub-sub" 1 900 0 50000))
          (repr call))
  (assert (.startswith call.call-id "msg_stub_sub_") (repr call))
  (assert (= call.at.tzinfo timezone.utc) (repr call)))


(deftest test-a-subagent-transcript-with-a-gap-carries-the-calls-it-read-and-leaves-a-log-line [tmp-path]
  ;; 失敗ケース: transcript が無い・途中で切れた・途中の行が壊れた時は、読めない行の呼びを発明せず、読めた行の呼びは載せ(捨てて 0 に
  ;; しない — card ki-3d45f5857239)、訳を行に載せ、上の層へ渡す所で log の行(SUBAGENT-TRANSCRIPT-LOG・event unread)に訳と手がかりと
  ;; 載せた呼びの数を残す。無い・切れた transcript は CLI が書き終えるのを待つ上限(SUBAGENT-SETTLE-SECONDS)まで読み直してから訳を
  ;; 載せる。手番そのものは成功のまま。切れた transcript の呼びは、閉じた行(呼びの途中の出力 3)の値で載る。
  (for [#(shape problem outputs) [#("absent" SubagentTranscriptProblem.ABSENT [])
                                  #("truncated" SubagentTranscriptProblem.TRUNCATED [3])
                                  #("broken" SubagentTranscriptProblem.UNREADABLE [900])]]
    (val case-path (/ tmp-path shape))
    (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler (host-of))]
                (Listen (tool-turn (spec-with case-path shape)) :types #(SlogEffect))))
    (val done (get heard 0))
    (assert (isinstance done.end Completed) (repr done.end))
    (val found (subagent-lines done))
    (assert (= (lfor line found #((lfor call line.kind.calls call.usage.output-tokens) line.kind.problem)) [#(outputs problem)])
            (repr found))
    (val logged (lfor effect (get heard 1) :if (= effect.msg SUBAGENT-TRANSCRIPT-LOG) effect.kwargs))
    (assert (= (lfor kwargs logged #((.get kwargs "event") (.get kwargs "problem") (.get kwargs "calls") (.get kwargs "parent_tool_use_id")))
               [#("unread" problem.value (len outputs) "toolu_stub")])
            (repr logged))
    (assert (.get (get logged 0) "detail") (repr logged))))


(defk dropped-mid-subagent [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "subagent を走らせている道具の途中(task の報せの前)で process を SIGKILL で消し、手番を最後まで読むため。"
  (val sid (str (uuid.uuid4)))
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- (ClaudeDropProcess sid))
  (<- lost (read-to-end started.turn 30.0))
  lost)


(deftest test-a-subagent-whose-process-went-down-before-the-notification-is-read-at-the-exit [tmp-path]
  ;; 報せの行の前に process が降りた subagent も、その transcript を手番の終わりの前に読む(降りる前に subagent が使った分も記録に
  ;; 残す)。
  (<- lost (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler (host-of))]
             (dropped-mid-subagent (spec-with tmp-path))))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (val found (subagent-lines lost))
  (assert (= (lfor line found #(line.kind.problem (len line.kind.calls) line.kind.parent-tool-use-id)) [#(None 1 "toolu_stub")])
          (repr lost.lines)))
