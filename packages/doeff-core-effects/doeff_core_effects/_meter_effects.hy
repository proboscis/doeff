;;; Meter effects -- inside a long-running process, accumulate counts, second observations and current values, and read a snapshot (ADR-DOE-CORE-EFFECTS-004).
;;; A foundation vocabulary with no business words. The spelling of names (Prometheus names, etc.) is decided by the caller.
;;;
;;;   CountMetric     Add amount (default 1) to the counter name. amount = 0 means "put a 0" -- so a reader need not distinguish absent from 0. Answer = None.
;;;   ObserveSeconds  Accumulate seconds into the total of seconds and the count. If the settings have a bucket table, the cumulative counters <name>_<label> for each bucket hit and
;;;                   <name>_<the inf label> are advanced in the same single step. Answer = None.
;;;   ObserveSecondsBatch
;;;                   Accumulate a sequence observations of second observations (SecondsObservation -- a name and seconds) in one step -- the snapshot is the same as firing ObserveSeconds
;;;                   one at a time in the order of the sequence (the total of seconds, the count and the bucket counters are all added per name in sequence order). So that a caller that observes seconds many times under different names for one event
;;;                   (the seconds of the sections of each request of the record service -- 7 to 9 per request) fires the effect only once.
;;;                   Answer = None.
;;;   SetGauge        Replace the gauge name with value. Answer = None.
;;;   ReadMeter       Read the current snapshot (MeterSnapshot).
;;;
;;; Responders: process-meter-handler (process_meter.hy -- the real one. It shares one store per process by name, and other runs and other threads read and write the same meter.
;;; It can also accumulate GC pauses) and memory-meter-handler (memory_meter.hy -- only the state inside one run. It reads no clock, gc or thread).
;;; Both pass the same contract test (tests/test_meter_contract.hy).
;;;
;;; What it guarantees: one write is seen at once within one name (the total of seconds, the count and the bucket counters of that name).
;;; What it does not guarantee: writes across names (accumulating into two names, like a total and its breakdown) are not necessarily seen at once -- a read in between sees a snapshot where only one side has advanced.
;;;             Writes across the names of an ObserveSecondsBatch sequence are likewise not promised (the two current responders swap in one step, but readers must not rely on that).
;;;
;;; The shape of the snapshot (counters, gauges, the total of seconds and the count) is aligned to the same three as the report shape of doeff-cluster's ReportMetrics (so the snapshot can be handed to a report later).
;;; The pure functions below (metered, counted, observed-batch, observed, gauged) are the one place where the two responders do the same computation, and they do no I/O.
;;; The computation of counts and second observations is in one place, metered (it accumulates a sequence of writes -- CountMetric, ObserveSeconds, ObserveSecondsBatch in firing order -- in one step.
;;; counted, observed-batch and observed are sequences of one write). The memory responder collects writes and, at read time or when the pile reaches METERED-WRITES entries,
;;; folds them in one step with metered (it does not copy the whole mapping of the snapshot at every write).
(require doeff-hy.macros [defeffect defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import functools [reduce])
(import itertools [groupby])
(import operator [add itemgetter])
(import doeff_hy._frozen [FrozenMap])


(defrecord SecondsTotal
  "One series of second observations: total = the total of the observed seconds, count = the number of observations."
  {:tags {:context "meter" :role "type"}
   :check [(>= count 0)]}
  (#^ float total)
  (#^ int count))


(defrecord MeterSnapshot
  "A snapshot of the meter: counters = name -> accumulated amount, gauges = name -> current value, durations = name -> total of seconds and count."
  {:tags {:context "meter" :role "type"}}
  (#^ (get FrozenMap float) counters)
  (#^ (get FrozenMap float) gauges)
  (#^ (get FrozenMap SecondsTotal) durations))


;; A meter with nothing accumulated.
(val EMPTY-METER (MeterSnapshot :counters (FrozenMap) :gauges (FrozenMap) :durations (FrozenMap)))


(defrecord MeterBucket
  "One bucket of second observations: label = the end of the counter name (e.g. le_200ms), ceiling = the upper limit (inclusive) of seconds that fall into this bucket."
  {:tags {:context "meter" :role "type"}}
  (#^ str label)
  (#^ float ceiling))


(defrecord MeterSettings
  "Meter settings: buckets = the table of buckets for second observations (if empty, no bucket counters are accumulated), inf-label = the end of the name of the bucket that counts everything,
   gc-pause-name = the name of the second observation into which GC pauses are accumulated (if None, they are not accumulated -- only the real responder reads it. The memory responder does not watch GC)."
  {:tags {:context "meter" :role "type"}}
  (setv #^ (get tuple #(MeterBucket ...)) buckets #())
  (setv #^ str inf-label "le_inf")
  (setv #^ (| str None) gc-pause-name None))


(defrecord SecondsObservation
  "One second observation (one item of the ObserveSecondsBatch sequence): name = the name of the second observation to accumulate into, seconds = the observed seconds. The same fields as ObserveSeconds."
  {:tags {:context "meter" :role "type"}}
  (#^ str name)
  (#^ float seconds))


(defeffect CountMetric
  "Add amount to the counter name (head note)."
  {:fields [(: name str) (: amount float 1.0)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ObserveSeconds
  "Accumulate seconds into the total of seconds and the count, and advance the bucket counters in the same single step (head note)."
  {:fields [(: name str) (: seconds float)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ObserveSecondsBatch
  "Accumulate the sequence observations of second observations in one step into the same snapshot as firing ObserveSeconds one at a time in sequence order (head note). An empty sequence accumulates nothing."
  {:fields [(: observations (get tuple #(SecondsObservation ...)))]
   :pre [(: observations tuple) (all (gfor observation observations (isinstance observation SecondsObservation)))]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect SetGauge
  "Replace the gauge name with value (head note)."
  {:fields [(: name str) (: value float)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ReadMeter
  "Read the current snapshot (head note)."
  {:fields []
   :answer MeterSnapshot
   :tags {:context "meter" :role "foundation"}})


;; Look up the name of a pair of name and amount (the key for grouping a sequence of writes by name).
(val NAME-OF-PAIR (itemgetter 0))


(defk grouped-in-order [pairs]
  {:pre [(: pairs (get tuple #(tuple ...)))] :post [(: % dict)] :tags {:context "meter" :role "judgment"}}
  "To turn a sequence of pairs of name and value into name -> the sequence of values of that name (in sequence order). The order of names is the order of first appearance (so the order of adding to the mapping is the same as when accumulating one at a time).
   It groups after sorting stably by name, so even a long sequence does not turn into a comparison of the number of names times the length of the sequence."
  (val grouped (dfor #(name group) (groupby (sorted pairs :key NAME-OF-PAIR) :key NAME-OF-PAIR)
                     name (tuple (gfor #(_ value) group value))))
  (dfor name (dict.fromkeys (gfor #(name _) pairs name)) name (get grouped name)))


(defk metered [snapshot settings writes]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: writes (get tuple #((| CountMetric ObserveSeconds ObserveSecondsBatch) ...)))]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "To accumulate a sequence of count and second-observation writes writes (in firing order) in one step into a new snapshot equal to accumulating them one at a time (the original snapshot is not changed -- head note): per name,
   the amounts and seconds in write order are added one at a time from the original value in sequence order, so even the floating-point rounding is the same as accumulating one at a time (adding the seconds of the sequence first and then accumulating
   changes the rounding). The value (SecondsTotal) and the copy of the mapping are made only once per name and per mapping -- so the memory responder, which collects writes and folds them, can fold
   with a fixed effort per write however long the pile is."
  (val counters snapshot.counters)
  (val durations snapshot.durations)
  ;; Expand into a sequence in firing order the second observations of each write (pairs of name and seconds -- a count accumulates no seconds) and the amounts to add to counters (pairs of name and amount -- a count adds amount to that name, a second observation adds 1 each to the
  ;; bucket it hits in the bucket table and to the inf bucket, and adds nothing if the bucket table is empty).
  (val per-write (tuple (gfor write writes
                              (cond (isinstance write ObserveSecondsBatch) (tuple (gfor observation write.observations
                                                                                        #(observation.name observation.seconds)))
                                    (isinstance write ObserveSeconds) #(#(write.name write.seconds))
                                    True #()))))
  (val increments (! (grouped-in-order
                       (tuple (gfor #(write pairs) (zip writes per-write)
                                    pair (if (isinstance write CountMetric)
                                             #(#(write.name write.amount))
                                             (tuple (gfor #(name seconds) (if settings.buckets pairs #())
                                                          counter (+ (lfor bucket settings.buckets :if (<= seconds bucket.ceiling)
                                                                           (+ name "_" bucket.label))
                                                                     [(+ name "_" settings.inf-label)])
                                                          #(counter 1.0))))
                                    pair)))))
  (val observed (! (grouped-in-order (tuple (gfor pairs per-write pair pairs pair)))))
  (val bumped (dfor #(name amounts) (.items increments) name (reduce add amounts (float (.get counters name 0.0)))))
  (val rows (dfor #(name seconds) (.items observed)
                  name (let [row (.get durations name)]
                         (SecondsTotal :total (reduce add seconds (if (is row None) 0.0 row.total))
                                       :count (+ (if (is row None) 0 row.count) (len seconds))))))
  (MeterSnapshot :counters (if bumped (.updated counters bumped) counters)
                 :gauges snapshot.gauges
                 :durations (if rows (.updated durations rows) durations)))


(defk counted [snapshot name amount]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: amount float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "A new snapshot with amount added to the counter name (the original snapshot is not changed -- the computation is a sequence of one write in metered)."
  (! (metered snapshot (MeterSettings) #((CountMetric :name name :amount amount)))))


(defk gauged [snapshot name value]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: value float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "A new snapshot with the gauge name replaced by value."
  (MeterSnapshot :counters snapshot.counters :gauges (.updated snapshot.gauges {name value})
                 :durations snapshot.durations))


(defk observed-batch [snapshot settings observations]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: observations (get tuple #(SecondsObservation ...)))]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "To make a new snapshot with the sequence observations of second observations accumulated in sequence order (the original snapshot is not changed): for each observation, accumulate the seconds into the total of seconds and the count of that name,
   and advance the counters of the bucket it hits in the bucket table and of the inf bucket. The snapshot is the same as accumulating ObserveSeconds one at a time in sequence order -- the total of seconds and the counters are both
   added one at a time per name in sequence order, so even the floating-point rounding is the same (adding the seconds of the sequence first and then accumulating changes the rounding). The computation is a sequence of one
   write in metered."
  (! (metered snapshot settings #((ObserveSecondsBatch :observations observations)))))


(defk observed [snapshot settings name seconds]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: name str) (: seconds float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "To make a new snapshot with seconds accumulated into the total of seconds and the count, and the counters of the bucket it hits in the bucket table and of the inf bucket advanced (it changes one name at once --
   the computation is a sequence of one observation in observed-batch)."
  (! (observed-batch snapshot settings #((SecondsObservation :name name :seconds seconds)))))
