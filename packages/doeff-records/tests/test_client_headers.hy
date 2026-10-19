;; 記録の client が要求に付ける見出し(#2988・#3007・#2986): 書き手の名は平文の X-Records-Writer で送り、Authorization は送らない。
;; 呼び手の job の名は X-Records-Caller で送る(一覧の読みの数)。
(require doeff-hy.macros [deftest val])
(import doeff_records.http_client [RecordsEndpoint request-headers])
(import doeff_records.wire [CALLER-HEADER WRITER-HEADER])


(deftest test-a-writer-is-sent-as-a-plain-header-without-a-token
  ;; 呼び手が名だけを渡す形(移行の後): 名の見出しだけを付け、Authorization を付けない。
  (val headers (! (request-headers (RecordsEndpoint "http://records.test" :writer "agent-writer"))))
  (assert (= (get headers WRITER-HEADER) "agent-writer") headers)
  (assert (not-in "Authorization" headers) headers))


(deftest test-a-caller-names-its-job-in-its-own-header
  ;; 呼び手の job の名は X-Records-Caller で送る(service が一覧の読みを呼び手ごとに数える)。渡さない呼び手は見出しを付けない。
  (val named (! (request-headers (RecordsEndpoint "http://records.test" :writer "agent-writer" :caller "reader-job"))))
  (assert (= (get named CALLER-HEADER) "reader-job") named)
  (assert (= (get named WRITER-HEADER) "agent-writer") named)
  (val unnamed (! (request-headers (RecordsEndpoint "http://records.test" :writer "agent-writer"))))
  (assert (not-in CALLER-HEADER unnamed) unnamed))
