;;; CallJob(job 向けの effect)の契約テスト — 同じ effect に答える本物(job-call-sender → HttpRequest)と模擬(job-call-handover → 相手の答えの
;;; 1 点へ直に)が、同じ deftest を通る。解釈器の組み立ては job_call_contract_handlers.hy。
;;;
;;;   * 読み: 鍵の綴り(区切りの :・空白・非 ASCII)と query の組(同じ名の 2 つ目・空の値・順)と要求の見出しが、相手にそのまま届き、答えの
;;;     status・content-type・本文・答えの見出しがそのまま返る
;;;   * 書き: 本文の bytes が相手にそのまま届く
;;;   * 相手の 404 は相手の答え(届かなかった事にしない)
;;;   * 表に無い API の CallJob は外側へ回す
;;; 宛先の環境変数が空の時・宛先に届かない時の答え(JobUnreachable)と、綴りの 1 点 job-target は本物だけの性質なので契約の外
;;; (この file の後ろの節 — 素の deftest)。
(require doeff-hy.macros [deftest defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import doeff [run with_handlers])
(import doeff_core_effects [Ask])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest])
(import doeff_cluster.shared.intent.job_call_model [CallJob JobAnswer JobField JobRequest JobUnreachable])
(import doeff_cluster.shared.protocol.job_call_handlers [JobDestination job-call-sender job-target])
(import tests.job_call_contract_handlers [ASKER-HEADER ELSEWHERE JSON-TYPE NOTES-API NOTES-ENVIRON NOTES-ROOT VERSION VERSION-HEADER])


(deftest test-a-read-reaches-the-counterpart-with-its-spelling-and-answers-as-is
  {:interpreters ["job-call-sender" "job-call-handover"]}
  (<- answer (CallJob NOTES-API (JobRequest :method "GET" :path "/items/ns:item-1 二 ö"
                                            :query #((JobField :name "tag" :value "a b") (JobField :name "tag" :value "c&d")
                                                     (JobField :name "empty" :value "") (JobField :name "名" :value "値"))
                                            :headers #((JobField :name ASKER-HEADER :value "reader-1")))))
  (assert (isinstance answer JobAnswer) answer)
  (assert (= answer.status 200) answer)
  (assert (= answer.content-type JSON-TYPE) answer)
  (assert (= (json.loads answer.body) {"key" "ns:item-1 二 ö" "query" [["tag" "a b"] ["tag" "c&d"] ["empty" ""] ["名" "値"]] "asker" "reader-1"})
          (.decode answer.body "utf-8"))
  (assert (in (JobField :name VERSION-HEADER :value VERSION) answer.headers) answer.headers))


(deftest test-a-write-carries-its-body-bytes
  {:interpreters ["job-call-sender" "job-call-handover"]}
  (<- answer (CallJob NOTES-API (JobRequest :method "POST" :path "/items/k" :body (.encode "{\"text\": \"本文\"}" "utf-8"))))
  (assert (= answer.status 202) answer)
  (assert (= (json.loads answer.body) {"key" "k" "body" "{\"text\": \"本文\"}"}) (.decode answer.body "utf-8")))


(deftest test-the-counterparts-404-is-its-answer
  {:interpreters ["job-call-sender" "job-call-handover"]}
  (<- answer (CallJob NOTES-API (JobRequest :method "GET" :path "/elsewhere")))
  (assert (isinstance answer JobAnswer) answer)
  (assert (= answer.status 404) answer)
  (assert (= (json.loads answer.body) {"missing" "/elsewhere"}) answer))


(deftest test-a-call-to-another-api-is-passed-outward
  {:interpreters ["job-call-sender" "job-call-handover"]}
  (<- answer (CallJob "another" (JobRequest :method "GET" :path "/items/k")))
  (assert (= answer ELSEWHERE) answer))


;; --- 本物だけの性質(契約の外) ---------------------------------------------------------------------------------------------------

(defhandler empty-environ
  ;; 宛先の環境変数が空の宛先に答えるため(送る側は HttpRequest を出さずに届かないと答えるはず)。
  (Ask [key]
    (resume "  ")))


(defhandler no-request
  ;; 送る側が HttpRequest を出したら検を落とすため(宛先が空の時は出さないはず)。
  (HttpRequest [method url]
    (raise (AssertionError (.format "宛先が空なのに要求を出した: {} {}" method url)))))


(defhandler fixed-environ
  ;; 宛先の環境変数に、末尾に / の付いた URL で答えるため(綴りが / を重ねないかを見る)。
  (Ask [key]
    (resume "http://notes.test:8080/")))


(defhandler refused-connection
  ;; 宛先の口が接続を断る時の HttpRequest の答え(届かなかった — 値で受ける旗の答え)を返すため。綴った URL を答えの url に写す。
  (HttpRequest [method url]
    (resume (HttpFailed :url url :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED))))


(defhandler redirect-flag-echo
  ;; 送る側が HttpRequest に渡した転送の旗を、答えの本文に写して返すため(転送を辿るのは HttpRequest の handler — 送る側は旗を渡すだけ)。
  (HttpRequest [method url follow-redirects]
    (resume (HttpFailed :url url :detail (.format "follow-redirects={}" follow-redirects) :kind HttpFailureKind.CONNECT-FAILED))))


(deftest test-the-sender-follows-redirects-only-when-the-request-asks
  ;; 本物だけの性質: 要求の follow-redirects を HttpRequest へそのまま渡す(既定は辿らない)。
  (val destinations #((JobDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON)))
  (for [#(asked expected) [#(None "follow-redirects=False") #(True "follow-redirects=True") #(False "follow-redirects=False")]]
    (val request (if (is asked None)
                     (JobRequest :method "GET" :path "/items/k")
                     (JobRequest :method "GET" :path "/items/k" :follow-redirects asked)))
    (val answer (run (with_handlers [redirect-flag-echo fixed-environ (job-call-sender destinations)] (CallJob NOTES-API request))))
    (assert (and (isinstance answer JobUnreachable) (in expected answer.reason)) #(asked answer))))


(deftest test-an-empty-destination-is-unreachable-without-a-request
  (val destinations #((JobDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON)))
  (val answer (run (with_handlers [no-request empty-environ (job-call-sender destinations)]
                     (CallJob NOTES-API (JobRequest :method "GET" :path "/items/k")))))
  (assert (isinstance answer JobUnreachable) answer)
  (assert (in NOTES-ENVIRON answer.reason) answer))


(deftest test-a-refused-connection-is-unreachable-with-the-spelled-url
  (val destinations #((JobDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON)))
  (val answer (run (with_handlers [refused-connection fixed-environ (job-call-sender destinations)]
                     (CallJob NOTES-API (JobRequest :method "GET" :path "/items/a b" :query #((JobField :name "q" :value "x y")))))))
  (assert (isinstance answer JobUnreachable) answer)
  (assert (in "http://notes.test:8080/v1/notes/items/a%20b?q=x+y" answer.reason) answer.reason))


(deftest test-the-target-keeps-colons-and-slashes-and-the-query-order
  (val target (run (job-target "/v1/x" (JobRequest :method "GET" :path "/items/ns:item:k-1/tags"
                                                   :query #((JobField :name "b" :value "2") (JobField :name "a" :value "1"))))))
  (assert (= target "/v1/x/items/ns:item:k-1/tags?b=2&a=1") target)
  (assert (= (run (job-target "" (JobRequest :method "GET" :path "/a b"))) "/a%20b")))
