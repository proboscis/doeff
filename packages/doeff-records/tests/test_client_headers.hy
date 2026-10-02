;; 記録の client が要求に付ける見出し(#2988・#3007): 書き手の名は平文の X-Records-Writer で送り、token は移行の間だけ在る時に送る。
(require doeff-hy.macros [deftest val])
(import doeff_records.http_client [RecordsEndpoint request-headers])
(import doeff_records.wire [WRITER-HEADER])


(deftest test-a-writer-is-sent-as-a-plain-header-without-a-token
  ;; 呼び手が名だけを渡す形(移行の後): 名の見出しだけを付け、Authorization を付けない。
  (val headers (! (request-headers (RecordsEndpoint "http://records.test" :writer "agent-writer"))))
  (assert (= (get headers WRITER-HEADER) "agent-writer") headers)
  (assert (not-in "Authorization" headers) headers))


(deftest test-a-token-from-a-current-caller-is-still-sent-during-the-move
  ;; 今の呼び手の形 (RecordsEndpoint url token …) は変えずに動く: token が在る時だけ Authorization を付け、名の見出しは付けない。
  (val headers (! (request-headers (RecordsEndpoint "http://records.test" "t"))))
  (assert (= (get headers "Authorization") "Bearer t") headers)
  (assert (not-in WRITER-HEADER headers) headers))
