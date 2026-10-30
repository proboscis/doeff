;;; ReachJob(届くかの検査)の契約テスト — 同じ effect に答える本物(job-reach-sender → HttpRequest)と模擬(job-reach-handover → 相手の答えの
;;; 1 点へ直に)が、同じ deftest を通る。解釈器の組み立ては job_reach_contract_handlers.hy。
;;;
;;;   * 相手が答えた要求は、status だけを相手の決めたまま答える(404 も届いた事にする)— 名の道も番地の道も
;;;   * 表に無い API の ReachJob は外側へ回す
;;; 届かない種類の写し・繋ぐまでの上限の渡し・道が選ぶ環境変数・道の無い宛先(JobNoWay)は本物だけの性質なので契約の外(この file の後ろの節 —
;;; 素の deftest)。模擬が答えない相手を繋がらなかったと読む事も模擬だけの性質。
(require doeff-hy.macros [deftest defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with_handlers])
(import doeff_core_effects [Ask])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_cluster [JobRequest JobUnreachable])
(import doeff_cluster [JobNoWay JobNotReached JobReached JobWay ReachFailure ReachJob])
(import doeff_cluster [Counterpart])
(import doeff_cluster [ReachDestination job-reach-handover job-reach-sender])
(import tests.job_call_contract_handlers [NOTES-API NOTES-ENVIRON NOTES-ROOT])
(import tests.job_reach_contract_handlers [ELSEWHERE-REACH NOTES-ADDRESS-ENVIRON])


(deftest test-an-answered-reach-is-the-counterparts-status-by-either-way
  {:interpreters ["job-reach-sender" "job-reach-handover"]}
  (for [way [JobWay.BY-NAME JobWay.BY-ADDRESS]]
    (<- found (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") way None))
    (assert (= found (JobReached :status 200)) #(way found))
    (<- missing (ReachJob NOTES-API (JobRequest :method "GET" :path "/elsewhere") way 1.0))
    (assert (= missing (JobReached :status 404)) #(way missing))))


(deftest test-a-reach-to-another-api-is-passed-outward
  {:interpreters ["job-reach-sender" "job-reach-handover"]}
  (<- answer (ReachJob "another" (JobRequest :method "GET" :path "/items/k") JobWay.BY-NAME None))
  (assert (= answer ELSEWHERE-REACH) answer))


;; --- 本物だけの性質(契約の外) ---------------------------------------------------------------------------------------------------

(val NAME-URL "http://notes.test:8080")
(val ADDRESS-URL "http://192.0.2.7:8080")
(val DESTINATIONS #((ReachDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON :address-environ NOTES-ADDRESS-ENVIRON)))


(defhandler two-ways-environ
  ;; 名の道と番地の道の環境変数に、違う host の URL で答えるため(道が選ぶ環境変数を見る)。
  (Ask [key]
    (cond (= key NOTES-ENVIRON) (resume NAME-URL)
          (= key NOTES-ADDRESS-ENVIRON) (resume ADDRESS-URL)
          True (reperform effect))))


(defhandler blank-environ
  ;; 宛先の環境変数が空の宛先に答えるため(送る側は HttpRequest を出さずに道が無いと答えるはず)。
  (Ask [key]
    (resume "  ")))


(defhandler no-request
  ;; 送る側が HttpRequest を出したら検を落とすため(道が無い時は出さないはず)。
  (HttpRequest [method url]
    (raise (AssertionError (.format "道が無いのに要求を出した: {} {}" method url)))))


(defhandler spelled-request
  ;; 送る側が HttpRequest に渡した URL・繋ぐまでの上限・待ちの上限・やり直しの数を、答えの訳に写して返すため。
  (HttpRequest [url connect-timeout-seconds timeout-seconds max-retries]
    (resume (HttpFailed :url url :detail (.format "{} connect={} timeout={} retries={}" url connect-timeout-seconds timeout-seconds max-retries)
                        :kind HttpFailureKind.OTHER))))


(defhandler failing-with [kind]
  ;; 送る側の HttpRequest に、種類 kind の失敗(値で受ける旗の答え)を返すため。
  ;; 引数に残す理由: 失敗の種類は検ごとの値。
  (HttpRequest [url]
    (resume (HttpFailed :url url :detail (.format "failed: {}" kind.value) :kind kind))))


(defhandler answering-503
  ;; 送る側の HttpRequest に 503 を答えるため(届いた答えは 2xx でなくても届いた事にする)。
  (HttpRequest [url]
    (resume (HttpResponse :status 503 :headers {} :content b"" :text "" :url url :elapsed-seconds 0.0))))


(deftest test-the-way-picks-its-environ-and-the-connect-patience-is-passed
  ;; 本物だけの性質: 名の道は environ・番地の道は address-environ の URL へ、根と path の綴りのまま。繋ぐまでの上限は HttpRequest へそのまま
  ;; 渡し(None は None)、待ちの上限は要求の patience・やり直さない。
  (for [#(way connect host) [#(JobWay.BY-NAME None NAME-URL) #(JobWay.BY-ADDRESS 1.0 ADDRESS-URL)]]
    (val answer (run (with_handlers [spelled-request two-ways-environ (job-reach-sender DESTINATIONS)]
                       (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/a b" :patience 10.0) way connect))))
    (assert (isinstance answer JobNotReached) answer)
    (assert (= answer.reason (.format "{}/v1/notes/items/a%20b connect={} timeout=10.0 retries=0" host connect)) answer.reason)))


(deftest test-failure-kinds-are-copied-not-read-from-the-reason
  ;; 本物だけの性質: HttpFailureKind の語を届かない種類へ写す(訳の字は理由の句のまま)。
  (for [#(kind failure) [#(HttpFailureKind.CONNECT-FAILED ReachFailure.NOT-CONNECTED) #(HttpFailureKind.TIMED-OUT ReachFailure.TIMED-OUT)
                         #(HttpFailureKind.OTHER ReachFailure.OTHER)]]
    (val answer (run (with_handlers [(failing-with kind) two-ways-environ (job-reach-sender DESTINATIONS)]
                       (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") JobWay.BY-NAME None))))
    (assert (= answer (JobNotReached :failure failure :reason (.format "failed: {}" kind.value))) answer)))


(deftest test-a-non-2xx-answer-is-reached
  (val answer (run (with_handlers [answering-503 two-ways-environ (job-reach-sender DESTINATIONS)]
                     (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") JobWay.BY-ADDRESS 1.0))))
  (assert (= answer (JobReached :status 503)) answer))


(deftest test-a-way-without-a-destination-is-no-way-without-a-request
  ;; 本物だけの性質: 番地の道を持たない宛先(address-environ が空)と、道の環境変数の値が空の宛先は、HttpRequest を出さずに JobNoWay。
  (val nameless #((ReachDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON)))
  (val unaddressed (run (with_handlers [no-request two-ways-environ (job-reach-sender nameless)]
                          (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") JobWay.BY-ADDRESS 1.0))))
  (assert (isinstance unaddressed JobNoWay) unaddressed)
  (for [way [JobWay.BY-NAME JobWay.BY-ADDRESS]]
    (val blank (run (with_handlers [no-request blank-environ (job-reach-sender DESTINATIONS)]
                      (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") way None))))
    (assert (isinstance blank JobNoWay) #(way blank))
    (assert (in (if (= way JobWay.BY-NAME) NOTES-ENVIRON NOTES-ADDRESS-ENVIRON) blank.reason) blank.reason)))


(defk down-answer [request]
  {:pre [(: request JobRequest)] :post [(: % JobUnreachable)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "居ない相手の答えの 1 点の代わり(どの要求にも届かないと答える)。"
  (JobUnreachable :reason "相手が居ない"))


(deftest test-the-handover-reads-an-unanswering-counterpart-as-not-connected
  ;; 模擬だけの性質: 相手の答えの 1 点が JobUnreachable を返したら、繋がらなかった(訳は相手の訳のまま)。
  (val down (Counterpart :api NOTES-API :answer down-answer))
  (val answer (run (with_handlers [(job-reach-handover #(down))]
                     (ReachJob NOTES-API (JobRequest :method "GET" :path "/items/k") JobWay.BY-NAME None))))
  (assert (= answer (JobNotReached :failure ReachFailure.NOT-CONNECTED :reason "相手が居ない")) answer))
