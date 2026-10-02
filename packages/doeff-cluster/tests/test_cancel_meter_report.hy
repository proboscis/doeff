;; 取り消しで止めた子 process の数えが coordinator の計測値に出る検(#2938 — #2847 の数えを #2740 の橋で送る)。
;;   壁の時計の sim-cluster(本物の coordinator と worker・本物の子 process)の上で、service が子 process の要求を 1 回取り消す。
;;   doeff-core-effects の metered-offloaded-subprocess-handler が止めた子を process に 1 つの計器(process-meter-handler)へ数え、
;;   計器の橋 with-meter-report がその断面を coordinator へ送り、GET /metrics に service の label つきで
;;   process_cancel_terminated_total が 1 で出る(counter の名に _total を付けるのは描き手 — 計器の名は付けない)。
;;   反例: 子 process の答え手に計器を渡さない(None)と、同じ筋書きで取り消しの後も行が出ない — 1 本目の検はこの形を赤にする。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_time [Delay GetMonotonic])
(import doeff_cluster.shared.intent.protocol [PlainText])
(import doeff_cluster.sim.local [wall-sim-cluster ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.wall_programs [rows-when-present])
(import tests.fixtures.cancel_meter_programs [cancel-reporters uncounted-cancel-reporters CANCELLED-KEY])

;; coordinator の GET /metrics の、service stopper の「SIGTERM で止めた子」の counter の行の頭(counter は名に _total が付く)。
(val STOPPED-LINE "process_cancel_terminated_total{service=\"stopper\"")
;; 取り消しの数えの行(名の綴りの食い違いを赤の文に見せるため、頭がこれの行を全部拾う)。
(val CANCEL-LINES "process_cancel_")
;; 筋書きが盤の取り消しの行を待つ上限(秒 — coordinator と worker の起動と service の起こしを含む)と、取り消しの後に計測値を読み続ける
;; 上限(秒 — 橋は 0.2 秒ごとに送る)・読み直す間隔(秒)。
(val CANCEL-DEADLINE-SECONDS 20.0)
(val SETTLE-SECONDS 5.0)
(val POLL-SECONDS 0.1)


(defrecord CancelSeen
  "筋書きで見た事: cancelled = service が子の要求を取り消した行が盤に出たか・stopped = GET /metrics の service stopper の「SIGTERM で
   止めた子」の値(行が無ければ None)・lines = GET /metrics の取り消しの数えの行(最後に読んだ物)。"
  (#^ bool cancelled)
  (#^ (| float None) stopped)
  (#^ (get tuple #(str ...)) lines))


(defk cancel-lines []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の GET /metrics を読み、取り消しの数えの行を返すため。"
  (<- metrics PlainText (ReadCoordinator "/metrics"))
  (tuple (gfor line (.splitlines metrics.text) :if (.startswith line CANCEL-LINES) line)))


(defk stopped-value [lines]
  {:pre [(: lines tuple)] :post [(: % (| float None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "取り消しの数えの行から、service stopper の「SIGTERM で止めた子」の値を取るため(行が無ければ None)。"
  (match (tuple (gfor line lines :if (.startswith line STOPPED-LINE) line))
    #() None
    found (float (get (.split (get found 0)) -1))))


(defk stopped-children-seen []
  {:pre [] :post [(: % CancelSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: service が子の要求を取り消した行が盤に出るのを待ち(CANCEL-DEADLINE-SECONDS まで)、その後 SETTLE-SECONDS の内に GET /metrics
   に service stopper の「SIGTERM で止めた子」の行が出るまで POLL-SECONDS おきに読む。"
  (<- rows dict (rows-when-present "cancel/" CANCELLED-KEY CANCEL-DEADLINE-SECONDS))
  (<- started float (GetMonotonic))
  (<- first tuple (cancel-lines))
  (<- first-value (| float None) (stopped-value first))
  (var lines first)
  (var stopped first-value)
  (var elapsed 0.0)
  (while (and (is stopped None) (< elapsed SETTLE-SECONDS))
    (<- (Delay POLL-SECONDS))
    (<- read tuple (cancel-lines))
    (<- value (| float None) (stopped-value read))
    (<- now float (GetMonotonic))
    (:= lines read)
    (:= stopped value)
    (:= elapsed (- now started)))
  (CancelSeen :cancelled (in CANCELLED-KEY rows) :stopped stopped :lines lines))


(deftest test-a-cancelled-child-is-counted-on-the-coordinator-metrics [tmp-path]
  ;; 子の要求を 1 回取り消す → 止めた子の数え 1 → 橋が送る → coordinator の GET /metrics に 1。
  (<- seen CancelSeen (wall-sim-cluster (cancel-reporters sim-foundation (str (/ tmp-path "child.pid"))) (stopped-children-seen)))
  (assert seen.cancelled seen)
  (assert (= seen.stopped 1.0) seen))


(deftest test-a-child-handler-without-a-meter-leaves-no-count [tmp-path]
  ;; 失敗ケース: 子 process の答え手に計器を渡さない(None)と、取り消しの後も行が出ない — 上の検はこの形を赤にする。
  (<- seen CancelSeen (wall-sim-cluster (uncounted-cancel-reporters sim-foundation (str (/ tmp-path "child.pid")))
                                        (stopped-children-seen)))
  (assert seen.cancelled seen)
  (assert (is seen.stopped None) seen)
  (assert (= seen.lines #()) seen))
