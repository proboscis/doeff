;;; 検の時計の道具。時計は doeff-time の仮想の時計(SimClock + sim-time-handler)ちょうど 1 つで、ここは時刻に答えない —
;;; 契約の物差し(epoch ミリ秒)で起点を置く・読むための換算と、眠りを数えて外側へ渡すだけの観測の handler。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_time [DelayEffect SimClock epoch-ms-of])
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
