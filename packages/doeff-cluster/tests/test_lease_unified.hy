;;; 書き手の停止を lease に一本化する(2026-09-25)・lease の柵の穴を塞ぐ。
;;;
;;;   - 期限は coordinator の時計だけで書き・判じる(POST /leases)。奪う側の時計が進んでいても、持ち主の期限より前には奪えない。
;;;   - 持ち主の柵の期限 = 送る前の自分の時計 + TTL。持ち主の時計がどちらへずれていても、柵は coordinator の期限より前に締まる。
;;;   - 旧い版の直の書き(盤の compare-and-set)で、まだ切れていない担い手を追い出すことはできない。
;;;   - 柵の余裕は書きが着くまでの上限(10 秒)より長く、TTL の半分以下。
;;;   - coordinator に届かない間、worker は書き手(入れ替えを宣言した job)を止めない。観測の行を書けなくても書き手は止まらない。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import time)
(import tempfile)
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with_handlers])
(import datetime [datetime timedelta])
(import doeff_time [GetTimeEffect SimClock sim-time-handler])
(import tests.board_fake [board-handlers])
(import doeff_cluster.shared.intent.shared_model [WriteShared])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp FENCE-MARGIN-MS])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-write-refusal lease-timing-refusal])
(import doeff_cluster.shared.core.semaphore_handlers [SemaphoreSession])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.core.policy [kept-when-cut-off])
(import tests.link_rig [LinkRig])
(import tests.transport_http [released-through])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable])
(import tests.test_semaphore [lease-writer run-all written-log FakeWrite cut-off-at])
(import doeff_cluster.shared.core.semaphore_handlers [cluster-semaphore lease-fence])

(setv T (ClusterTiming))


(defk lease [state name op token now [ttl 15000] [permits 1]]
  {:pre [(: state ClusterState) (: name str) (: op str) (: token str) (: now int) (: ttl int) (: permits int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "lease の口 POST /leases/<name> の要求を 1 つ判断 responded に渡し、(状態 状態の番号 本文) の組を返すため。"
  (responded state (! (http-request "POST" (+ "/leases/" name) {} {"op" op "token" token "permits" permits "ttlMs" ttl} :actor "w")) now T))


;; --- coordinator の時計だけで判じる ------------------------------------------------------------------

;; 取る・期限の前の断り・期限で奪える・奪われた延長は lost・延長は時刻 + TTL・drop・期限 0 の断りは、tests/test_shared_contract.hy が
;; 本物の client(coordinator の POST /leases)と fake の両方で、coordinator の時計(仮想の時計)で見る。


(deftest test-an-old-writer-cannot-evict-a-live-holder-through-the-board
  ;; 旧い版の process は自分の時計で「切れた」と判じて盤を compare-and-set で書く。coordinator の時計でまだ切れていなければ断る。
  (setv #(s _ _) (! (lease (ClusterState) "app-writer" "claim" "a/1/x/1" 1000)))
  (setv row (. (get s.board "semaphore/app-writer") value))
  (setv stolen {"permits" 1 "holders" {"b/1/y/1" 99999}})
  (setv #(_ early-status early-body) (responded s (! (http-request "PUT" "/board/semaphore/app-writer" {} {"value" stolen "expect" row} :actor "b"))
                                              10000 T))
  (assert (= early-status 409) early-body)
  ;; 切れた後なら通る(旧い版の奪い方も期限の後なら正しい)
  (setv #(_ late-status _) (responded s (! (http-request "PUT" "/board/semaphore/app-writer" {} {"value" stolen "expect" row} :actor "b"))
                                    16001 T))
  (assert (= late-status 200))
  ;; 外すだけの書き(旧い worker の drop)は通す
  (assert (is (semaphore-write-refusal row {"permits" 1 "holders" {}} 2000) None)))


;; --- 持ち主の時計のずれ ---------------------------------------------------------------------------

(defhandler skewed-clock [#^ int offset]
  ;; この worker の時計だけ offset ms ずれている(保存 = coordinator の時計は外側の本当の時刻)。時刻は持たない — 外側の仮想の時計
  ;; (sim-time-handler)の答えを読み、offset を足して返すだけ。
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (+ now (timedelta :milliseconds offset)))))


(defk fenced-writer [session who outer attempts]
  {:pre [(: session SemaphoreSession) (: who str) (: outer list) (: attempts list)] :post [(: % (type None))]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "1 つの worker の書き手: outer(途絶・時計のずれ)→ cluster-semaphore → 書きの柵の下で、lease を取って 1 秒ごとに書く(90 秒まで)。"
  ;; 本番の書き手と同じ柵の余裕(FENCE-MARGIN-MS)。
  (<- (with_handlers (+ outer [(cluster-semaphore session) (lease-fence "writer-a" #(FakeWrite) FENCE-MARGIN-MS)])
        (lease-writer who attempts 1 90000)))
  None)


(defk run-takeover [a-offset b-offset]
  {:pre [(: a-offset int) (: b-offset int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "A は lease を持って 1 秒ごとに書く。3 秒目に A から coordinator へ届かなくなる(延長できない)。B は 0 秒から待つ。"
  (val clock (SimClock))
  (val store {})
  (val attempts [])
  (val written [])
  (val sa (SemaphoreSession "old" :ttl-seconds 45.0 :poll-seconds 0.5))
  (val sb (SemaphoreSession "new" :ttl-seconds 45.0 :poll-seconds 0.5))
  #((with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
      (run-all [(fenced-writer sa "a" [(cut-off-at clock 3000) (skewed-clock a-offset)] attempts)
                (fenced-writer sb "b" [(skewed-clock b-offset)] attempts)]))
    attempts written))


(deftest test-no-write-of-the-old-holder-lands-after-the-new-holder-starts-whatever-the-clock-skew
  (for [#(a-offset b-offset) [#(0 0) #(-5000 0) #(5000 0) #(0 5000) #(0 -5000) #(-5000 5000)]]
    (setv #(program attempts written) (! (run-takeover a-offset b-offset)))
    (<- program)
    (setv a-landed (lfor #(w at) written :if (= w "a") at) b-landed (lfor #(w at) written :if (= w "b") at))
    (assert (and a-landed b-landed) #(a-offset b-offset))
    ;; A の柵は coordinator の期限(45 秒)の余裕 12 秒前に締まる。B は coordinator の期限の後に取る。
    (assert (<= (max a-landed) (- 45000 FENCE-MARGIN-MS)) #(a-offset b-offset a-landed))
    (assert (>= (min b-landed) 45000) #(a-offset b-offset b-landed))))


;; --- 柵の余裕と TTL の組 ----------------------------------------------------------------------------

(deftest test-lease-timing-needs-a-margin-longer-than-a-write-and-at-most-half-the-ttl
  (assert (is (lease-timing-refusal 45.0 12000) None))
  (assert (is (lease-timing-refusal 90.0 12000) None))
  ;; 断る組は理由の文を返す(None でないことを先に確かめてから、文の中身を読む)。
  (val short-margin (lease-timing-refusal 15.0 2000))    ; 以前の組(余裕 2 秒)は断る
  (assert (is-not short-margin None))
  (assert (in "書きが着くまで" short-margin))
  (val past-half (lease-timing-refusal 15.0 12000))
  (assert (is-not past-half None))
  (assert (in "半分" past-half)))


;; --- coordinator に届かない間も書き手は止めない -----------------------------------------------------

(deftest test-only-lease-governed-jobs-are-kept-when-cut-off
  (val writer (JobSpec "writer-a" "m" #() "r" :handoff True))
  (val runner (JobSpec "turn-runner" "m" #() "r"))
  (val task (JobSpec "task/t1" "m" #() "r" :once True :handoff True))
  (assert (= (kept-when-cut-off #(writer runner task) 60000 240000) #(writer))))


(deftest test-a-cut-off-worker-keeps-its-writers-and-stops-the-rest
  (var up True)
  ;; httpx の MockTransport が要求ごとに同期で呼ぶ callback(Program の外)— 届くかどうかは呼ばれた時の up で決まる。
  (val handle (fn #^ httpx.Response [#^ httpx.Request request]
                (if up
                    (httpx.Response 200 :json {"jobs" [{"name" "writer-a" "entry" "m" "args" [] "revision" "r" "handoff" True}
                                                       {"name" "turn-runner" "entry" "m" "args" [] "revision" "r"}]
                                               "tasks" [] "timing" {"fence_ms" 20000}})
                    (raise (httpx.ConnectError "coordinator を作り直している")))))
  (val link (LinkRig "http://coord" "atlas" #() 10 0 20000 :transport (httpx.MockTransport handle)
                     :task-dir (str (/ (Path (tempfile.mkdtemp)) "tasks"))))
  ;; poll の答えは DesiredJobs か DesiredUnreadable — jobs を読む前に DesiredJobs であることを確かめる(読めない答えから jobs を
  ;; 読めば属性の誤りで落ちるだけで、確かめたい「読めた」を確かめない)。
  (val seen (.poll link))
  (assert (isinstance seen DesiredJobs) seen)
  (assert (= (len seen.jobs) 2))
  (:= up False)
  (assert (isinstance (.poll link) DesiredUnreadable))
  (setv link.state.last-ok-ms (- (int (* 1000 (time.time))) (int (* 1000 21))))              ; 途絶が fence(20 秒)を越えた
  (val desired (.poll link))
  (assert (isinstance desired DesiredJobs) desired)
  (assert (= (lfor j desired.jobs j.name) ["writer-a"])))

(deftest test-the-worker-returns-a-finished-process-lease-through-the-coordinator
  (setv board {"semaphore/app-writer" {"permits" 1 "holders" {"app-writer/1-old/1" 99 "app-writer/2-new/1" 88}}}
        posts [])
  (defn #^ httpx.Response handle [#^ httpx.Request request]
    (if (= request.method "GET")
        (httpx.Response 200 :json board)
        (do (.append posts #(request.url.path (json.loads request.content)))
            (httpx.Response 200 :json {"ok" True "dropped" 1}))))
  (<- (released-through (httpx.MockTransport handle) "app-writer" "1-old"))
  (assert (= posts [#("/leases/app-writer" {"op" "drop" "token" "app-writer/1-old/"})])))
