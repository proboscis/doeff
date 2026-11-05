;;; Properties of only the memory handler memory-latest-handler (the same contract as the real one is in test_latest_contract.hy; real-only properties are in test_process_latest.hy).
;;;
;;;   * Each publish or read makes one session round trip (a Get or Put going to state) -- the storage is the value of one session, and the clause rewrites its content.
;;;     A shape that takes out two session values per publish, or a shape that re-puts the value, increases the step count of tests of heavy publishers by that much (after 7d38d5bce, which held the doorbell set in
;;;     a second session value, the agora-controllers screen and turn-simulation tests went up +4,732 and +4,852 steps).
(require doeff-hy.macros [defk defhandler deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [run with_handlers])
(import doeff_core_effects [Get Put])
(import doeff_core_effects [state])
(import doeff_core_effects [PublishLatest ReadLatest])
(import doeff_core_effects [memory-latest-handler])


(defrecord Progress
  "Test value: a synchronous progress."
  (#^ str waiting))


;; The state key that holds the instrument's count (the instrument does not count round trips of this key).
(val COUNTED "test-memory-latest/session-accesses")
(val TIMES 10)


(defk counted-once []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "To advance the instrument's count (state key COUNTED) by one."
  (<- seen int (Get COUNTED))
  (<- (Put COUNTED (+ seen 1)))
  None)


(defhandler session-accesses
  "Test instrument: counts the Get and Put going from the inner handler to state, and passes them on to the outer state as they are."
  (Get [key]
    (when (!= key COUNTED)
      (<- (counted-once)))
    (reperform effect))
  (Put [key value]
    (when (!= key COUNTED)
      (<- (counted-once)))
    (reperform effect)))


(defk touches-of-publish-and-read [times]
  {:pre [(: times int)] :post [(: % int)] :tags {:context "latest-test" :role "program"}}
  "After the first publish that creates the storage, publish and read `times` times each, and answer the number of Get and Put that went to state in between."
  (<- (Put COUNTED 0))
  (<- (PublishLatest (Progress :waiting "creating")))
  (<- before int (Get COUNTED))
  (for [n (range times)]
    (<- (PublishLatest (Progress :waiting (str n))))
    (<- (ReadLatest Progress)))
  (<- after int (Get COUNTED))
  (- after before))


(deftest test-each-publish-and-read-touches-the-session-once
  (val touched (run (with_handlers [(state) session-accesses memory-latest-handler] (touches-of-publish-and-read TIMES))))
  (assert (= touched (* 2 TIMES))
          (.format "{} publishes and {} reads made {} session round trips (one each would be {})" TIMES TIMES touched (* 2 TIMES))))
