;;; Tests for offloaded-lock-handler (os_file.hy) -- on one scheduler, while a task holding a lock has moved to waiting, another task
;;; comes to take the same lock, and the scheduler thread must not be blocked. Uses a real lock file (under tmp).
;;;
;;;   (a) While task A, which holds the lock, waits for an outside signal, task B comes to take the same lock. A releases the lock after the signal, and B takes and releases it. With os-file-handler alone
;;;       (waiting on the thread that called the lock acquire), B's wait blocks the scheduler thread, A's receipt of the signal never runs, and A cannot release the lock, so
;;;       the test goes red at SCENARIO-LIMIT (the failure form of this test).
;;;   (b) B is cancelled while B waits for the lock. The lock that B's thread took after A released it is given back on the spot by the cleanup -- the next task C can take the same lock
;;;       (otherwise C would keep waiting for a lock with no owner and go red at SCENARIO-LIMIT).
;;;
;;; The scenario runs in a new VM on a daemon thread separate from the test thread; if there is no answer in SCENARIO-LIMIT seconds, the test fails naming it (the blocked thread is
;;; left as a daemon -- the same practice as test_offloaded_process_cancel.hy).
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import doeff [Program with_handlers])
(import doeff_core_effects [AcquireLock ReleaseLock LockHeld FileFailed])
(import doeff_core_effects [os-file-handler offloaded-lock-handler])
(import doeff_core_effects._offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects [scheduled CreateExternalPromise Task Wait Spawn Cancel TaskCancelledError])

;; Upper limit (seconds) for one scenario. It normally ends within 1 second. Past it, the scheduler thread is considered blocked and the test fails.
(val SCENARIO-LIMIT 5.0)
;; Seconds A waits for the outside signal while holding the lock (B comes to take the lock during this).
(val HOLD-SECONDS 0.2)


(defrecord Contended
  "What (a) saw: holder-released = whether A could release the lock; waiter-got = whether B could take the lock."
  (#^ bool holder-released)
  (#^ bool waiter-got))


(defrecord Abandoned
  "What (b) saw: ended-by-cancel = whether the cancelled B ended with TaskCancelledError; next-got = whether the later C could take the lock."
  (#^ bool ended-by-cancel)
  (#^ bool next-got))


(defk outside-signal [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "To wait for a signal that the test shell thread completes after seconds seconds (the scheduler runs other tasks while waiting)."
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk take-and-release [path]
  {:pre [(: path str)] :post [(: % bool)] :tags {:context "file-test" :role "program"}}
  "To take and release the lock (the answer = whether it could be taken)."
  (<- held (| LockHeld FileFailed) (AcquireLock path))
  (when (isinstance held FileFailed)
    (return False))
  (<- (ReleaseLock held))
  True)


(defk contended [path]
  {:pre [(: path str)] :post [(: % Contended)] :tags {:context "file-test" :role "program"}}
  "Scenario (a): A takes the lock, spawns B, waits for the outside signal, then releases the lock and waits for B to finish."
  (<- held LockHeld (AcquireLock path))
  (<- waiter Task (Spawn (take-and-release path)))
  (<- (outside-signal HOLD-SECONDS))
  (<- released (| FileFailed None) (ReleaseLock held))
  (<- got bool (Wait waiter))
  (Contended :holder-released (is released None) :waiter-got got))


(defk ended-by-cancel [task]
  {:pre [(: task Task)] :post [(: % bool)] :tags {:context "file-test" :role "program"}}
  "To read whether the cancelled task ended with TaskCancelledError."
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk abandoned [path]
  {:pre [(: path str)] :post [(: % Abandoned)] :tags {:context "file-test" :role "program"}}
  "Scenario (b): A takes the lock, spawns B and makes it wait for the lock, cancels B and then releases the lock, and checks whether the later C can take it."
  (<- held LockHeld (AcquireLock path))
  (<- waiter Task (Spawn (take-and-release path)))
  ;; Wait until B hands its lock acquire to a thread (the scheduler runs B during the signal).
  (<- (outside-signal HOLD-SECONDS))
  (<- (Cancel waiter))
  (<- cancelled bool (ended-by-cancel waiter))
  (<- (ReleaseLock held))
  (<- got bool (take-and-release path))
  (Abandoned :ended-by-cancel cancelled :next-got got))


(defk settled-within [scenario what]
  {:pre [(: scenario Program) (: what str)] :post [(: % "the scenario's answer (the type depends on the scenario)")]
   :tags {:context "file-test" :role "program"}}
  "To run the scenario once, in a new VM on a daemon thread separate from the test thread, under os-file-handler, offloaded-lock-handler (inner) and scheduled,
   waiting for the answer up to SCENARIO-LIMIT seconds. Past it, fail naming what (see the header note)."
  (val running (.submit (ThreadPerCall) run-detached
                        (scheduled (with_handlers [os-file-handler offloaded-lock-handler] scenario))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} did not finish in {} seconds (the scheduler thread was blocked)" what SCENARIO-LIMIT))))))


(deftest test-a-task-waiting-for-a-held-lock-does-not-block-the-holder [tmp-path]
  ;; (a) (header note).
  (<- seen Contended (settled-within (contended (str (/ tmp-path "lock"))) "the lock-holding task and the waiting task"))
  (assert seen.holder-released seen)
  (assert seen.waiter-got seen))


(deftest test-a-lock-taken-after-its-waiter-was-cancelled-is-given-back [tmp-path]
  ;; (b) (header note).
  (<- seen Abandoned (settled-within (abandoned (str (/ tmp-path "lock"))) "the lock after the cancelled wait"))
  (assert seen.ended-by-cancel seen)
  (assert seen.next-got seen))
