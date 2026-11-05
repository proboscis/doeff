;;; Contract test for the latest value -- the real one (process-latest-handler) and the fake (memory-latest-handler) that answer the same effects (PublishLatest, ReadLatest)
;;; pass through the same deftest (decision of #1107). The interpreter assembly is in latest_contract_handlers.hy.
;;;
;;; Properties checked:
;;;   * reading a type that has not been published gives None
;;;   * a published value is read back as is
;;;   * publishing the same type again makes the last value the one that is read
;;;   * the key is exactly the value's type -- values of different types do not overwrite each other. A value of a child type cannot be read by the parent type
;;;   * the wait until change (AwaitLatest) does not wait if the value already differs, wakes with a value published by another task, and does not break the next wait even if cancelled
;;; Properties of the real one only (sharing across runs and threads) are in test_process_latest.hy.
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_core_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects [Spawn Wait Cancel CreatePromise CompletePromise])


(defrecord Progress
  "Check value 1: synchronization progress."
  (#^ str waiting))


(defrecord Faults
  "Check value 2: number of faults."
  (#^ int total))


(defclass [(dataclass :frozen True)] LoudFaults [Faults]
  "Child type of Faults (to check that the key is exactly the type).")


(deftest test-a-type-never-published-reads-none
  {:interpreters ["process-latest" "memory-latest"]}
  (<- value (ReadLatest Progress))
  (assert (is value None) (.format "Read of an unpublished type is {!r}" value)))


(deftest test-a-published-value-is-read-back
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "list of record tables")))
  (<- value (ReadLatest Progress))
  (assert (= value (Progress :waiting "list of record tables")) (.format "Read of the published value is {!r}" value)))


(deftest test-the-last-publish-wins
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "list")))
  (<- (PublishLatest (Progress :waiting "folded")))
  (<- value (ReadLatest Progress))
  (assert (= value (Progress :waiting "folded")) (.format "Read after republishing is {!r}" value)))


(deftest test-the-key-is-the-exact-type
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "list")))
  (<- (PublishLatest (Faults :total 2)))
  (<- (PublishLatest (LoudFaults :total 7)))
  (<- progress (ReadLatest Progress))
  (<- faults (ReadLatest Faults))
  (<- loud (ReadLatest LoudFaults))
  (assert (= progress (Progress :waiting "list")) (.format "Progress after publishing Faults is {!r}" progress))
  (assert (= faults (Faults :total 2)) (.format "Faults after publishing the child type LoudFaults is {!r} (not overwritten by the child type)" faults))
  (assert (= loud (LoudFaults :total 7)) (.format "Read of LoudFaults is {!r}" loud)))


;; --- Wait until change (AwaitLatest) ------------------------------------------------------------------
;; Reads, when published, the snapshot that the writer's run published, from another run without re-checking at fixed intervals. Both answerers share the same contract:
;;   * if the current value already differs from seen, answer without waiting
;;   * wake with a value published by another task while waiting, and answer that value
;;   * even if published after a cancelled wait, the next wait wakes with the published value (cancellation does not break the store)

(defk waiting-for [seen]
  {:pre [(: seen (| Progress None))] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Waits until Progress's latest value differs from seen and answers that value (a Program run in a separate task)."
  (<- value (AwaitLatest Progress seen))
  value)


(defk let-others-run []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "To keep running until the earlier-spawned task enters its bell wait (waits for the later-spawned task to finish -- no clock used)."
  (<- promise (CreatePromise))
  (<- helper (Spawn (CompletePromise promise None)))
  (<- (Wait helper))
  None)


(deftest test-await-answers-at-once-when-the-value-already-differs
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "list")))
  (<- value (AwaitLatest Progress None))
  (assert (= value (Progress :waiting "list")) (.format "Answer of the wait for an already published value is {!r}" value)))


(deftest test-await-wakes-on-a-publish-from-another-task
  {:interpreters ["process-latest" "memory-latest"]}
  (val first (Progress :waiting "list"))
  (<- (PublishLatest first))
  (<- waiter (Spawn (waiting-for first)))
  (<- (let-others-run))
  (<- (PublishLatest (Progress :waiting "summarized")))
  (<- value (Wait waiter))
  (assert (= value (Progress :waiting "summarized")) (.format "Answer of the wait after republishing is {!r}" value)))


(deftest test-a-cancelled-await-does-not-break-the-next-one
  {:interpreters ["process-latest" "memory-latest"]}
  (val first (Progress :waiting "list"))
  (<- (PublishLatest first))
  (<- cancelled (Spawn (waiting-for first)))
  (<- (let-others-run))
  (<- (Cancel cancelled))
  (val second (Progress :waiting "summarized"))
  (<- (PublishLatest second))
  (<- waiter (Spawn (waiting-for second)))
  (<- (let-others-run))
  (<- (PublishLatest (Progress :waiting "closed")))
  (<- value (Wait waiter))
  (assert (= value (Progress :waiting "closed")) (.format "Answer of the wait after cancellation is {!r}" value)))
