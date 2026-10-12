;;; 検の時計の道具。時計は doeff-time の仮想の時計(SimClock + sim-time-handler)ちょうど 1 つで、ここは時刻に答えない —
;;; 契約の物差し(epoch ミリ秒)で起点を置く・読むための換算と、眠りを数えて外側へ渡すだけの観測の handler。例外は機体の壁の時計の
;;; 代役 wall-back だけ: 外側の時計の GetTime の答えを決まった ms 戻して渡す(壁の時計が step した機体 — GetMonotonic は通す)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import datetime [datetime timedelta])
(import doeff_time [DelayEffect GetTimeEffect SimClock epoch-ms-of])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])


(defk clock-at [ms]
  {:pre [(: ms int)] :post [(: % SimClock)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "契約の物差し(epoch ミリ秒)の時刻 ms から始まる仮想の時計を作るため。"
  (SimClock (datetime-of-epoch-ms ms)))


(defk clock-ms [clock]
  {:pre [(: clock SimClock)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "仮想の時計のいまを epoch ミリ秒で読むため。"
  (epoch-ms-of clock.current-time))


(defhandler count-delays [#^ list delays]
  ;; 眠りの秒を delays に積んで、外側の sim-time-handler へそのまま渡す(時刻には答えない)。
  (DelayEffect [seconds]
    (.append delays seconds)
    (<- effect)
    (resume None)))


(defhandler wall-back [#^ int back-ms]
  ;; 引数に残す理由: 検ごとに別の戻りで並べる(Ask で区別できない)。
  ;; 壁の時計が back-ms 戻った機体の代役(負 = 進んだ): 外側の時計の GetTime の答えだけをずらす。単調な時計(GetMonotonic)と眠りは通す。
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (- now (timedelta :milliseconds back-ms)))))
