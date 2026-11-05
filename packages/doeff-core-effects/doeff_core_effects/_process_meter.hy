;;; process-meter-handler, the real meter answerer (ADR-DOE-CORE-EFFECTS-004) - answers CountMetric, ObserveSeconds,
;;; ObserveSecondsBatch, SetGauge, and ReadMeter with one store per process.
;;;
;;; Purpose: in a long-running process, the processing loop's run accumulates into the meter, and another run (the probe's HTTP - it answers even while the processing loop is computing)
;;; reads the same meter. Session values exist only within a single run, so this module holds one "name -> store" map per process, and answerers
;;; installed under the same name (in different runs or threads) read and write the same store. No class is made (ADR-DOE-HY-007 R3, R4) - the store holds only
;;; an immutable cross-section, the writer's lock, and the GC pause box. Precedent for a module holding one per-process thing = the Await bridge in handlers.py.
;;;
;;;   Write  Take the lock, fold in the GC pause box, then build a new cross-section with the pure functions (counted, observed, observed-batch, gauged in meter_effects.hy)
;;;         and swap it in. The swap happens exactly once per write - everything within one name (seconds total, count, bucket counters) is seen at once
;;;         (an ObserveSecondsBatch sequence is also one write - the lock is taken once and the swap is done once).
;;;   Read   Take one reference to the current cross-section without waiting for the writer's lock (a read does not queue behind a blocked writer). If the lock is free, fold in the GC pause box
;;;         before reading (if not free, read without folding).
;;;   GC     If the settings have gc-pause-name, hook one callback onto gc.callbacks when the store is created, and accumulate the seconds from start to stop of each collection into the box. The callback
;;;         does not take the lock - a collection calls the callback on the thread that triggered it, so if a collection starts inside a thread holding the lock, a callback that re-acquires it
;;;         would make that thread wait on itself forever (threading.Lock cannot be re-acquired).
;;;
;;; Installing different settings under the same name is refused with ValueError (the same store is not written with mismatched bucket tables). Stores persist until the process ends.
;;; Only this module touches the clock, gc, and threads (the memory answerer memory_meter.hy does not).
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "meter" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections [deque])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import gc)
(import threading)
(import time)
(import doeff_core_effects._meter_effects [CountMetric EMPTY-METER MeterSettings MeterSnapshot ObserveSeconds ObserveSecondsBatch ReadMeter
                                         SetGauge counted gauged observed observed-batch])


(defrecord MeterPlace
  "A store for one name: settings = the settings when installed, lock = the writer's lock, pauses = the box of pause seconds that the GC callback accumulates. The cross-section itself is
   placed in SNAPSHOTS (name -> cross-section) and swapped on every write."
  {:tags {:context "meter" :role "foundation"}}
  (#^ MeterSettings settings)
  (#^ object lock)
  (#^ deque pauses))


;; One per process: name -> store, name -> current cross-section, and the lock for creating stores.
(val PLACES {})
(val SNAPSHOTS {})
(val PLACES-LOCK (threading.Lock))


(defk gc-pause-watch [pauses]
  {:pre [(: pauses deque)] :post [(: % Callable)] :tags {:context "meter" :role "foundation"}}
  "To build the callback that accumulates the seconds from start to stop of each collection into pauses (it takes no lock - see the header comment). The built callback is hooked onto gc.callbacks
   and is called from outside the VM as a bare function in the middle of a collection."
  (val started [None])
  (fn [phase info]
    (setv now (time.monotonic))
    (if (= phase "start")
        (setv (get started 0) now)
        (do
          (setv began (get started 0))
          (setv (get started 0) None)
          (when (is-not began None)
            (.append pauses (- now began)))))))


(defk meter-place [name settings]
  {:pre [(: name str) (: settings MeterSettings)] :post [(: % MeterPlace)] :tags {:context "meter" :role "foundation"}}
  "To look up the store for name when installing the answerer (create it if absent, and hook the GC callback if the settings have gc-pause-name). Refuse different settings under the same name."
  (with [PLACES-LOCK]
    (var place (.get PLACES name))
    (when (is place None)
      (:= place (MeterPlace :settings settings :lock (threading.Lock) :pauses (deque)))
      (setv (get PLACES name) place)
      (setv (get SNAPSHOTS name) EMPTY-METER)
      (when (is-not settings.gc-pause-name None)
        (<- watch Callable (gc-pause-watch place.pauses))
        (.append gc.callbacks watch)))
    (when (!= place.settings settings)
      (raise (ValueError (.format "meter store {!r} was installed with different settings: {!r} (current settings {!r})" name place.settings settings))))
    place))


(defk with-pauses [snapshot settings pauses]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: pauses tuple)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "To accumulate the GC pause seconds taken out of the box into the cross-section as observations of seconds under the settings' name (as is if there is no name)."
  (var folded snapshot)
  (when (is-not settings.gc-pause-name None)
    (for [seconds pauses]
      (<- next MeterSnapshot (observed folded settings settings.gc-pause-name seconds))
      (:= folded next)))
  folded)


(defk rewritten [name place change]
  {:pre [(: name str) (: place MeterPlace) (: change Callable)]
   :post [(: % None)] :tags {:context "meter" :role "foundation"}}
  "To apply change (a Program from cross-section to new cross-section) once, inside the writer's lock, to the cross-section with GC pauses folded in, and swap it in."
  (with [place.lock]
    (val pauses (tuple (gfor _ (range (len place.pauses)) (.popleft place.pauses))))
    (<- base MeterSnapshot (with-pauses (get SNAPSHOTS name) place.settings pauses))
    (<- next MeterSnapshot (change base))
    (setv (get SNAPSHOTS name) next))
  None)


(defhandler process-meter-handler [#^ str place-name #^ MeterSettings settings]
  "Answers CountMetric, ObserveSeconds, ObserveSecondsBatch, SetGauge, and ReadMeter with the one-per-process store place-name (see the header comment)."
  ;; Reason it stays an argument: place-name is the key for sharing the same store with other runs, and settings are the bucket table and GC pause name used when creating the store - both are values
  ;; decided at each installation site and cannot be told apart by Ask (meters with different names can be lined up in one set).
  (session val place (! (meter-place place-name settings)))
  (CountMetric [name amount]
    (<- (rewritten place-name place (fn [snapshot] (counted snapshot name amount))))
    (resume None))
  (ObserveSeconds [name seconds]
    (<- (rewritten place-name place (fn [snapshot] (observed snapshot place.settings name seconds))))
    (resume None))
  (ObserveSecondsBatch [observations]
    (<- (rewritten place-name place (fn [snapshot] (observed-batch snapshot place.settings observations))))
    (resume None))
  (SetGauge [name value]
    (<- (rewritten place-name place (fn [snapshot] (gauged snapshot name value))))
    (resume None))
  (ReadMeter []
    (when (.acquire place.lock :blocking False)
      (try
        (val pauses (tuple (gfor _ (range (len place.pauses)) (.popleft place.pauses))))
        (<- folded MeterSnapshot (with-pauses (get SNAPSHOTS place-name) place.settings pauses))
        (setv (get SNAPSHOTS place-name) folded)
        (finally
          (.release place.lock))))
    (resume (get SNAPSHOTS place-name))))
