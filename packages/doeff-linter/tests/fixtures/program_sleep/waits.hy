;;; Fixture for DOEFF210 (Hy programs do not sleep on the wall clock) — fires on time.sleep / asyncio.sleep inside a program (defk, defp),
;;; and does not fire on handler implementations, time effects, 0-second yields, or definitions that are not programs.
;;; A trailing "; fires" at the end of a line marks a line that should fire: tests/program_sleep.rs compares the set of marked lines with the set of linter hit lines
;;; (removing the rule, removing an exclusion, or adding an exclusion each makes the sets differ and goes red). This file is never run (read-only fixture).
(require doeff-hy.macros [defk defp deff defhandler <- val var])
(import asyncio)
(import time)
(import time :as clock)
(import asyncio [sleep :as rest])
(import doeff [Await])
(import doeff_time [Delay DelayEffect])


;; ---------------------------------------------------------------------------
;; Fires: a program waits on the wall clock, bypassing the handler

(defk retry-after-a-pause [attempt]
  {:pre [(: attempt int)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "A program that waits between retries on the wall clock."
  (time.sleep 1.0)  ; fires
  None)


(defk poll-until-ready [probe]
  {:pre [(callable probe)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "Even if wrapped in Await, an asyncio sleep waits on the wall clock."
  (var ready False)
  (while (not ready)
    (<- (Await (asyncio.sleep 0.5)))  ; fires
    (:= ready (probe)))
  ready)


(defk waits-through-aliases []
  {:pre [] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "Names bound by an import alias and by an import that lists names."
  (clock.sleep 0.2)  ; fires
  (<- (Await (rest 0.2)))  ; fires
  None)


(defp settle-then-go
  {:post [(: % None)] :tags {:context "sample" :role "program"}}
  (time.sleep 0.3)  ; fires
  None)


(defk sleeps-inside-a-nested-callable [run-now]
  {:pre [(callable run-now)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "An fn inside a program body is, literally, inside the program."
  (run-now (fn [] (time.sleep 0.1)))  ; fires
  None)


(defk program-without-tags [seconds]
  {:pre [(: seconds float)] :post [(: % None)]}
  "A definition that does not declare a role is a program (the general I/O role belongs only to definitions that declare it)."
  (time.sleep seconds)  ; fires
  None)


;; ---------------------------------------------------------------------------
;; Does not fire: a program that waits through a time effect

(defk waits-by-the-time-effect [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "What one second means is decided by the time handler the caller installs."
  (<- (Delay seconds))
  None)


(defk yields-once []
  {:pre [] :post [(: % None)] :tags {:context "sample" :role "program"}}
  "A call whose seconds is the literal 0 only yields once without waiting any time."
  (<- (Await (asyncio.sleep 0)))
  (time.sleep 0)
  None)


;; ---------------------------------------------------------------------------
;; Does not fire: handler implementations (waiting on the wall clock is the handler's responsibility)

(defhandler wall-clock-time
  (DelayEffect [seconds]
    (time.sleep seconds)
    (resume None)))


(defk raw-time-handler [effect k]
  {:pre [] :post [(: % "handler's answer")] :tags {:context "sample" :role "protocol"}}
  "A definition that takes (effect k) is a handler implementation."
  (time.sleep 0.1)
  (<- answer (k None))
  answer)


(defk settle-the-terminal [pane]
  {:pre [(: pane str)] :post [(: % None)] :tags {:context "sample" :role "foundation"}}
  "A helper called only from handler clauses — waits the real time a real terminal needs to finish receiving a paste. Declares the general I/O role in its own dict."
  (time.sleep 1.0)
  None)


;; ---------------------------------------------------------------------------
;; Does not fire: definitions that are not programs, and forms that are not read

(deff blocking-callback [seconds]  ; defk にできない: threading.Thread calls it as a callback on another thread
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "entry"}}
  "A plain callable cannot emit effects — it is not counted as a program even without declaring the general I/O role (whether this deff is justified is for DOEFF111 and 203)."
  (time.sleep seconds)
  None)


(defk mentions-a-sleep-without-calling-it []
  {:pre [] :post [(: % str)] :tags {:context "sample" :role "judgment"}}
  ;; (time.sleep 9) — inside a comment
  #_(time.sleep 9)
  (val quoted '(time.sleep 9))
  "(time.sleep 9)")
