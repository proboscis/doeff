;;; Memory handler for the meter, memory-meter-handler (ADR-DOE-CORE-EFFECTS-004): answers CountMetric, ObserveSeconds,
;;; ObserveSecondsBatch, SetGauge and ReadMeter with state within one run (a session value).
;;;
;;; Why: in mocks and tests we want to check the meter under the same contract as the real one (process_meter.hy) without touching clocks, gc or threads. The computation is the same
;;; pure functions as the real one (metered and gauged in meter_effects.hy); only the store differs (one per process -> within this run). GC pauses are not observed (the setting
;;; gc-pause-name is not read). It is not shared with other runs. An outer state handler is required.
;;;
;;; Write buffering: count and seconds-observation writes are answered by just appending to the store's buffer (pending of MeterHold, in the order fired), and
;;; at a read (ReadMeter), or when the buffer reaches METERED-WRITES entries, they are folded into the snapshot all at once with metered. A read always folds before answering, so the snapshot the reader
;;; sees is the same as one built up one write at a time (down to rounding -- see the note on metered). We do not copy the whole snapshot mapping on every write (in a record service
;;; that piles up seconds observations and counts on every request, copying the snapshot took about 10% of the scheduler thread's CPU). Gauge replacement is written to the snapshot without buffering (it does not
;;; overlap the mappings that counts and seconds observations touch).
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "meter" :role "foundation"})
(import dataclasses [dataclass])  ; named by the defrecord expansion
(import doeff_core_effects._meter_effects [CountMetric EMPTY-METER MeterSettings MeterSnapshot ObserveSeconds ObserveSecondsBatch ReadMeter
                                         SetGauge gauged metered])

;; The number of writes at which the buffer is folded into the snapshot (keeps the buffer length and its copying bounded even if no read comes).
(val METERED-WRITES 256)


(defrecord MeterHold
  "Value of the memory handler's store (the write buffering in the header comment): snapshot = the already folded snapshot, pending = the sequence of count and seconds-observation writes not yet folded
   (CountMetric, ObserveSeconds, ObserveSecondsBatch in the order fired)."
  {:tags {:context "meter" :role "foundation"}}
  (#^ MeterSnapshot snapshot)
  (#^ tuple pending))


;; A store with nothing accumulated.
(val EMPTY-HOLD (MeterHold :snapshot EMPTY-METER :pending #()))


(defk held-write [hold write]
  {:pre [(: hold MeterHold) (: write (| CountMetric ObserveSeconds ObserveSecondsBatch))]
   :post [(: % MeterHold)] :tags {:context "meter" :role "judgment"}}
  "To append the count or seconds-observation write to the end of the store's buffer (folding is done in the handler clause -- the write buffering in the header comment. This emits no effect and does not
   run a Program for each write)."
  (MeterHold :snapshot hold.snapshot :pending (+ hold.pending #(write))))


(defk folded [hold settings]
  {:pre [(: hold MeterHold) (: settings MeterSettings)] :post [(: % MeterHold)] :tags {:context "meter" :role "judgment"}}
  "To fold the store's buffer into the snapshot and leave a store with an empty buffer (before a read -- the reader sees the same snapshot as one built up one write at a time)."
  (if hold.pending
      (MeterHold :snapshot (! (metered hold.snapshot settings hold.pending)) :pending #())
      hold))


(defhandler memory-meter-handler [#^ MeterSettings settings]
  "Answers CountMetric, ObserveSeconds, ObserveSecondsBatch, SetGauge and ReadMeter with the snapshot within this run (see the header comment)."
  ;; Why it stays an argument: settings is a table of the same magnitude as the real one -- a value decided per place of installation, which Ask cannot distinguish.
  ;; Counts and seconds observations are appended to the buffer, and only the write that brings the buffer to METERED-WRITES entries folds it into the snapshot (the write buffering in the header comment).
  (session var hold EMPTY-HOLD)
  (CountMetric [name amount]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (ObserveSeconds [name seconds]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (ObserveSecondsBatch [observations]
    (val written (! (held-write hold effect)))
    (:= hold (if (< (len written.pending) METERED-WRITES) written (! (folded written settings))))
    (resume None))
  (SetGauge [name value]
    (:= hold (MeterHold :snapshot (! (gauged hold.snapshot name value)) :pending hold.pending))
    (resume None))
  (ReadMeter []
    (val read (! (folded hold settings)))
    (:= hold read)
    (resume read.snapshot)))
