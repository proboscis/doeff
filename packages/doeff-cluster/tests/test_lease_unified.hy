;;; 書き手の停止を lease に一本化する(2026-09-25)・lease の柵の穴を塞ぐ。
;;;
;;;   - 期限は coordinator の時計だけで書き・判じる(POST /leases)。奪う側の時計が進んでいても、持ち主の期限より前には奪えない。
;;;   - 持ち主の柵の期限 = 送る前の自分の時計 + TTL。持ち主の時計がどちらへずれていても、柵は coordinator の期限より前に締まる。
;;;   - 旧い版の直の書き(盤の compare-and-set)で、まだ切れていない担い手を追い出すことはできない。
;;;   - 柵の余裕は書きが着くまでの上限(10 秒)より長く、TTL の半分以下。
;;;   - coordinator に届かない間、worker は書き手(入れ替えを宣言した job)を止めない。観測の行を書けなくても書き手は止まらない。
(require doeff-hy.macros [deftest defk defhandler <-])
(import json)
(import time)
(import tempfile)
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with_handlers])
(import datetime [timedelta])
(import doeff_time [GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared_handlers [shared-memory])
(import doeff_cluster.shared_model [WriteShared])
(import doeff_cluster.semaphore_model [LeaseOp lease-op semaphore-write-refusal lease-timing-refusal FENCE-MARGIN-MS])
(import doeff_cluster.semaphore_handlers [SemaphoreSession])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request])
(import doeff_cluster.api_policy [respond])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.worker_policy [kept-when-cut-off])
(import doeff_cluster.handlers [CoordinatorLink release-leases])
(import doeff_cluster.worker_model [DesiredJobs DesiredUnreadable])
(import tests.test_semaphore [lease-writer run-all written-log FakeWrite cut-off-at])
(import doeff_cluster.semaphore_handlers [cluster-semaphore lease-fence])

(setv T (ClusterTiming))


(defn #^ tuple lease [#^ ClusterState state #^ str name #^ str op #^ str token #^ int now #^ int [ttl 15000] #^ int [permits 1]]
  (respond state (Request "POST" (+ "/leases/" name) {} {"op" op "token" token "permits" permits "ttlMs" ttl} :actor "w") now T))


;; --- coordinator の時計だけで判じる ------------------------------------------------------------------

(deftest test-the-coordinator-grants-and-expires-leases-by-its-own-clock
  (setv #(s status answer) (lease (ClusterState) "app-writer" "claim" "a/1/x/1" 1000))
  (assert (= #(status (get answer "ok")) #(200 True)))
  (assert (= (get s.board "semaphore/app-writer" "holders") {"a/1/x/1" 16000}))
  ;; 奪う側の時計がいくら進んでいても、判じるのは coordinator の時刻
  (setv #(_ _ answer) (lease s "app-writer" "claim" "b/1/y/1" 15999))
  (assert (not (get answer "ok")))
  (setv #(s2 _ answer) (lease s "app-writer" "claim" "b/1/y/1" 16000))
  (assert (get answer "ok"))
  (assert (= (get s2.board "semaphore/app-writer" "holders") {"b/1/y/1" 31000}))
  ;; 奪われた持ち主の延長は lost
  (setv #(_ _ answer) (lease s2 "app-writer" "renew" "a/1/x/1" 16500))
  (assert (= (get answer "reason") "lost"))
  ;; 延長は期限を coordinator の時刻 + TTL へ
  (setv #(s3 _ answer) (lease s "app-writer" "renew" "a/1/x/1" 5000))
  (assert (= (get s3.board "semaphore/app-writer" "holders" "a/1/x/1") 20000))
  ;; drop(worker が終わった process の lease を返す)と release
  (setv #(s4 _ answer) (lease s3 "app-writer" "drop" "a/1/" 6000))
  (assert (= (get answer "dropped") 1))
  (assert (= (get s4.board "semaphore/app-writer" "holders") {}))
  (setv #(_ status _) (lease s "app-writer" "claim" "a/1/x/1" 1000 :ttl 0))
  (assert (= status 400)))


(deftest test-an-old-writer-cannot-evict-a-live-holder-through-the-board
  ;; 旧い版の process は自分の時計で「切れた」と判じて盤を compare-and-set で書く。coordinator の時計でまだ切れていなければ断る。
  (setv #(s _ _) (lease (ClusterState) "app-writer" "claim" "a/1/x/1" 1000))
  (setv row (get s.board "semaphore/app-writer"))
  (setv stolen {"permits" 1 "holders" {"b/1/y/1" 99999}})
  (setv #(_ status body) (respond s (Request "PUT" "/board/semaphore/app-writer" {} {"value" stolen "expect" row} :actor "b")
                                  10000 T))
  (assert (= status 409) body)
  ;; 切れた後なら通る(旧い版の奪い方も期限の後なら正しい)
  (setv #(_ status _) (respond s (Request "PUT" "/board/semaphore/app-writer" {} {"value" stolen "expect" row} :actor "b")
                               16001 T))
  (assert (= status 200))
  ;; 外すだけの書き(旧い worker の drop)は通す
  (assert (is (semaphore-write-refusal row {"permits" 1 "holders" {}} 2000) None)))


;; --- 持ち主の時計のずれ ---------------------------------------------------------------------------

(defhandler skewed-clock [#^ int offset]
  ;; この worker の時計だけ offset ms ずれている(保存 = coordinator の時計は外側の本当の時刻)。時刻は持たない — 外側の仮想の時計
  ;; (sim-time-handler)の答えを読み、offset を足して返すだけ。
  (GetTimeEffect []
    (<- now effect)
    (resume (+ now (timedelta :milliseconds offset)))))


(defn #^ tuple run-takeover [#^ int a-offset #^ int b-offset]
  "A は lease を持って 1 秒ごとに書く。3 秒目に A から coordinator へ届かなくなる(延長できない)。B は 0 秒から待つ。"
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "old" :ttl-seconds 45.0 :poll-seconds 0.5)
        sb (SemaphoreSession "new" :ttl-seconds 45.0 :poll-seconds 0.5))
  (defn #^ object fenced [#^ SemaphoreSession session #^ str who #^ list outer]
    ;; 本番の書き手と同じ柵の余裕(FENCE-MARGIN-MS)。
    (with_handlers (+ outer [(cluster-semaphore session) (lease-fence "writer-a" #(FakeWrite) FENCE-MARGIN-MS)])
      (lease-writer who attempts 1 90000)))
  #((with_handlers [(sim-time-handler :clock clock) (shared-memory store) (written-log written)]
      (run-all [(fenced sa "a" [(cut-off-at clock 3000) (skewed-clock a-offset)])
                (fenced sb "b" [(skewed-clock b-offset)])]))
    attempts written))


(deftest test-no-write-of-the-old-holder-lands-after-the-new-holder-starts-whatever-the-clock-skew
  (for [#(a-offset b-offset) [#(0 0) #(-5000 0) #(5000 0) #(0 5000) #(0 -5000) #(-5000 5000)]]
    (setv #(program attempts written) (run-takeover a-offset b-offset))
    (<- program)
    (setv a-landed (lfor #(w at) written :if (= w "a") at) b-landed (lfor #(w at) written :if (= w "b") at))
    (assert (and a-landed b-landed) #(a-offset b-offset))
    ;; A の柵は coordinator の期限(45 秒)の余裕 12 秒前に締まる。B は coordinator の期限の後に取る。
    (assert (<= (max a-landed) (- 45000 FENCE-MARGIN-MS)) #(a-offset b-offset a-landed))
    (assert (>= (min b-landed) 45000) #(a-offset b-offset b-landed))))


;; --- 柵の余裕と TTL の組 ----------------------------------------------------------------------------

(defn #^ None test-lease-timing-needs-a-margin-longer-than-a-write-and-at-most-half-the-ttl []
  (assert (is (lease-timing-refusal 45.0 12000) None))
  (assert (is (lease-timing-refusal 90.0 12000) None))
  (assert (in "書きが着くまで" (lease-timing-refusal 15.0 2000)))    ; 以前の組(余裕 2 秒)は断る
  (assert (in "半分" (lease-timing-refusal 15.0 12000))))


;; --- coordinator に届かない間も書き手は止めない -----------------------------------------------------

(defn #^ None test-only-lease-governed-jobs-are-kept-when-cut-off []
  (setv writer (JobSpec "writer-a" "m" #() "r" :handoff True)
        runner (JobSpec "turn-runner" "m" #() "r")
        task (JobSpec "task/t1" "m" #() "r" :once True :handoff True))
  (assert (= (kept-when-cut-off #(writer runner task)) #(writer))))


(defn #^ None test-a-cut-off-worker-keeps-its-writers-and-stops-the-rest []
  (setv up [True])
  (defn #^ httpx.Response handle [#^ httpx.Request request]
    (if (get up 0)
        (httpx.Response 200 :json {"jobs" [{"name" "writer-a" "entry" "m" "args" [] "revision" "r" "handoff" True}
                                           {"name" "turn-runner" "entry" "m" "args" [] "revision" "r"}]
                                   "tasks" [] "timing" {"fence_ms" 20000}})
        (raise (httpx.ConnectError "coordinator を作り直している"))))
  (setv link (CoordinatorLink "http://coord" "atlas" {} 10 20000 :transport (httpx.MockTransport handle)
                              :task-dir (str (/ (Path (tempfile.mkdtemp)) "tasks"))))
  (assert (= (len (. (.poll link) jobs)) 2))
  (setv (get up 0) False)
  (assert (isinstance (.poll link) DesiredUnreadable))
  (setv link.last-ok (- (time.monotonic) 21))              ; 途絶が fence(20 秒)を越えた
  (setv desired (.poll link))
  (assert (= (lfor j desired.jobs j.name) ["writer-a"])))

(deftest test-the-worker-returns-a-finished-process-lease-through-the-coordinator
  (setv board {"semaphore/app-writer" {"permits" 1 "holders" {"zeus/1-old/ab12/1" 99 "zeus/2-new/cd34/1" 88}}}
        posts [])
  (defn #^ httpx.Response handle [#^ httpx.Request request]
    (if (= request.method "GET")
        (httpx.Response 200 :json board)
        (do (.append posts #(request.url.path (json.loads request.content)))
            (httpx.Response 200 :json {"ok" True "dropped" 1}))))
  (release-leases (CoordinatorLink "http://coord" "zeus" {} 1 60000 :transport (httpx.MockTransport handle)) "1-old")
  (assert (= posts [#("/leases/app-writer" {"op" "drop" "token" "zeus/1-old/"})])))
