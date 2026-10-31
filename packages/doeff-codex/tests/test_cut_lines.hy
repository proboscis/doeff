;; 1 行の上限を超えて頭だけを読んだ行の分類の検 — 頭(途中で切れた JSON)を閉じて分け、要求への答えは id と thread の id を名乗る記録に、
;; ほかの行は中身の欠けた記録を作らず method を名乗る Unparsed にする事。答えの形は録った実物の行(tests/recorded/codex-0.162.1 の
;; thread/start の答え — thread/resume の答えと同じ形で、thread の id が頭に・履歴 turns が thread の最後の欄に在る)に、履歴を足して作る。
;; 出自 = thread/resume の答え 1 行で turn-host の本体が 27 GB に膨らんだ事。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <-])
(import json)
(import pathlib [Path])
(import doeff_codex._lines [classify-cut-line closed-json-head Response ErrorResponse Unparsed])
(import doeff_codex._process [CUT-HEAD-BYTES])

(val RECORDED (/ (. (Path __file__) parent) "recorded" "codex-0.162.1"))
(val CUT-SIZE 300000000)


(defk recorded-answer [#^ int request-id]
  {:pre [(: request-id int)] :post [(: % dict)] :tags {:context "codex-test" :role "foundation"}}
  "録った two-turns の、その id の要求への答え(JSON の object)を引くため。"
  (val messages (lfor line (.splitlines (.read-text (/ RECORDED "two-turns.stdout.jsonl") :encoding "utf-8"))
                      :if (.strip line)
                      (json.loads line)))
  (next (gfor message messages :if (and (= (.get message "id") request-id) (in "result" message)) message)))


(defk head-of [#^ str line]
  {:pre [(: line str)] :post [(: % str)] :tags {:context "codex-test" :role "foundation"}}
  "行の頭を、器(process.hy)が残す頭と同じ切り方(頭の CUT-HEAD-BYTES の bytes)で取るため。"
  (.decode (cut (.encode line "utf-8") 0 CUT-HEAD-BYTES) "utf-8" "replace"))


(defk with-history [#^ dict answer]
  {:pre [(: answer dict)] :post [(: % str)] :tags {:context "codex-test" :role "foundation"}}
  "thread を開いた答えの thread の turns に、頭の大きさを越える履歴を載せた 1 行を作るため。"
  (val turn {"id" "history-turn" "status" "completed" "items" [{"type" "agentMessage" "id" "m" "text" (* "x" 1000)}]})
  (val result (get answer "result"))
  (json.dumps {#** answer "result" {#** result "thread" {#** (get result "thread") "turns" (* [turn] 200)}}}
              :ensure-ascii False :separators #("," ":")))


(deftest test-the-head-of-a-cut-thread-answer-names-its-request-and-thread
  ;; 履歴の途中で切れた thread を開く答えの頭は、要求の id と thread の id を名乗る答えの記録になる(launch が使うのはこの 2 つだけ)。
  (<- answer (recorded-answer 2))
  (<- line (with-history answer))
  (assert (> (len (.encode line "utf-8")) CUT-HEAD-BYTES))
  (<- head (head-of line))
  (<- record (classify-cut-line head CUT-SIZE))
  (assert (= record (Response :id 2 :thread-id (get answer "result" "thread" "id"))) record))


(deftest test-a-cut-notification-is-unparsed-with-its-method-and-size
  ;; 答えでない行(ターンの出来事)は、頭から中身の欠けた記録を作らない — method と大きさを名乗る Unparsed になる。
  (<- head (head-of (json.dumps {"method" "item/completed"
                                 "params" {"threadId" "t" "turnId" "u"
                                           "item" {"type" "agentMessage" "id" "m" "text" (* "y" (* 2 CUT-HEAD-BYTES))}}})))
  (<- record (classify-cut-line head CUT-SIZE))
  (assert (isinstance record Unparsed) record)
  (assert (= record.method "item/completed") record)
  (assert (in (str CUT-SIZE) record.reason) record))


(deftest test-a-cut-error-answer-keeps-its-code-and-message
  ;; 誤りの答えの頭に code と文が揃っていれば、誤りの答えの記録になる(欄は途中で切れず、欠けるなら丸ごと落ちる)。
  (<- head (head-of (json.dumps {"id" 4 "error" {"code" -32600 "message" "no such thread" "data" (* "z" (* 2 CUT-HEAD-BYTES))}})))
  (<- record (classify-cut-line head CUT-SIZE))
  (assert (= record (ErrorResponse :id 4 :code -32600 :message "no such thread")) record))


(deftest test-a-head-cut-before-any-whole-field-is-unparsed
  ;; 欄が 1 つも揃わない頭は、読めない行(Unparsed)になる — 落ちない。
  (<- record (classify-cut-line "{\"id\":12" CUT-SIZE))
  (assert (isinstance record Unparsed) record)
  (assert (= record.method "") record))


(deftest test-a-cut-json-head-closes-at-its-last-whole-value
  ;; 頭を閉じる所は、最後に揃った値の後: 途中の文字列・数・鍵だけの欄・開いたばかりの入れ子は落とし、開いた入れ子を順に閉じる。
  (<- a (closed-json-head "{\"a\":1,\"b\":\"xy"))
  (assert (= a "{\"a\":1}") a)
  (<- b (closed-json-head "{\"a\":[1,2,{\"c\":\"d\"},{\"e\":"))
  (assert (= b "{\"a\":[1,2,{\"c\":\"d\"}]}") b)
  (<- c (closed-json-head "{\"a\":1,\"b\":12"))
  (assert (= c "{\"a\":1}") c)
  (<- d (closed-json-head "{\"a\":\"q\\\"r\",\"s\":\"t\\\""))
  (assert (= d "{\"a\":\"q\\\"r\"}") d)
  (<- e (closed-json-head "{\"a\":{\"b\":[true,null],\"c\":"))
  (assert (= e "{\"a\":{\"b\":[true,null]}}") e)
  (<- f (closed-json-head "{\"id\":12"))
  (assert (= f "") f))
