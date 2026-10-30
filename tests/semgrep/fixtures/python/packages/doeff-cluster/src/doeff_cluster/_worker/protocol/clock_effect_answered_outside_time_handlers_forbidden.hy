;;; 検体(違反をわざと書いた file): 時計の effect に doeff-time の答え手の外で答える・答え手に時計の源を渡す。
;;; doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers が 10・15・20・21 行目で発火し、24〜26 行目では発火しない。
(require doeff-hy.macros [defhandler <- val])
(import doeff_time [GetMonotonic GetMonotonicEffect GetTimeEffect async-time-handler sync-time-handler sim-time-handler])
(import doeff_cluster._shared.core.clock [datetime-of-epoch-ms monotonic-ms-of])


(defhandler steady-wall [#^ int started-ms #^ int started-mono-ms]
  ;; 設計検証の盲検 B の形: 壁の時計を「起動の刻 + 単調な時計の経過」に替える — 壁の差が単調な差と同じになり、suspend を拾わない。
  (GetTimeEffect []
    (<- mono float (GetMonotonic))
    (resume (datetime-of-epoch-ms (+ started-ms (- (monotonic-ms-of mono) started-mono-ms))))))

(defhandler shifted-monotonic [#^ float ahead]
  (GetMonotonicEffect []
    (<- mono float effect)
    (resume (+ mono ahead))))

(defn sourced-handlers [steady-now boottime]
  [(async-time-handler :now steady-now)
   (sync-time-handler :monotonic boottime)])


(defn default-handlers [clock]
  ;; 発火しない: 本番の答え手を引数なしで並べる・検と模擬の仮想の時計。
  [(async-time-handler) (sync-time-handler) (sim-time-handler :clock clock)])
