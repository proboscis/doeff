;; 終わった process の lease の返し(worker/protocol/lease_release の lease-release)が worker の拍を待たせない形の検。
;;
;; 実例(2026-11-12 の版の替わる入れ替え): 回収(ReapJob)1 件ごとに、拍の中で lease の返し(ReleaseLeases)が盤の semaphore の行を
;; 全部読んだ(3 万行・2.7 MB・1 回 0.4〜1.4 秒)。入れ替えでは 10〜19 件の回収が 1 つの拍に並び、回収の行が 1〜3 秒おきに出て、
;; 拍の区間 ActionsToPublishStatus が 10〜36 秒になり、入れ替えの新の起こしがその拍の終わりを待った。だから:
;;   - 答え手は返しを待ちの列に積んですぐ答える — 続けて頼んだ返しの間に模擬の時計が進まない
;;   - 返しは拍の外の task が行い、積まれた物を盤の 1 回の読みでまとめて返す(読みの数は返しの数に比例しない)
;;   - 列が空になった後に頼んだ返しも返る(task が起き直す)・思わぬ例外の組はあきらめて 1 行出し、次の組は返る
;; 反例 = 直す前の形(答え手の節が released-leases を返しごとに拍の中で待つ lease-release)は、12 件の返しの間に模擬の時計が
;; 12 回の読み + 12 回の drop の分(36 秒)進み、盤の読みが 12 回 — 下の断言が赤(2026-11-15 に直す前の lease_release.hy で確かめた)。
;; 土台: coordinator = 盤の dict を答える検の答え手(要求 1 つに模擬の SLOW-SECONDS 秒)・時計 = 模擬の時計。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import doeff [with-handlers])
(import doeff_core_effects [HttpRequest SlogEffect scheduled state])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster [ReleaseLeases lease-release now-epoch-ms])
(import doeff_cluster._worker.protocol.lease_release [RELEASE-FAILED-LOG])
(import tests.transport_http [transport-http route-cell TEST-ROUTE])

;; coordinator の要求 1 つの模擬の秒(本番の盤の読みは混んだ時に 1〜3 秒)。
(val SLOW-SECONDS 1.5)
;; 1 つの拍に並ぶ回収の数(本番の入れ替えで 10〜19)。
(val REAPS 12)
;; 返しを頼んだ後、拍の外の task が終わるまで待つ模擬の秒。
(val SETTLE-SECONDS 120.0)


(defclass Coordinator []
  "検の coordinator: board = 盤の semaphore の行・reads = 盤の読みの数・drops = drop の要求の #(path token) の並び・fail-reads = まだ例外で
   断る盤の読みの数。"
  (defn #^ None __init__ [self #^ dict board #^ int [fail-reads 0]]
    (setv self.board board self.reads 0 self.drops #() self.fail-reads fail-reads))
  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (if (= request.method "GET")
        (do (setv self.reads (+ self.reads 1))
            (when (> self.fail-reads 0)
              (setv self.fail-reads (- self.fail-reads 1))
              (raise (RuntimeError "盤の読みの思わぬ失敗")))
            (httpx.Response 200 :json (dfor #(k v) (.items self.board) :if (.startswith k (get request.url.params "prefix")) k v)))
        (do (setv self.drops (+ self.drops #(#(request.url.path (get (json.loads request.content) "token")))))
            (httpx.Response 200 :json {"ok" True "dropped" 1})))))


(defhandler slow-requests
  "遅い coordinator の代役: 要求を模擬の SLOW-SECONDS 秒待ってから外側の答え手へ出し直す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest []
    (<- (Delay SLOW-SECONDS))
    (<- answer effect)
    (resume answer)))


(defeffect NotedFailures
  "返しの task の失敗の行の数を問う — 検だけの問い。"
  {:fields [] :answer int :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler failures-noted
  "外の世界の log の代役: 返しの task の失敗の行を数え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var failures 0)
  (SlogEffect []
    (when (= effect.msg RELEASE-FAILED-LOG) (:= failures (+ failures 1)))
    (resume None))
  (NotedFailures []
    (resume failures)))


(defn #^ dict held-board [#^ int n]
  "job-<i> の世代 1-old が 1 つずつ持つ lease の行 n 本と、別の process の行 1 本の盤。"
  (| (dfor i (range n) (.format "semaphore/lock-{}" i) {"permits" 1 "holders" {(.format "job-{}/1-old/1" i) 99}})
     {"semaphore/other" {"permits" 1 "holders" {"other/1-cur/1" 77}}}))


(defk released-in-a-row [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: job-0 … job-<n-1> の世代 1-old の返しを続けて頼み(1 つの拍の中の回収の並びの代役)、task が終わるまで待ち、もう 1 つ
   (job-late)を頼んでまた待つため。答え = #(続けて頼んだ間に進んだ模擬の ms 後の返しの前の失敗の行の数 最後の失敗の行の数)。"
  (<- began int (now-epoch-ms))
  (for [i (range n)]
    (<- (ReleaseLeases (.format "job-{}" i) "1-old")))
  (<- asked int (now-epoch-ms))
  (<- (Delay SETTLE-SECONDS))
  (<- failed-first int (NotedFailures))
  (<- (ReleaseLeases "job-late" "1-old"))
  (<- (Delay SETTLE-SECONDS))
  (<- failed int (NotedFailures))
  #((- asked began) failed-first failed))


(defk run-releases [coordinator]
  {:pre [(: coordinator Coordinator)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書き released-in-a-row を、遅い coordinator と模擬の時計の下で回すため。"
  (<- answer tuple (scheduled (with-handlers [(state) (transport-http (httpx.MockTransport coordinator.handle)) failures-noted
                                              (sim-time-handler :clock (SimClock)) slow-requests
                                              (lease-release (route-cell) TEST-ROUTE)]
                                             (released-in-a-row REAPS))))
  answer)


(deftest test-a-row-of-releases-does-not-hold-the-tick
  ;; 12 件の返しを続けて頼んでも、頼みの間に模擬の時計は進まない(直す前は 12 回の読みと 12 回の drop で 36 秒)。
  (val coordinator (Coordinator (| (held-board REAPS) {"semaphore/late" {"permits" 1 "holders" {"job-late/1-old/1" 99}}})))
  (<- got tuple (run-releases coordinator))
  (setv #(asked-ms _ _) got)
  (assert (= asked-ms 0) got))


(deftest test-the-releases-of-one-tick-share-one-board-read
  ;; 拍の外の task は積まれた返しを 1 回の読みで返す: 12 件の lease は全部外れ、別の process の行は触らず、盤の読みは 12 回でなく
  ;; 2 回まで(最初の頼みで起きた task が読む前に、残りが積まれる)+ 後の返しの 1 回。
  (val coordinator (Coordinator (| (held-board REAPS) {"semaphore/late" {"permits" 1 "holders" {"job-late/1-old/1" 99}}})))
  (<- got tuple (run-releases coordinator))
  (val expected (+ (lfor i (range REAPS) #((.format "/leases/lock-{}" i) (.format "job-{}/1-old/" i)))
                   [#("/leases/late" "job-late/1-old/")]))
  (assert (= (sorted coordinator.drops) (sorted expected)) coordinator.drops)
  (assert (<= coordinator.reads 3) coordinator.reads)
  (assert (= got #(0 0 0)) got))


(deftest test-a-failed-batch-is-given-up-and-the-next-release-still-goes
  ;; 盤の読みが思わぬ例外で終わった組は、あきらめて失敗の行を 1 つ出す(lease は期限で切れる)。task は止まらず、後の返しは返る。
  (val coordinator (Coordinator (| (held-board 1) {"semaphore/late" {"permits" 1 "holders" {"job-late/1-old/1" 99}}}) :fail-reads 1))
  (<- got tuple (scheduled (with-handlers [(state) (transport-http (httpx.MockTransport coordinator.handle)) failures-noted
                                           (sim-time-handler :clock (SimClock)) slow-requests
                                           (lease-release (route-cell) TEST-ROUTE)]
                                          (released-in-a-row 1))))
  (setv #(_ failed-first failed) got)
  (assert (= failed-first 1) got)
  (assert (= failed 1) got)
  (assert (= coordinator.drops #(#("/leases/late" "job-late/1-old/"))) coordinator.drops))
