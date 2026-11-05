;;; Interpreters (composition root) for the doeff-time contract tests -- run the same contract Program, swapping only the clock handler.
;;;
;;;   async  real clock: async-time-handler (asyncio sleep, wall clock, time.monotonic)
;;;   sync   real clock: sync-time-handler (time.sleep, threading.Timer)
;;;   sim    virtual clock: sim-time-handler (starts at SIM-START and advances exactly by the amount waited)
;;;
;;; The contract Program reads its own clock traits (ClockTraits -- how loosely time spans are judged) via the ClockUnderTest effect.
;;; OuterProbe is an effect answered only by a handler placed outside the clock handler (checks that the outer handler is visible from a ScheduleAt Program, and the order of execution -- the answer is the arrival sequence number).
;;;
;;; Used by the doeff_interpreter in conftest.py (deftest :interpreters name -> under-clock).
(require doeff-hy.macros [defk defhandler <- val])
(import dataclasses [dataclass])
(import functools [partial])
(import datetime [datetime timezone])
(import doeff [EffectBase Program with_handlers])
(import doeff_time [async-time-handler sync-time-handler sim-time-handler])

(val ASYNC "async")
(val SYNC "sync")
(val SIM "sim")
(val SIM-START (datetime 2024 1 1 :tzinfo timezone.utc))
;; Interpreter name -> function that makes the clock handler (a new handler per run -- the virtual clock is not shared between tests).
(val CLOCKS {ASYNC async-time-handler
             SYNC sync-time-handler
             SIM (partial sim-time-handler :start-time SIM-START)})


(defclass [(dataclass :frozen True)] ClockTraits []
  "Clock traits: exact = advances exactly by the amount waited (virtual clock). A real clock passes even if it wakes up early by up to early seconds or late by up to late seconds
   (early = resolution of the asyncio wake-up clock plus the gap between wall clock and monotonic; late = scheduler delay on a busy machine)."
  (#^ str name)
  (#^ bool exact)
  (#^ float early)
  (#^ float late))


(val REAL-EARLY 0.005)
(val REAL-LATE 1.0)
(val TRAITS {ASYNC (ClockTraits ASYNC False REAL-EARLY REAL-LATE)
             SYNC (ClockTraits SYNC False REAL-EARLY REAL-LATE)
             SIM (ClockTraits SIM True 0.0 0.0)})


(defclass [(dataclass :frozen True)] ClockUnderTest [EffectBase])

(defclass [(dataclass :frozen True)] OuterProbe [EffectBase])


(defhandler clock-under-test [traits]
  ;; Reason to keep it as an argument: the traits are values decided by the assembly side per interpreter name, and the contract Program side has no name to distinguish them via Ask.
  (ClockUnderTest []
    (resume traits)))


(defhandler outer-probe
  (session var arrivals 0)
  (OuterProbe []
    (:= arrivals (+ arrivals 1))
    (resume arrivals)))


(defk under-clock [name program]
  {:pre [(: name str) (in name CLOCKS) (: program Program)]
   :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "doeff-time-test" :role "foundation"}}
  "Run program under the clock handler for name. From the outside in: answerer of the clock traits, outer handler (OuterProbe), clock handler."
  (<- answer (with_handlers [(clock-under-test (get TRAITS name)) outer-probe ((get CLOCKS name))] program))
  answer)
