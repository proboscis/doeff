;;; DOEFF211(Hy の検は realtime を名乗らない限り壁の時計で眠らない)の検体 — 名乗っていない所の time.sleep / asyncio.sleep と
;;; 言い換えを鳴らし、realtime を名乗る検(deftest・素の関数)の中・0 秒の譲り・出来事の待ち・読まない form は鳴らさない。
;;; 行末の印を 2 つの検が読む(同じ検体で、規則の読みと名簿の読みが食い違うとどちらかが赤になる):
;;;   「; 鳴る」  … 鳴るべき行。packages/doeff-linter/tests/test_sleep.rs が、印の行の集合と linter の当たりの行の集合を突き合わせる。
;;;   「; 名乗る」… realtime を名乗る検(deftest か、decorator で名乗る素の関数)の頭の行。tests/semgrep/test_realtime_tests_registry.py が、名簿の読みが拾う名の集合と突き合わせる。
;;; この file は実行しない(読むだけの検体)。module の段の名乗りの検体は隣の realtime_module.hy。
(require doeff-hy.macros [deftest defk <- val])
(import asyncio)
(import threading)
(import time)
(import time :as clock)
(import asyncio [sleep :as rest])
(import pytest)
(import doeff [Await])
(import doeff_time [Delay])


;; ---------------------------------------------------------------------------
;; 鳴る: 名乗らずに壁の時計で待つ

(deftest test-waits-for-the-server-by-sleeping
  (start-server)
  (time.sleep 0.3)  ; 鳴る
  (assert (server-ready)))


(defk poll-until-ready [probe deadline]
  {:pre [(callable probe)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "検の helper(program でも素の callable でも、検の file の中の眠りは同じに数える)。"
  (while (and (not (probe)) (< (time.monotonic) deadline))
    (time.sleep 0.05))  ; 鳴る
  (probe))


(defn :async #^ bool all-closed [sessions]
  (while (not (all (gfor s sessions s.closed)))
    (await (asyncio.sleep 0.02)))  ; 鳴る
  True)


(deftest test-waits-through-aliases
  (clock.sleep 0.2)  ; 鳴る
  (<- (Await (rest 0.2)))  ; 鳴る
  (.sleep time 0.1)  ; 鳴る
  (assert True))


(deftest test-rewords-the-sleep-as-an-event-nobody-sets
  ;; その場で作った Event は誰も set しない — 秒つきで待つのは眠りの言い換え。
  (.wait (threading.Event) 0.1)  ; 鳴る
  (assert True))


(deftest test-marked-with-another-mark
  {:marks ["slow"]}
  (time.sleep 1.0)  ; 鳴る
  (assert True))


(deftest test-with-a-map-that-is-not-the-meta
  (assert True)
  {:marks ["realtime"]}
  (time.sleep 1.0)  ; 鳴る
  (assert True))


(defk helper-with-marks-in-its-contract []
  {:pre [] :post [(: % None)] :marks ["realtime"] :tags {:context "sample" :role "program"}}
  "deftest でない定義の map は名乗りではない。"
  (time.sleep 1.0)  ; 鳴る
  None)


(defn [pytest.mark.slow] #^ None test-decorated-with-another-mark []
  (time.sleep 1.0))  ; 鳴る


(defn test-with-the-mark-in-its-parameters [pytest.mark.realtime]
  ;; 名の後の `[…]` は引数の並び(decorator の並びは名より前)。
  (time.sleep 1.0))  ; 鳴る


(defn declares-inside-a-function []
  ;; 最上位でない束ねは module の名乗りではない。
  (val pytestmark pytest.mark.realtime)
  (time.sleep 1.0))  ; 鳴る


(setv marks pytest.mark.realtime)
(time.sleep 2.0)  ; 鳴る


;; ---------------------------------------------------------------------------
;; 鳴らない: realtime を名乗る deftest(実時間そのものが対象)

(deftest test-two-threads-overlap  ; 名乗る
  {:marks ["realtime"]}
  (start-two-threads)
  (time.sleep 0.3)
  (assert (= (peak) 2)))


(deftest test-a-real-process-ends [tmp-path]  ; 名乗る
  "先頭の docstring と引数の並びの後の meta。"
  {:interpreters ["sample"] :marks ["slow" "realtime"]}
  (<- child (start tmp-path))
  (while (alive child)
    (<- (Await (asyncio.sleep 0.05))))
  (.wait (threading.Event) 0.1)
  (assert True))


(deftest test-marks-as-a-tuple-of-symbols  ; 名乗る
  {:marks #(realtime)}
  (run-later (fn [] (time.sleep 0.1)))
  (assert True))


(defn [pytest.mark.slow pytest.mark.realtime] #^ None test-a-plain-function-declares-by-its-decorator []  ; 名乗る
  "素の関数の検(deftest でない)は、decorator の並びの印で名乗る。関数の中で定める program の眠りも、名乗った関数の中。"
  (defk counted []
    {:pre [] :post [(: % int)]}
    (time.sleep 0.1)
    1)
  (assert (= (run (counted)) 1)))


;; ---------------------------------------------------------------------------
;; 鳴らない: 時間を待たない形・出来事の待ち・読まない form

(deftest test-waits-on-the-virtual-clock
  (<- (Delay 5.0))
  (assert True))


(deftest test-yields-once
  (<- (Await (asyncio.sleep 0)))
  (time.sleep 0)
  (asyncio.sleep 0 1)
  (assert True))


(deftest test-waits-for-the-event-itself
  (setv ready (threading.Event))
  (start-server ready)
  (.wait ready 5)
  (.wait (threading.Event))
  (assert (.is-set ready)))


(deftest test-hands-a-sleeping-program-to-a-child-process [tmp-path]
  ;; (time.sleep 9) — 註の中
  #_(time.sleep 9)
  (val quoted '(time.sleep 9))
  (.write-text (/ tmp-path "nap.hy") "(import time)\n(time.sleep 30)\n")
  (assert True))
