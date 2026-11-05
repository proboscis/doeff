;;; The heap-freeze effect -- once, on the beat when startup loading is done, take the objects alive right now out of later GC scans (
;;; ADR-DOE-CORE-EFFECTS-004). A foundation vocabulary with no business terms.
;;;
;;;   CollectAndFreeze  Collect, then freeze. Answer = the number of frozen objects (int).
;;;
;;; Why: a process holding a large cache that barely changes after startup (tens of thousands of rows of objects) runs generation-2 collections on allocation counts, and each time
;;; it scans everything and lengthens one pass of the processing loop (measured on agora's screen: 5 ms without collection / 32 ms with). The program knows nothing about GC and only asks for the freeze at a boundary.
;;;
;;; Why this is a separate module from the meter (meter_effects.hy): the meter only accumulates and reads observations and does not change process behavior. Freezing is an operation that changes the process's GC state,
;;; and programs without a meter use it too. Putting them together would mean a mock that swaps out the meter also swaps out GC operations.
;;;
;;; Answerers: gc-freeze-handler (gc_freeze.hy -- real) and scripted-freeze-handler (scripted_freeze.hy -- does not touch GC, answers a decided number).
(require doeff-hy.macros [defeffect])


(defeffect CollectAndFreeze
  "Collect, then freeze (see the header comment). Answer = the number of frozen objects."
  {:fields []
   :answer int
   :tags {:context "heap" :role "foundation"}})
