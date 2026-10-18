;; 1 回きりの print(ClaudePrintOnce — CLI の print mode を `--output-format stream-json --verbose --json-schema …` で 1 回走らせて構造化の答えを
;; 受ける)の検。
;;
;; - 純関数: argv・env・CLI の答えの JSON の読み(実物 2.1.292 の行 — 2026-10-19 の測り)と、口座の利用枠の尽きの読み(card ki-eb4f8e1db64d)。
;; - 本番の handler + 替え玉の CLI(tests/stub_cli/claude_print.py — API は撃たない): 借りた資格をその process だけの設定 dir に置いて
;;   終わったら消す・家の資格のまま走らせる・待ちの上限で止める・CLI が無い。
;; - fake の handler: 筋書きの答えで答える。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import os)
(import os.path)
(import pathlib [Path])
(import sys)
(import pytest)
(import doeff [with_handlers])
(import doeff_hy.frozen [thaw-json])
(import doeff_claude_code.values [ClaudeHome LentCredential])
(import doeff_claude_code.lines [AccountLimitHit Usage])
(import doeff_claude_code.argv [print-once-argv print-once-env])
(import doeff_claude_code.effects [ClaudePrintOnce PrintAnswered PrintFailed PrintLimited PrintTimedOut])
(import doeff_claude_code.print_once [claude-print-handler print-outcome])
(import doeff_claude_code.fake [fake-claude-print-handler])

(val STUB-PATH (str (/ (. (Path __file__) (resolve) parent) "stub_cli" "claude_print.py")))
;; -I = 呼び手の env と作業 dir を読まない・-B = bytecode を checkout に書かない(子の env は家の env ちょうどなので PYTHONDONTWRITEBYTECODE が無い)。
(val STUB #(sys.executable "-I" "-B" STUB-PATH))
(val HOME (ClaudeHome "/h/.claude" {"PATH" "/bin" "HOME" "/h"}))
;; 答えの Schema(はいの確率 1 つと、候補 2 つの重み)。
(val SCHEMA (json.dumps {"type" "object"
                         "properties" {"plain" {"type" "number"}
                                       "pick" {"type" "object"
                                               "properties" {"p0" {"type" "number"} "p1" {"type" "number"}}
                                               "required" ["p0" "p1"] "additionalProperties" False}}
                         "required" ["plain" "pick"] "additionalProperties" False}))
(val STUB-ANSWER {"plain" 0.8125 "pick" {"p0" 0.625 "p1" 0.375}})
;; 実物の CLI の答えの行(claude 2.1.292・2026-10-19 の測り — 欄は読む物だけに削った)。
(val REAL-ANSWERED {"type" "result" "subtype" "success" "is_error" False "duration_ms" 2286 "duration_api_ms" 1616 "num_turns" 2
                    "result" "{\"yes\":1}" "structured_output" {"yes" 1} "total_cost_usd" 0.00155 "terminal_reason" "completed"
                    "api_error_status" None
                    "usage" {"input_tokens" 920 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0 "output_tokens" 126}
                    "modelUsage" {"claude-haiku-4-5" {"inputTokens" 920 "outputTokens" 126 "contextWindow" 200000
                                                      "maxOutputTokens" 32000}}})
(val REAL-UNAUTHORIZED {"type" "result" "subtype" "success" "is_error" True "duration_ms" 2686 "duration_api_ms" 0
                        "result" "Failed to authenticate. API Error: 401 OAuth access token is invalid."
                        "api_error_status" 401 "terminal_reason" "api_error" "total_cost_usd" 0 "modelUsage" {}
                        "usage" {"input_tokens" 0 "output_tokens" 0}})
;; 実物の stream-json の前の行(claude 2.1.292・2026-10-19 07:51 JST の測り — 許された限度の行は答えの途中に 1 本出る。欄は読む物だけに削った)。
(val REAL-ALLOWED-LIMIT {"type" "rate_limit_event"
                         "rate_limit_info" {"status" "allowed_warning" "resetsAt" 1792886400 "rateLimitType" "seven_day" "utilization" 0.39
                                            "isUsingOverage" False}})
(val REAL-INIT {"type" "system" "subtype" "init" "model" "claude-haiku-4-5-20251001" "claude_code_version" "2.1.292" "tools" []})
;; 口座の利用枠が尽きた print の行(形の出所: 拒まれた限度の行と限度の答えは test_dialogue.hy の #3983 の見本と同じ欄・result の行の
;; api_error usage_limit_reached は CLI 2.1.292 の型の宣言 — 尽きた口座の本物の見本は手元に無い)。
(val LIMIT-TEXT "You've hit your session limit · resets 12pm (Asia/Tokyo)")
(val LIMIT-REJECTED {"type" "rate_limit_event"
                     "rate_limit_info" {"status" "rejected" "resetsAt" 1791342000 "rateLimitType" "five_hour" "overageStatus" "rejected"}})
(val LIMIT-ANSWER {"type" "assistant" "parent_tool_use_id" None "error" "rate_limit"
                   "message" {"id" "msg_l" "model" "<synthetic>" "content" [{"type" "text" "text" LIMIT-TEXT}]
                              "usage" {"input_tokens" 0 "output_tokens" 0}}})
(val LIMIT-RESULT {"type" "result" "subtype" "success" "is_error" True "result" LIMIT-TEXT "api_error_status" 429
                   "api_error" "usage_limit_reached" "terminal_reason" "api_error" "usage" {} "modelUsage" {}})
(val REAL-UNKNOWN-MODEL-STDERR "[claude-code:unrecognized_model] {\"model\":\"claude-nonexistent-9\",\"query_source\":\"sdk\"}\n")
(val REAL-UNKNOWN-MODEL {"type" "result" "subtype" "success" "is_error" True
                         "result" "There's an issue with the selected model (claude-nonexistent-9). It may not exist or you may not have access to it. Run --model to pick a different model."
                         "api_error_status" 404 "terminal_reason" "api_error" "usage" {} "modelUsage" {}})


(defn stream-of [#* records]
  "CLI の stream-json の stdout(行ごとに JSON の object 1 つ)。"
  (+ (.join "\n" (gfor record records (json.dumps record))) "\n"))


(defn request-of [#** changes]
  "検の 1 回きりの print の宣言(既定 = 資格を借りない・thinking と effort は CLI の既定)。"
  (ClaudePrintOnce #** (| {"home" HOME "model" "claude-test" "system_prompt" "Judge the material." "json_schema" SCHEMA
                           "input" "the material" "timeout_seconds" 60.0}
                          changes)))

(defn lent [#^ str token]
  (LentCredential :access-token token :expires-at 4102444800.0 :subscription-type "max"))

(defk performed [request]
  {:pre [(: request ClaudePrintOnce)] :post [(: % (| PrintAnswered PrintFailed PrintLimited PrintTimedOut))] :tags {:context "claude-code" :role "program"}}
  "宣言を 1 回出して答えを受けるため(検の Program)。"
  (<- outcome request)
  outcome)

(defn stub-home [#^ Path tmp-path #^ Path record]
  "替え玉の CLI を走らせる家: 設定 dir は検の tmp の下・env は PATH・HOME と替え玉が見た物を書く file だけ。"
  (ClaudeHome (str (/ tmp-path "home")) {"PATH" (.get os.environ "PATH" "/usr/bin:/bin") "HOME" (str tmp-path)
                                         "STUB_PRINT_RECORD" (str record)}))

(defn seen-by-stub [#^ Path record]
  (json.loads (.read-text record :encoding "utf-8")))

(defn process-gone [#^ int pid]
  (try
    (os.kill pid 0)
    (except [ProcessLookupError] (return True)))
  False)


;; --- 宣言の検め -----------------------------------------------------------------------------------

(deftest test-a-print-request-refuses-what-the-cli-cannot-take
  ;; Schema は JSON の object の文・待ちの上限は正の秒・thinking の token 数は 0 以上の整数(bool でない)・model は空でない。
  (for [changes [{"json_schema" "{\"type\":"} {"json_schema" "[1, 2]"} {"timeout_seconds" 0.0} {"timeout_seconds" -1.0}
                 {"thinking_tokens" -1} {"thinking_tokens" True} {"model" ""}]]
    (with [(pytest.raises Exception)]
      (request-of #** changes))))


;; --- 純関数 ---------------------------------------------------------------------------------------

(deftest test-the-print-once-argv
  ;; 1 回きりの print の旗(素の CLI の測り 2026-10-18 と同じ組): 道具なし・設定の源なし・家の MCP を継がない・記録を残さない。
  ;; 出力は stream-json(--verbose が要る)— 口座の利用枠の尽きを名乗る行(拒まれた rate_limit_event・error rate_limit の答え)は result の
  ;; 行の前に出て、`--output-format json` の 1 行には載らない(card ki-eb4f8e1db64d)。本文は argv に載せない(標準入力)。
  (assert (= (print-once-argv #("claude") (request-of))
             ["claude" "-p" "--output-format" "stream-json" "--verbose" "--model" "claude-test" "--system-prompt" "Judge the material."
              "--json-schema" SCHEMA "--tools" "" "--setting-sources" "" "--strict-mcp-config" "--no-session-persistence"]))
  (assert (= (cut (print-once-argv #("claude") (request-of :effort "low")) -2 None) ["--effort" "low"]))
  (assert (not-in "the material" (print-once-argv #("claude") (request-of)))))

(deftest test-the-print-once-env
  ;; 家の env ちょうど + CLAUDE_CONFIG_DIR = その process の設定 dir。thinking の token 数を宣言すれば MAX_THINKING_TOKENS(0 = 考えない)。
  ;; 借りた資格の token は env に載らない(設定 dir の .credentials.json だけが運ぶ)。
  (assert (= (print-once-env (request-of) "/h/.claude") {"PATH" "/bin" "HOME" "/h" "CLAUDE_CONFIG_DIR" "/h/.claude"}))
  (val env (print-once-env (request-of :thinking-tokens 0 :credential (lent "secret-token")) "/h/.claude/.credential-homes/x"))
  (assert (= (get env "MAX_THINKING_TOKENS") "0"))
  (assert (= (get env "CLAUDE_CONFIG_DIR") "/h/.claude/.credential-homes/x"))
  (assert (not-in "secret-token" (.values env))))

(deftest test-the-print-outcome-reads-a-structured-answer
  (<- outcome (print-outcome 0 (json.dumps REAL-ANSWERED) "" 2400 False 60.0))
  (assert (isinstance outcome PrintAnswered) outcome)
  ;; 答えの値は CLI の値のまま(丸めない)。
  (assert (= (thaw-json outcome.structured-output) {"yes" 1}))
  (assert (= outcome.usage (Usage :input-tokens 920 :output-tokens 126 :cache-creation-input-tokens 0 :cache-read-input-tokens 0)))
  (assert (= outcome.cost-usd 0.00155))
  (assert (= outcome.models #("claude-haiku-4-5")))
  (assert (= outcome.elapsed-ms 2400))
  (assert (= outcome.api-ms 1616)))

(deftest test-the-print-outcome-names-an-api-error
  (<- unauthorized (print-outcome 1 (json.dumps REAL-UNAUTHORIZED) "" 2700 False 60.0))
  (assert (= unauthorized (PrintFailed :exit-code 1 :api-status 401
                                       :detail "Failed to authenticate. API Error: 401 OAuth access token is invalid."
                                       :stderr-tail "")))
  (<- unknown (print-outcome 1 (json.dumps REAL-UNKNOWN-MODEL) REAL-UNKNOWN-MODEL-STDERR 1100 False 60.0))
  (assert (isinstance unknown PrintFailed) unknown)
  (assert (= unknown.api-status 404))
  (assert (in "unrecognized_model" unknown.stderr-tail)))

(deftest test-the-print-outcome-without-an-answer-line-is-a-failed-print
  ;; 答えの JSON を出さずに降りた CLI(実物: 壊れた --json-schema は stderr に 1 行・rc 1)。
  (<- outcome (print-outcome 1 "" "Error: --json-schema is not valid JSON: JSON Parse error: Expected ']'\n" 1400 False 60.0))
  (assert (isinstance outcome PrintFailed) outcome)
  (assert (= #(outcome.exit-code outcome.api-status) #(1 None)))
  (assert (in "not valid JSON" outcome.stderr-tail)))

(deftest test-the-print-outcome-without-a-structured-answer-is-a-failed-print
  ;; 誤りではないが構造化の答えが無い行は、答えにしない(本文の文を答えと読み替えない)。
  (val line (dfor #(key value) (.items REAL-ANSWERED) :if (!= key "structured_output") key value))
  (<- outcome (print-outcome 0 (json.dumps line) "" 2300 False 60.0))
  (assert (isinstance outcome PrintFailed) outcome)
  (assert (= #(outcome.exit-code outcome.api-status) #(0 None))))

(deftest test-the-print-outcome-past-the-wait-limit-is-a-timed-out-print
  (<- outcome (print-outcome None "" "half a line" 5000 True 5.0))
  (assert (= outcome (PrintTimedOut :seconds 5.0 :stderr-tail "half a line"))))


(deftest test-the-print-outcome-reads-the-answer-at-the-end-of-the-stream
  ;; stream-json の stdout: 答えは最後の result の行。許された限度の行(allowed・allowed_warning)は尽きではない(実物の測りの並び)。
  (<- outcome (print-outcome 0 (stream-of REAL-INIT REAL-ALLOWED-LIMIT REAL-ANSWERED) "" 2400 False 60.0))
  (assert (isinstance outcome PrintAnswered) outcome)
  (assert (= (thaw-json outcome.structured-output) {"yes" 1})))

(deftest test-the-print-outcome-names-the-exhausted-usage-limit-apart-from-other-failures
  ;; 拒まれた限度の行と限度の答え(error rate_limit)を出して降りた print は、誤りの PrintFailed でなく PrintLimited: どの枠か・戻る刻
  ;; (epoch 秒)・CLI の限度の文(上の層が「判定器の口座の枠が尽きた・いつ戻る」と言うため — card ki-eb4f8e1db64d)。
  ;; 失敗ケース(前の形): api_error_status 429 の PrintFailed になり、戻る刻を持たず、判定器に届かない時と同じ断りになる。
  (<- outcome (print-outcome 1 (stream-of REAL-INIT LIMIT-REJECTED LIMIT-ANSWER LIMIT-RESULT) "" 900 False 60.0))
  (assert (= outcome (PrintLimited :limit (AccountLimitHit :window "five_hour" :resets-at 1791342000 :text LIMIT-TEXT)
                                   :exit-code 1 :stderr-tail ""))
          outcome)
  ;; 限度の行が無く限度の答え(error rate_limit)だけでも尽き(枠と戻る刻は名乗らない — None)。
  (<- bare (print-outcome 1 (stream-of LIMIT-ANSWER LIMIT-RESULT) "" 900 False 60.0))
  (assert (= bare.limit (AccountLimitHit :window None :resets-at None :text LIMIT-TEXT)) bare)
  ;; 拒まれた限度の行が在っても、構造化の答えを返した print は答え(尽きは次の要求の事)。
  (<- answered (print-outcome 0 (stream-of LIMIT-REJECTED REAL-ANSWERED) "" 900 False 60.0))
  (assert (isinstance answered PrintAnswered) answered)
  ;; 限度を名乗らない 429 は今までどおりの誤り(資格の断りなどと同じ PrintFailed)。
  (<- throttled (print-outcome 1 (stream-of (| LIMIT-RESULT {"api_error" None "result" "API Error: 429 overloaded"})) "" 900 False 60.0))
  (assert (= #((type throttled) throttled.api-status) #(PrintFailed 429)) throttled))


;; --- 本番の handler + 替え玉の CLI ---------------------------------------------------------------------

(deftest test-the-print-handler-runs-the-cli-once-with-the-lent-credential-in-a-home-of-its-own [tmp-path]
  (val record (/ tmp-path "record.json"))
  (val request (request-of :home (stub-home tmp-path record) :input "the material to judge" :credential (lent "lent-token")
                           :thinking-tokens 0 :effort "low"))
  (<- outcome (with_handlers [(claude-print-handler STUB)] (performed request)))
  (assert (isinstance outcome PrintAnswered) outcome)
  (assert (= (thaw-json outcome.structured-output) STUB-ANSWER))
  (assert (= outcome.models #("claude-test")))
  (assert (= outcome.cost-usd 0.0125))
  (assert (= outcome.usage.input-tokens 1000))
  (assert (>= outcome.elapsed-ms 0))
  (val seen (seen-by-stub record))
  (assert (= (get seen "argv") (cut (print-once-argv #("claude") request) 1 None)))
  (assert (= (get seen "input_chars") (len "the material to judge")))
  (assert (= (get seen "max_thinking_tokens") "0"))
  ;; 借りた資格は家の設定 dir の下の、その process だけの設定 dir の .credentials.json(0600・refreshToken は null)に在った。
  (assert (.startswith (get seen "config_dir") (str (/ tmp-path "home" ".credential-homes"))) seen)
  (assert (= (get seen "credential") {"access_token" "lent-token" "refresh_token" None "expires_at" 4102444800000 "mode" 0o600}))
  ;; 終わったら、その設定 dir も、CLI を走らせた作業 dir も消える。
  (assert (not (os.path.exists (get seen "config_dir"))))
  (assert (not (os.path.exists (get seen "cwd"))))
  (assert (!= (os.path.realpath (get seen "cwd")) (os.path.realpath (os.getcwd)))))

(deftest test-the-print-handler-uses-the-home-credential-when-none-is-lent [tmp-path]
  (val record (/ tmp-path "record.json"))
  (val home (stub-home tmp-path record))
  (.mkdir (Path home.config-dir) :parents True)
  (val own (/ (Path home.config-dir) ".credentials.json"))
  (.write-text own (json.dumps {"claudeAiOauth" {"accessToken" "home-token" "refreshToken" None "expiresAt" None}}) :encoding "utf-8")
  (<- outcome (with_handlers [(claude-print-handler STUB)] (performed (request-of :home home))))
  (assert (isinstance outcome PrintAnswered) outcome)
  (val seen (seen-by-stub record))
  (assert (= (get seen "config_dir") home.config-dir))
  (assert (= (get (get seen "credential") "access_token") "home-token"))
  (assert (is (get seen "max_thinking_tokens") None))
  ;; 家の資格は家に残る(消すのは借りた資格の家だけ)。
  (assert (.exists own)))

(deftest test-the-print-handler-names-the-cli-api-error [tmp-path]
  (val record (/ tmp-path "record.json"))
  (val request (request-of :home (stub-home tmp-path record) :input "STUB:api-401" :credential (lent "dead-token")))
  (<- outcome (with_handlers [(claude-print-handler STUB)] (performed request)))
  (assert (isinstance outcome PrintFailed) outcome)
  (assert (= #(outcome.exit-code outcome.api-status) #(1 401)))
  (assert (not (os.path.exists (get (seen-by-stub record) "config_dir")))))

(deftest test-the-print-handler-names-the-exhausted-usage-limit [tmp-path]
  ;; 替え玉の CLI が尽きた口座の行(拒まれた限度の行・限度の答え・result の 429)を出すと、handler は PrintLimited で答える。
  (val record (/ tmp-path "record.json"))
  (val request (request-of :home (stub-home tmp-path record) :input "STUB:usage-limit" :credential (lent "spent-token")))
  (<- outcome (with_handlers [(claude-print-handler STUB)] (performed request)))
  (assert (isinstance outcome PrintLimited) outcome)
  (assert (= #(outcome.limit.window outcome.limit.resets-at outcome.exit-code) #("five_hour" 1791342000 1)) outcome)
  (assert (not (os.path.exists (get (seen-by-stub record) "config_dir")))))

(deftest test-the-print-handler-stops-the-cli-past-the-wait-limit [tmp-path]
  ;; 降ろされるまで待つ CLI は、待ちの上限で止めて PrintTimedOut。process は残らず、借りた資格の家も消える。
  (val record (/ tmp-path "record.json"))
  (val request (request-of :home (stub-home tmp-path record) :input "STUB:hang" :credential (lent "slow-token")
                           :timeout-seconds 5.0))
  (<- outcome (with_handlers [(claude-print-handler STUB)] (performed request)))
  (assert (isinstance outcome PrintTimedOut) outcome)
  (assert (= outcome.seconds 5.0))
  (val seen (seen-by-stub record))
  (assert (process-gone (get seen "pid")))
  (assert (not (os.path.exists (get seen "config_dir")))))

(deftest test-a-cli-that-cannot-start-is-a-failed-print [tmp-path]
  (val home (ClaudeHome (str (/ tmp-path "home")) {"PATH" "/usr/bin:/bin"}))
  (<- outcome (with_handlers [(claude-print-handler #((str (/ tmp-path "no-such-claude"))))]
                             (performed (request-of :home home :credential (lent "unused-token")))))
  (assert (isinstance outcome PrintFailed) outcome)
  (assert (is outcome.exit-code None))
  (assert (in "no-such-claude" outcome.detail))
  ;; 起動に失敗しても、借りた資格の家は残らない。
  (val homes (/ tmp-path "home" ".credential-homes"))
  (assert (or (not (.exists homes)) (= (list (.iterdir homes)) []))))


;; --- fake の handler -------------------------------------------------------------------------------

(defk echo-print [#^ ClaudePrintOnce request]
  {:pre [(: request ClaudePrintOnce)] :post [(: % PrintAnswered)] :tags {:context "claude-code" :role "program"}}
  "fake の筋書き: 宣言の model と本文をそのまま構造化の答えに写す(fake が宣言をそのまま渡した事を答えで確かめるため)。"
  (PrintAnswered :structured-output {"model" request.model "input" request.input} :usage (Usage) :cost-usd None
                 :models #("claude-fake") :elapsed-ms 0 :api-ms None))

(defk not-an-outcome [#^ ClaudePrintOnce request]
  {:pre [(: request ClaudePrintOnce)] :post [(: % dict)] :tags {:context "claude-code" :role "program"}}
  "fake の筋書きの誤り: 答えの和の外の値(構造化の答えの素の dict)を返す。"
  {"plain" 0.5})

(deftest test-the-fake-print-handler-answers-with-the-scripted-outcome
  ;; 筋書き(respond — 宣言 → 答えの Program)の答えで、CLI を起こさずに答える。
  (<- outcome (with_handlers [(fake-claude-print-handler echo-print)] (performed (request-of :thinking-tokens 0))))
  (assert (isinstance outcome PrintAnswered) outcome)
  (assert (= (thaw-json outcome.structured-output) {"model" "claude-test" "input" "the material"})))

(defk limited-print [#^ ClaudePrintOnce request]
  {:pre [(: request ClaudePrintOnce)] :post [(: % PrintLimited)] :tags {:context "claude-code" :role "program"}}
  "fake の筋書き: 口座の利用枠が尽きた print(使い手の検が尽きの筋を fake で起こすため)。"
  (PrintLimited :limit (AccountLimitHit :window "seven_day" :resets-at 1791342000 :text "limit") :exit-code 1 :stderr-tail ""))

(deftest test-the-fake-print-handler-answers-an-exhausted-usage-limit
  (<- outcome (with_handlers [(fake-claude-print-handler limited-print)] (performed (request-of))))
  (assert (= outcome.limit.window "seven_day") outcome))

(deftest test-the-fake-print-handler-refuses-an-answer-outside-the-outcomes
  (with [(pytest.raises TypeError)]
    (<- (with_handlers [(fake-claude-print-handler not-an-outcome)] (performed (request-of))))))
