;;; Instrument for the lag of the listening loop (the loop lag in the header comment of _aiohttp_http_server.hy -- the stretch of the screen's ws round trip is
;;; read on the listening side, separated from the CPU wait of the measuring client).
;;;   (1) One line of a window reports the beat count, the maximum, and the 99% (sorted ascending, the ceil(0.99 n)-th) in ms
;;;   (2) On a real asyncio loop, blocking for 0.15 seconds between beats makes the maximum of the window line exceed 100 ms (it measures oversleep)
;;;   (3) After the listening loop is started, the lag instrument is running on that loop
(require doeff-hy.macros [deftest <- val])
(import asyncio)
(import contextlib [redirect-stderr])
(import io [StringIO])
(import re)
(import time)
(import pytest)
(import doeff_core_effects._aiohttp_http_server [EDGE-KEY EDGE-LAG-METERS WebEdge edge-lag-line meter-edge-lag])

;; The waits in this file are real time (what they are about is in this file's row of the roster tests/realtime_tests.toml).
(val pytestmark pytest.mark.realtime)

;; The beat and window (seconds) of the instrument in (2), and the seconds the loop is blocked between beats.
(val TICK-SECONDS 0.01)
(val WINDOW-SECONDS 0.3)
(val BLOCK-SECONDS 0.15)


(deftest test-a-window-line-names-the-tick-count-the-longest-and-the-99th-percentile
  ;; Failure case (1): a window of 100 beats from 1 ms to 100 ms has maximum 100.0 ms and the 99% is the 99th, 99.0 ms.
  (<- line (edge-lag-line (tuple (gfor k (range 100 0 -1) (/ k 1000.0))) 60.0))
  (assert (= line "edge-loop lag: window=60s ticks=100 max_ms=100.0 p99_ms=99.0") line))


(deftest test-a-blocked-loop-shows-as-an-overshoot-in-the-window-line
  ;; Failure case (2): run the instrument on a real asyncio loop; if the loop is blocked for 0.15 seconds between beats, the maximum of that window exceeds 100 ms. An instrument that does not
  ;; measure oversleep (for example, one that counts the slept seconds rather than the scheduled time) does not reach 100 ms.
  (val loop (asyncio.new-event-loop))
  (val said (StringIO))
  (with [(redirect-stderr said)]
    (val meter (.create-task loop (meter-edge-lag TICK-SECONDS WINDOW-SECONDS)))
    (.call-later loop (* 3 TICK-SECONDS) time.sleep BLOCK-SECONDS)
    (.run-until-complete loop (asyncio.sleep (+ WINDOW-SECONDS BLOCK-SECONDS)))
    (.cancel meter)
    (.run-until-complete loop (asyncio.gather meter :return-exceptions True))
    (.close loop))
  (val found (re.search r"edge-loop lag: window=0s ticks=\d+ max_ms=([0-9.]+) p99_ms=[0-9.]+" (.getvalue said)))
  (assert (is-not found None) (.getvalue said))
  (assert (> (float (.group found 1)) 100.0) (.getvalue said)))


(deftest test-the-edge-loop-carries-a-running-lag-meter
  ;; Failure case (3): after the listening loop is started, the instrument task is running on that loop without finishing (there is no version without the instrument in the roster).
  (val loop (.edge-loop (WebEdge)))
  (val meter (.get EDGE-LAG-METERS EDGE-KEY))
  (assert (is-not meter None))
  (assert (not (.done meter)))
  (assert (.is-running loop)))
