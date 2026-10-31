;;; 待ち受けの loop の遅れの計器(_aiohttp_http_server.hy の頭の註の loop の遅れ — 画面の ws の往復の延びを、
;;; 測りの client の CPU の待ちと分けて待ち受けの側で読む)。
;;;   (1) 窓の 1 行は、拍の数・最長・99%(並べて下から ceil(0.99 n) 番目)を ms で名乗る
;;;   (2) 本物の asyncio の loop で、拍の間を 0.15 秒塞ぐと、窓の 1 行の最長が 100 ms を越える(寝過ごしを測っている)
;;;   (3) 待ち受けの loop を引くと、遅れの計器がその loop の上で走り出している
(require doeff-hy.macros [deftest <- val])
(import asyncio)
(import contextlib [redirect-stderr])
(import io [StringIO])
(import re)
(import time)
(import pytest)
(import doeff_core_effects._aiohttp_http_server [EDGE-KEY EDGE-LAG-METERS WebEdge edge-lag-line meter-edge-lag])

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)。
(val pytestmark pytest.mark.realtime)

;; (2) の計器の拍と窓(秒)と、拍の間に loop を塞ぐ秒。
(val TICK-SECONDS 0.01)
(val WINDOW-SECONDS 0.3)
(val BLOCK-SECONDS 0.15)


(deftest test-a-window-line-names-the-tick-count-the-longest-and-the-99th-percentile
  ;; 失敗ケース (1): 1 ms から 100 ms の 100 拍の窓は、最長 100.0 ms・99% は 99 番目の 99.0 ms。
  (<- line (edge-lag-line (tuple (gfor k (range 100 0 -1) (/ k 1000.0))) 60.0))
  (assert (= line "edge-loop lag: window=60s ticks=100 max_ms=100.0 p99_ms=99.0") line))


(deftest test-a-blocked-loop-shows-as-an-overshoot-in-the-window-line
  ;; 失敗ケース (2): 本物の asyncio の loop で計器を走らせ、拍の間に loop を 0.15 秒塞ぐと、その窓の最長が 100 ms を越える。寝過ごしを
  ;; 測らない計器(予定の刻でなく寝た秒を数える形など)は 100 ms に届かない。
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
  ;; 失敗ケース (3): 待ち受けの loop を引いた後、計器の task がその loop の上で終わらずに走っている(計器を載せない版は名簿に無い)。
  (val loop (.edge-loop (WebEdge)))
  (val meter (.get EDGE-LAG-METERS EDGE-KEY))
  (assert (is-not meter None))
  (assert (not (.done meter)))
  (assert (.is-running loop)))
