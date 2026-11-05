;;; Sample (a file with a deliberately written violation): answer a clock effect outside doeff-time's answerers, or pass a clock source to an answerer.
;;; doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers fires at lines 10, 15, 20, 21 and does not fire at lines 24-26.
(require doeff-hy.macros [defhandler <- val])
(import doeff_time [GetMonotonic GetMonotonicEffect GetTimeEffect async-time-handler sync-time-handler sim-time-handler])
(import doeff_cluster._shared.core.clock [datetime-of-epoch-ms monotonic-ms-of])


(defhandler steady-wall [#^ int started-ms #^ int started-mono-ms]
  ;; Shape of blind B of the design review: replace the wall clock with "start time + elapsed monotonic clock" -- the wall difference becomes the same as the monotonic difference, and a suspend is not picked up.
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
  ;; Does not fire: list the production answerers with no arguments -- the virtual clock of tests and mocks.
  [(async-time-handler) (sync-time-handler) (sim-time-handler :clock clock)])
