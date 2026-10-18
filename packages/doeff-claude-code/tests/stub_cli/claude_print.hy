;;; claude の 1 回きりの print(`-p --output-format json --json-schema …`)の替え玉 — 本番の print の handler を API を撃たずに回すための CLI。
;;;
;;; 実物(claude 2.1.292・実測 2026-10-19)と同じ形の 1 つの JSON の object(type result)を stdout に出して降りる:
;;; - 成功 = rc 0・is_error 偽・structured_output(--json-schema の欄ごとの答え)と、その JSON の文の result・usage・modelUsage・
;;;   total_cost_usd・duration_ms・duration_api_ms。
;;; - API の誤り = rc 1・is_error 真・api_error_status(例 401)・result に誤りの文。
;;; - 設定 dir(CLAUDE_CONFIG_DIR)に .credentials.json が無い = rc 1・is_error 真・api_error_status なし(実物の「Not logged in」の形)。
;;; 答えは --json-schema の欄の型で決める: number = NUMBER-ANSWER・object = 最初の欄に FIRST-OPTION-ANSWER、残りの欄に 1 − それを等分。
;;; 標準入力の本文の印で筋を替える(印は本文のどこに在ってもよい):
;;;   STUB:api-401 = API の誤り 401 / STUB:crash = stdout に何も出さず stderr に 1 行・rc 2 / STUB:no-structured = 成功だが
;;;   structured_output が無い / STUB:out-of-range = number の欄を 1.5 で答える / STUB:hang = 降ろされるまで待つ(信号を待つ — 時間で
;;;   起きない)。
;;; env STUB_PRINT_RECORD(file の path)が在れば、見た物(argv・cwd・設定 dir・その .credentials.json の access token と権限・
;;; MAX_THINKING_TOKENS・本文の長さ・pid)を JSON で書く — 検が handler の渡し方を確かめるため。
(import json)
(import os)
(import os.path)
(import signal)
(import stat)
(import sys)

(setv NUMBER-ANSWER 0.8125)
(setv FIRST-OPTION-ANSWER 0.625)
(setv FLAGS-WITH-VALUE #{"--output-format" "--json-schema" "--model" "--system-prompt" "--tools" "--setting-sources" "--effort"})


(defn read-flags [argv]
  "argv の旗 → 値の表(値を取らない旗は True)。"
  (setv flags {} at 0)
  (while (< at (len argv))
    (setv name (get argv at))
    (if (in name FLAGS-WITH-VALUE)
        (do (setv (get flags name) (get argv (+ at 1))) (+= at 2))
        (do (setv (get flags name) True) (+= at 1))))
  flags)

(defn answer-for [schema #^ str text]
  "答えの Schema の欄ごとの答え(頭の註の規則)。"
  (setv number (if (in "STUB:out-of-range" text) 1.5 NUMBER-ANSWER))
  (dfor #(name field) (.items (.get schema "properties" {}))
        name (if (= (.get field "type") "object")
                 (do (setv options (list (.get field "properties" {})))
                     (dfor #(at option) (enumerate options)
                           option (if (= at 0) FIRST-OPTION-ANSWER (/ (- 1 FIRST-OPTION-ANSWER) (- (len options) 1)))))
                 number)))

(defn credential-seen [#^ str config-dir]
  "設定 dir の .credentials.json の access token と権限(無ければ None)。"
  (setv path (os.path.join config-dir ".credentials.json"))
  (when (not (os.path.exists path)) (return None))
  (with [stream (open path :encoding "utf-8")]
    (setv oauth (get (json.load stream) "claudeAiOauth")))
  {"access_token" (get oauth "accessToken")
   "refresh_token" (get oauth "refreshToken")
   "expires_at" (get oauth "expiresAt")
   "mode" (stat.S-IMODE (. (os.stat path) st-mode))})

(defn result-line [#^ str model #** fields]
  "実物の result の行の形(欄は実物の名のまま)。"
  (| {"type" "result" "subtype" "success" "is_error" False "num_turns" 1 "duration_ms" 12 "duration_api_ms" 7
      "total_cost_usd" 0.0 "usage" {"input_tokens" 0 "output_tokens" 0} "modelUsage" {} "result" ""}
     fields))

(defn main []
  (setv argv (cut sys.argv 1 None)
        flags (read-flags argv)
        text (.read sys.stdin)
        model (.get flags "--model" "")
        config-dir (.get os.environ "CLAUDE_CONFIG_DIR" "")
        credential (if config-dir (credential-seen config-dir) None)
        record (.get os.environ "STUB_PRINT_RECORD"))
  (when record
    (with [stream (open record "w" :encoding "utf-8")]
      (json.dump {"argv" argv "cwd" (os.getcwd) "config_dir" config-dir "credential" credential
                  "max_thinking_tokens" (.get os.environ "MAX_THINKING_TOKENS") "input_chars" (len text) "pid" (os.getpid)}
                 stream)))
  (when (in "STUB:hang" text)
    (while True (signal.pause)))
  (when (in "STUB:crash" text)
    (print "stub crash" :file sys.stderr)
    (sys.exit 2))
  (when (is credential None)
    (print (json.dumps (result-line model :is_error True :result "Not logged in · Please run /login")))
    (sys.exit 1))
  (when (in "STUB:api-401" text)
    (print (json.dumps (result-line model :is_error True :api_error_status 401
                                    :result "Failed to authenticate. API Error: 401 OAuth access token is invalid.")))
    (sys.exit 1))
  (setv answer (answer-for (json.loads (get flags "--json-schema")) text)
        usage {"input_tokens" 1000 "cache_creation_input_tokens" 200 "cache_read_input_tokens" 300 "output_tokens" 50})
  (setv line (result-line model :result (json.dumps answer) :usage usage :total_cost_usd 0.0125
                          :modelUsage {model {"inputTokens" 1000 "outputTokens" 50 "contextWindow" 200000 "maxOutputTokens" 32000}}))
  (when (not-in "STUB:no-structured" text)
    (setv (get line "structured_output") answer))
  (print (json.dumps line))
  (sys.exit 0))

(main)
