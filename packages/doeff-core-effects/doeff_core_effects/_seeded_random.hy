;;; Fake handler for randomness, seeded-random-handler: answers RandomBytes with bytes determined by the seed and the order of calls
;;; within this run. So that mocks and tests get the same per-startup identifiers every time without touching the OS's randomness.
;;;
;;; How values are made: the n-th call (0-based) returns the first count bytes of the sha256("<seed>:<n>:<block>") digests laid out as blocks 0, 1, ...
;;; The same seed and the same call order give the same values, and each call gives a different value. The call count is state within this run
;;; (a session value) and is not shared with other runs. An outer state handler is required.
(require doeff-hy.macros [defhandler defk var val])
(val MODULE-TAGS {:context "random" :role "foundation"})
(import hashlib)
(import doeff_core_effects._random_effects [RandomBytes])


(defk seeded-bytes [seed call count]
  {:pre [(: seed int) (: call int) (>= call 0) (: count int) (>= count 0)]
   :post [(: % bytes) (= (len %) count)]
   :tags {:context "random" :role "foundation"}}
  "The count bytes for the call-th call with seed (made as described in the header comment)."
  (val blocks (// (+ count 31) 32))
  (val digests (lfor block (range blocks) (.digest (hashlib.sha256 (.encode (.format "{}:{}:{}" seed call block))))))
  (cut (.join b"" digests) count))


(defhandler seeded-random-handler [#^ int seed]
  "Answers RandomBytes with count bytes determined by seed and the order of calls within this run (does not touch the OS's randomness)."
  ;; Why it stays an argument: the seed is a scripted value chosen per test and mock, not a setting read through Ask.
  (session var calls 0)
  (RandomBytes [count]
    (val answer (! (seeded-bytes seed calls count)))
    (:= calls (+ calls 1))
    (resume answer)))
