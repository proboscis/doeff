;;; Fixture for DOEFF211 (a Hy test does not sleep on the wall clock unless it declares realtime) — fires on time.sleep / asyncio.sleep and
;;; their rewordings where not declared, and does not fire inside tests that declare realtime (deftest, plain functions), zero-second yields, event waits, or unread forms.
;;; Two tests read the end-of-line markers (on the same fixture; if the rule's reading and the registry's reading disagree, one of them goes red):
;;;   "; fires"  ... a line that should fire. packages/doeff-linter/tests/test_sleep.rs compares the set of marked lines with the set of linter hit lines.
;;;   "; declares"  ... the head line of a test that declares realtime (a deftest, or a plain function declaring by decorator). tests/semgrep/test_realtime_tests_registry.py compares it with the set of names the registry's reading picks up.
;;; This file is not executed (a fixture that is only read). The fixture for module-level declaration is the neighboring realtime_module.hy.
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
;; Fires: waiting on the wall clock without declaring

(deftest test-waits-for-the-server-by-sleeping
  (start-server)
  (time.sleep 0.3)  ; fires
  (assert (server-ready)))


(defk poll-until-ready [probe deadline]
  {:pre [(callable probe)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "Test helper (whether a program or a plain callable, a sleep inside a test file is counted the same)."
  (while (and (not (probe)) (< (time.monotonic) deadline))
    (time.sleep 0.05))  ; fires
  (probe))


(defn :async #^ bool all-closed [sessions]
  (while (not (all (gfor s sessions s.closed)))
    (await (asyncio.sleep 0.02)))  ; fires
  True)


(deftest test-waits-through-aliases
  (clock.sleep 0.2)  ; fires
  (<- (Await (rest 0.2)))  ; fires
  (.sleep time 0.1)  ; fires
  (assert True))


(deftest test-rewords-the-sleep-as-an-event-nobody-sets
  ;; An Event made on the spot is never set by anyone — waiting with seconds is a rewording of sleeping.
  (.wait (threading.Event) 0.1)  ; fires
  (assert True))


(deftest test-marked-with-another-mark
  {:marks ["slow"]}
  (time.sleep 1.0)  ; fires
  (assert True))


(deftest test-with-a-map-that-is-not-the-meta
  (assert True)
  {:marks ["realtime"]}
  (time.sleep 1.0)  ; fires
  (assert True))


(defk helper-with-marks-in-its-contract []
  {:pre [] :post [(: % None)] :marks ["realtime"] :tags {:context "sample" :role "program"}}
  "The map of a definition that is not a deftest is not a declaration."
  (time.sleep 1.0)  ; fires
  None)


(defn [pytest.mark.slow] #^ None test-decorated-with-another-mark []
  (time.sleep 1.0))  ; fires


(defn test-with-the-mark-in-its-parameters [pytest.mark.realtime]
  ;; The `[…]` after the name is the parameter list (the decorator list comes before the name).
  (time.sleep 1.0))  ; fires


(defn declares-inside-a-function []
  ;; A binding that is not top-level is not a module-level declaration.
  (val pytestmark pytest.mark.realtime)
  (time.sleep 1.0))  ; fires


(setv marks pytest.mark.realtime)
(time.sleep 2.0)  ; fires


;; ---------------------------------------------------------------------------
;; Does not fire: a deftest that declares realtime (real time itself is the subject)

(deftest test-two-threads-overlap  ; declares
  {:marks ["realtime"]}
  (start-two-threads)
  (time.sleep 0.3)
  (assert (= (peak) 2)))


(deftest test-a-real-process-ends [tmp-path]  ; declares
  "The meta after the leading docstring and the parameter list."
  {:interpreters ["sample"] :marks ["slow" "realtime"]}
  (<- child (start tmp-path))
  (while (alive child)
    (<- (Await (asyncio.sleep 0.05))))
  (.wait (threading.Event) 0.1)
  (assert True))


(deftest test-marks-as-a-tuple-of-symbols  ; declares
  {:marks #(realtime)}
  (run-later (fn [] (time.sleep 0.1)))
  (assert True))


(defn [pytest.mark.slow pytest.mark.realtime] #^ None test-a-plain-function-declares-by-its-decorator []  ; declares
  "A plain-function test (not a deftest) declares by the marks in its decorator list. A program's sleep defined inside the function is also inside the declared function."
  (defk counted []
    {:pre [] :post [(: % int)]}
    (time.sleep 0.1)
    1)
  (assert (= (run (counted)) 1)))


;; ---------------------------------------------------------------------------
;; Does not fire: forms that do not wait on time, event waits, and unread forms

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
  ;; (time.sleep 9) — inside a comment
  #_(time.sleep 9)
  (val quoted '(time.sleep 9))
  (.write-text (/ tmp-path "nap.hy") "(import time)\n(time.sleep 30)\n")
  (assert True))
