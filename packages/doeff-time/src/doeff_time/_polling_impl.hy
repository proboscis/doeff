(require doeff-hy.macros [<- do!])

(import math)
(import threading)
(import time)
(import datetime [datetime])

(import doeff [DoExpr EffectBase Pure])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait])
(import doeff_time._internals.validation [ensure_aware_datetime])
(import doeff_time.effects [Delay GetTime])


(defn _as-program [value]
  (if (or (isinstance value DoExpr) (isinstance value EffectBase))
      value
      (Pure value)))


(defn _coerce-interval-seconds [value]
  (setv seconds (float value))
  (when (not (math.isfinite seconds))
    (raise (ValueError (+ "interval_seconds must be finite, got " (repr value)))))
  (when (<= seconds 0.0)
    (raise (ValueError "interval_seconds must be > 0.0")))
  seconds)


(defn _sleep-seconds-datetime [now deadline interval-seconds]
  (setv remaining (.total-seconds (- deadline now)))
  (max 0.0 (min interval-seconds remaining)))


(defn _sleep-monotonic [seconds]
  (do!
    (<- delay-ep (CreateExternalPromise))
    (.start (threading.Timer seconds delay-ep.complete [None]))
    (<- _ (Wait delay-ep.future))
    None))


(defn _numeric-deadline? [deadline]
  (and (isinstance deadline #(int float))
       (math.isfinite (float deadline))))


(defn _datetime-deadline? [deadline]
  (isinstance deadline datetime))


(defn poll-until [poll done * deadline interval-seconds]
  "Poll until effectful done(value) returns True or deadline is reached.

  `poll` is a zero-argument callable. `done` is a one-argument callable receiving
  the polled value. Each callable may return either a plain value, an EffectBase,
  or a doeff Program. The final returned value is the first completed value, or
  the last polled value observed at/after the deadline.
  "
  (when (not (or (_datetime-deadline? deadline) (_numeric-deadline? deadline)))
    (raise (TypeError (+ "deadline must be an aware datetime or finite monotonic seconds, got "
                         (getattr (type deadline) "__name__")))))
  (setv checked-deadline
    (if (_datetime-deadline? deadline)
        (ensure_aware_datetime deadline :name "deadline")
        (float deadline)))
  (setv checked-interval (_coerce-interval-seconds interval-seconds))
  (do!
    (setv result None)
    (while True
      (<- value (_as-program (poll)))
      (<- is-done (_as-program (done value)))
      (when (not (isinstance is-done bool))
        (raise (TypeError (+ "done(value) must return bool or Program[bool], got "
                             (getattr (type is-done) "__name__")))))
      (when is-done
        (setv result value)
        (break))
      (if (_datetime-deadline? checked-deadline)
          (do
            (<- now (GetTime))
            (when (>= now checked-deadline)
              (setv result value)
              (break))
            (setv delay-seconds
              (_sleep-seconds-datetime now checked-deadline checked-interval)))
          (do
            (setv now (time.monotonic))
            (when (>= now checked-deadline)
              (setv result value)
              (break))
            (setv delay-seconds
              (max 0.0 (min checked-interval (- checked-deadline now))))))
      (when (> delay-seconds 0.0)
        (if (_datetime-deadline? checked-deadline)
            (do
              (<- _ (Delay delay-seconds)))
            (do
              (<- _ (_sleep-monotonic delay-seconds))))))
    result))
