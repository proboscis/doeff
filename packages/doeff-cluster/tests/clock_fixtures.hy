;;; 検の時計の道具。時計は doeff-time の仮想の時計(SimClock + sim-time-handler)ちょうど 1 つで、ここは時刻に答えない —
;;; 契約の物差し(epoch ミリ秒)で起点を置く・読むための換算と、眠りを数えて外側へ渡すだけの観測の handler。
(require doeff-hy.macros [defhandler <-])
(import doeff_time [DelayEffect SimClock])
(import doeff_cluster.clock [epoch-ms-of datetime-of-epoch-ms])


(defn #^ SimClock clock-at [#^ int ms]
  "契約の物差し(epoch ミリ秒)の時刻から始まる仮想の時計。"
  (SimClock (datetime-of-epoch-ms ms)))


(defn #^ int clock-ms [#^ SimClock clock]
  "仮想の時計のいまを epoch ミリ秒で。"
  (epoch-ms-of clock.current-time))


(defhandler count-delays [#^ list delays]
  ;; 眠りの秒を delays に積んで、外側の sim-time-handler へそのまま渡す(時刻には答えない)。
  (DelayEffect [seconds]
    (.append delays seconds)
    (<- effect)
    (resume None)))
