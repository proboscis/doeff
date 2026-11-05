;;; Properties of only the real latest-value responder process-latest-handler. Contract properties are in test_latest_contract.hy.
;;;
;;;   * Responders installed under the same name share one store whether in another run or another thread (the processing loop's run puts, and the probe's other run reads)
;;;   * A different name means a different store
;;;   * A value put by a run in another thread wakes this run's wait until it changes (AwaitLatest); a cancelled wait leaves no bell behind
;;; The store remains for the whole test process, so each test uses a different name.
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects [process-latest-handler]
        doeff_core_effects._process_latest [BELLS])
(import doeff_core_effects [scheduled Spawn Wait Cancel CreatePromise CompletePromise])


(defrecord Progress
  "A test value: synchronization progress."
  (#^ str waiting))


(defk publish [value]
  {:pre [(: value Progress)] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "Put value (a Program run in another run)."
  (<- (PublishLatest value))
  None)


(defk read-progress []
  {:pre [] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Read the latest Progress value (a Program run in another run or another thread)."
  (<- value (ReadLatest Progress))
  value)


(deftest test-two-runs-with-the-same-name-share-one-board
  (run (with_handlers [(state) (process-latest-handler "process-latest-shared")] (publish (Progress :waiting "folded"))))
  (val value (run (with_handlers [(state) (process-latest-handler "process-latest-shared")] (read-progress))))
  (assert (= value (Progress :waiting "folded")) (.format "the read of the value put by another run is {!r}" value)))


(deftest test-a-run-in-another-thread-reads-the-same-board
  (run (with_handlers [(state) (process-latest-handler "process-latest-thread")] (publish (Progress :waiting "folded"))))
  (val seen [])
  (val reader (threading.Thread
                 :target (fn [] (.append seen (run (with_handlers [(state) (process-latest-handler "process-latest-thread")] (read-progress)))))))
  (.start reader)
  (.join reader 10.0)
  (assert (= seen [(Progress :waiting "folded")]) (.format "the read in another thread is {!r}" seen)))


(deftest test-different-names-do-not-share
  (run (with_handlers [(state) (process-latest-handler "process-latest-left")] (publish (Progress :waiting "folded"))))
  (val value (run (with_handlers [(state) (process-latest-handler "process-latest-right")] (read-progress))))
  (assert (is value None) (.format "the read of the store under a different name is {!r}" value)))


;; --- Properties of only the real wait until change (AwaitLatest) -----------------------------------------------------
;;   * A value put by a run in another thread wakes this run's wait (the shape where a snapshot put by the processing loop's run wakes the wait of the probe's other run)
;;   * A cancelled wait leaves no bell behind (the per-process store of bells returns to empty)

(defk awaited [seen]
  {:pre [(: seen (| Progress None))] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Wait until the latest Progress value becomes something other than seen, and answer that value."
  (<- value (AwaitLatest Progress seen))
  value)


(deftest test-a-publish-from-another-thread-wakes-the-await
  ;; Failure case = in a shape where the putter does not ring the bell (or an external completion does not wake the scheduler), the wait does not wake and stops at the join limit.
  (val name "process-latest-await-thread")
  (val first (Progress :waiting "list"))
  (run (with_handlers [(state) (process-latest-handler name)] (publish first)))
  (val timer (threading.Timer 0.05 (fn [] (run (with_handlers [(state) (process-latest-handler name)] (publish (Progress :waiting "summarized")))))))
  (setv timer.daemon True)
  (.start timer)
  (val seen [])
  (val waiter (threading.Thread
                 :target (fn [] (.append seen (run (scheduled (with_handlers [(state) (process-latest-handler name)] (awaited first))))))
                 :daemon True))
  (.start waiter)
  (.join waiter 10.0)
  (assert (= seen [(Progress :waiting "summarized")]) (.format "the answer of the wait after another thread put is {!r}" seen)))


(defk let-others-run []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "To run the task spawned first (it enters the wait and unwinds on cancellation) (waiting for the later-spawned task to finish - no clock is used)."
  (<- promise (CreatePromise))
  (<- helper (Spawn (CompletePromise promise None)))
  (<- (Wait helper))
  None)


(defk cancelled-then-published [name first]
  {:pre [(: name str) (: first Progress)] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "To spawn a wait, let it enter the bell wait, then cancel it, and put a value after that (the caller checks whether the cancellation callback removed the bell)."
  (<- waiter (Spawn (awaited first)))
  (<- (let-others-run))
  (assert (= (len (.get (get BELLS name) Progress #())) 1) (.format "the bell after entering the wait is {!r}" (get BELLS name)))
  (<- (Cancel waiter))
  ;; Keep running until the cancelled task finishes unwinding (if the root returns first, the unwinding is left half-run).
  (<- (let-others-run))
  None)


(deftest test-a-cancelled-await-leaves-no-bell
  ;; Failure case = in a shape with no cancellation callback, the bell of the cancelled wait stays in the store, and every put keeps ringing a promise with no waiter.
  (val name "process-latest-await-cancel")
  (val first (Progress :waiting "list"))
  (run (with_handlers [(state) (process-latest-handler name)] (publish first)))
  (run (scheduled (with_handlers [(state) (process-latest-handler name)] (cancelled-then-published name first))))
  (assert (= (.get (get BELLS name) Progress #()) #()) (.format "the bell after cancellation is {!r}" (get BELLS name))))
