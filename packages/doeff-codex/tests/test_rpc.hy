;; app-server を起こす argv と、stdin へ書く JSON-RPC の 1 行の組み立ての検 — 組み立てた行は、本物の codex(0.162.1)が答えた録りの
;; 時に書いた行(tests/recorded/codex-0.162.1/*.stdin.jsonl)と同じ中身になる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <- var])
(import json)
(import pathlib [Path])
(import doeff_codex.rpc [app-server-argv initialize-request initialized-notification thread-start-request thread-resume-request
                         turn-start-request turn-steer-request turn-interrupt-request server-response-line ApprovalPolicy SandboxMode])
(import doeff_hy.json_value [OpaqueJson])

(val RECORDED (/ (. (Path __file__) parent) "recorded" "codex-0.162.1"))


(defk recorded-requests [#^ str name]
  {:pre [(: name str)] :post [(: % tuple)] :tags {:context "codex-test" :role "foundation"}}
  "録りの時に stdin へ書いた行を、書いた順に JSON の object の列で(組み立てた行と中身で比べるため)。"
  (var sent #())
  (for [line (.splitlines (.read-text (/ RECORDED f"{name}.stdin.jsonl") :encoding "utf-8"))]
    (when (.strip line)
      (:= sent (+ sent #((json.loads line))))))
  sent)


(deftest test-the-app-server-argv-speaks-json-rpc-on-stdio
  ;; 起こす命令は前置き(実行ファイルと前の引数)の後に app-server と stdio の待ち受け。
  (<- argv (app-server-argv #("codex")))
  (assert (= argv ["codex" "app-server" "--listen" "stdio://"]) argv)
  (<- wrapped (app-server-argv #("/opt/bin/codex" "-c" "model=\"gpt-6-astra\"")))
  (assert (= wrapped ["/opt/bin/codex" "-c" "model=\"gpt-6-astra\"" "app-server" "--listen" "stdio://"]) wrapped))


(deftest test-the-built-requests-match-the-recorded-exchange
  ;; 録りの時に書いた 5 行(初期化・初期化の済み・thread の始め・ターン 2 つ)と、組み立てた 5 行の中身が同じ。
  (<- sent (recorded-requests "two-turns"))
  (val cwd (get (get sent 2) "params" "cwd"))
  (<- init-line (initialize-request 1 "doeff-codex-recorder" "0"))
  (<- ready-line (initialized-notification))
  (<- thread-line (thread-start-request 2 cwd :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY))
  ;; 録りの行の threadId は録った時の thread の id(thread/start の答えから読んだ値)— 組み立ての側にも同じ id を渡す。
  (val thread-id (get (get sent 3) "params" "threadId"))
  (<- first-line (turn-start-request 3 thread-id "say hello" #()))
  (<- second-line (turn-start-request 4 thread-id "say hello again" #()))
  (val built (tuple (gfor line #(init-line ready-line thread-line first-line second-line) (json.loads line))))
  (assert (= built sent) #(built sent))
  (assert (= (tuple (gfor message built (.get message "method")))
             #("initialize" "initialized" "thread/start" "turn/start" "turn/start")))
  ;; 1 行は改行を含まない(stdin の 1 行 = 1 つの message)。
  (assert (not (any (gfor line #(init-line ready-line thread-line first-line second-line) (in "\n" line))))))


(deftest test-interrupt-and-resume-name-their-thread-and-turn
  ;; 止めの要求は thread とターンの id を、続きの要求は thread の id を持つ(録りの interrupt の 4 行目と同じ中身)。
  (<- sent (recorded-requests "interrupt"))
  (val recorded-interrupt (get sent 4))
  (<- interrupt-line (turn-interrupt-request 4 (get recorded-interrupt "params" "threadId") (get recorded-interrupt "params" "turnId")))
  (assert (= (json.loads interrupt-line) recorded-interrupt) interrupt-line)
  (<- resume-line (thread-resume-request 5 "t-1" "/w"))
  (assert (= (json.loads resume-line)
             {"jsonrpc" "2.0" "id" 5 "method" "thread/resume" "params" {"threadId" "t-1" "cwd" "/w"}})
          resume-line)
  ;; 新しい process で続ける時は、thread/start と同じ方針を名乗り直す。
  (<- resumed (thread-resume-request 7 "t-1" "/w" :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY
                                     :model "gpt-6-astra"))
  (assert (= (get (json.loads resumed) "params")
             {"threadId" "t-1" "cwd" "/w" "approvalPolicy" "never" "sandbox" "read-only" "model" "gpt-6-astra"})
          resumed)
  ;; model を名指した thread の始め。
  (<- named (thread-start-request 6 "/w" :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.WORKSPACE-WRITE :model "gpt-6-astra"))
  (assert (= (get (json.loads named) "params") {"cwd" "/w" "approvalPolicy" "never" "sandbox" "workspace-write" "model" "gpt-6-astra"})
          named))


(deftest test-a-server-request-is-answered-with-its-id
  ;; codex からの要求(道具の許可の問いなど)への答えは、同じ id と、上の層が渡した result の中身をそのまま持つ 1 行。
  (<- line (server-response-line 7 (OpaqueJson.of {"decision" "accept"})))
  (assert (= (json.loads line) {"jsonrpc" "2.0" "id" 7 "result" {"decision" "accept"}}) line)
  (assert (not-in "\n" line)))


(deftest test-the-declared-compaction-and-effort-ride-on-their-requests
  ;; 圧縮の閾値は thread を開く 2 つの要求の config(snake_case の鍵)に、考えの深さは turn/start の effort に載る。名乗らなければ送らない。
  (<- opened (thread-start-request 2 "/w" :auto-compact-token-limit 600000))
  (assert (= (get (json.loads opened) "params") {"cwd" "/w" "config" {"model_auto_compact_token_limit" 600000}}) opened)
  (<- resumed (thread-resume-request 3 "t-1" "/w" :auto-compact-token-limit 250000))
  (assert (= (get (json.loads resumed) "params") {"threadId" "t-1" "cwd" "/w" "config" {"model_auto_compact_token_limit" 250000}})
          resumed)
  (<- deep (turn-start-request 4 "t-1" "think" #() :effort "xhigh"))
  (assert (= (get (json.loads deep) "params") {"threadId" "t-1" "input" [{"type" "text" "text" "think"}] "effort" "xhigh"}) deep))


(deftest test-images-and-steering-build-their-input-lists
  ;; 画像は文字の後ろに image の入力(data URL)で並ぶ。画像だけの入力(空の文字)は文字の入力を送らない。
  (<- with-image (turn-start-request 5 "t-1" "look" #("data:image/png;base64,AAAA")))
  (assert (= (get (json.loads with-image) "params" "input")
             [{"type" "text" "text" "look"} {"type" "image" "url" "data:image/png;base64,AAAA"}])
          with-image)
  (<- only-image (turn-start-request 6 "t-1" "" #("data:image/png;base64,AAAA")))
  (assert (= (get (json.loads only-image) "params" "input") [{"type" "image" "url" "data:image/png;base64,AAAA"}]) only-image)
  ;; 走っているターンへの追送は、そのターンの id を expectedTurnId で名乗る。
  (<- steer (turn-steer-request 7 "t-1" "turn-9" "also this" #()))
  (assert (= (json.loads steer)
             {"jsonrpc" "2.0" "id" 7 "method" "turn/steer"
              "params" {"threadId" "t-1" "expectedTurnId" "turn-9" "input" [{"type" "text" "text" "also this"}]}})
          steer))
