;;; run-apart: a mechanism that runs a program outside the scheduler thread of the task that fired it. The program's computation (VM steps and
;;; the plain functions inside the program) runs in a fresh VM on another thread; the effects the program emits are sent back to the firing task and answered by the handlers at the firing site.
;;;
;;; Why: the scheduler runs every task on a single thread, and tasks switch only at scheduler effects (wait, spawn, signal). A computation that continues without
;;; emitting effects (line-by-line parsing, recomputing sha256, building tuples) occupies the scheduler thread until it finishes, and makes every other task of the same run
;;; (requests waiting to be served) queue up behind it. On 2026-10-27 from 01:26 to 01:44 JST and from 02:08 to 02:16 JST, the maintenance worker of the record service (the
;;; forwarding function supplied by the user) occupied the scheduler thread twice for 4 minutes each, checking 190,000 rows read back from ClickHouse one row at a time, and the requests of both the main and the twin
;;; all waited for minutes (all DB connections were idle; the request waits were in the queue interval before the task started). Compute (compute_effects.hy) can run
;;; only pure programs (a VM with no handler attached), so a program that computes while emitting effects (SQL, meters, clock), like the forwarding function, could not be moved out.
;;;
;;; Shape:
;;;   - run-apart = a handler value applied to a program (ADR-DOE-API-001 R3, R16 - applied as (run-apart program), and can also be placed in a with-handlers list).
;;;     Inside the task of the program it is applied to, it starts a fresh VM (PyVM) on one daemon thread and runs the program under only a forwarding handler
;;;     (forwarding-handler). Every effect the program emits reaches this handler, which puts the effect on the lane (ApartLane) and waits for the answer (since it is
;;;     another thread, only that thread waits). The firing task hangs an externally completed promise on the lane and waits (Wait - the other tasks of the scheduler
;;;     keep running), fires the delivered effect itself (all handlers at the firing site answer - store, SQL, meters, clock, and scheduler effects too),
;;;     and returns the answer or the exception. The program's answer is the answer of run-apart, and the program's exception is the exception of run-apart.
;;;   - The effects of one program come one at a time (the next is not emitted while the VM waits for an answer), so the firing task answers them one at a time in the order they arrive.
;;;   - The computation runs on another thread, and on free-threaded Python it runs in parallel with the scheduler thread of the firing run. An effect round trip is slower by one thread handoff
;;;     (the externally completed promise and the Future) - use it for heavy computations with few effects (not for short per-request programs).
;;;   - Try is answered inside the VM on the other thread (a try-handler is placed in the VM - the Try's program stays on the other thread, and the inner handlers are re-acquired too).
;;;   - Effects answered at the firing site that carry a program as a value (the programs of Spawn, Gather, Race, and the program of SqlTransaction) have that program run by the
;;;     firing run. The computation inside returns to the scheduler thread of the firing run, and the program inside does not have, in the VM on the other thread, the handlers
;;;     that the program wrapped around it (the handlers inside run-apart) either - only the handlers at the firing site answer. Place heavy computations and effects that need the inner handlers
;;;     outside of it.
;;;   - Cancellation: when the firing task is cancelled, the lane is closed, and the effect waiting for an answer and the effects the program emits after the close throw ApartClosed into the
;;;     program (the program's finally runs, but effects inside the finally also become ApartClosed - so it cannot be used by a program that uses effects for cleanup).
;;;     A computation that emits no effects does not stop until it finishes (the thread is a daemon - it does not block process exit). The program's answer after the close
;;;     is discarded. The caller cancels the run-apart task when it no longer waits for the program (a daemon task is not cancelled at the end of the run).
;;;   - How to choose: a single pure computation whose entrance is visible -> Compute (compute_effects.hy - has a pool and meters). A blocking call of a driver -> offloaded
;;;     (offloaded_call.hy). A program that the user decides on and that computes for a long time while emitting effects -> run-apart (one thread per call, no limit and no meter -
;;;     the same shape as ThreadPerCall, so do not use it per request).
;;; Where the mechanism comes from (precedents): waiting on an externally completed promise follows the same practice as offloaded in offloaded_call.hy; starting a fresh VM is the same as
;;; run-detached in offloaded_call.hy. Unlike Compute (pure programs only), the program may emit effects.
(require doeff-hy.macros [defk deff <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; named by the expansion of defrecord
(import threading)
(import functools [partial])
(import concurrent.futures [Future])
(import doeff [Program Transfer TransferThrow with-handlers])
(import doeff.program [ProgramHandler handler :as raw-handler])
(import doeff [PyVM])
(import collections.abc [Callable])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait TaskCancelledError])
(import doeff_core_effects._handlers [try-handler])

(val MODULE-TAGS {:context "scheduler" :role "foundation"})


(defclass ApartClosed [Exception]
  "The exception thrown at an effect emitted by the program on the other thread after the firing task was cancelled and the lane closed (the cancellation item of the header note).")


(defrecord ApartAsked
  "One effect emitted by the program on the other thread: effect = the emitted effect, reply = the Future that returns the answer (the firing task finishes it with a value or an exception)."
  (#^ object effect)
  (#^ Future reply))


(defrecord ApartDone
  "The program on the other thread answered: value = the program's answer."
  (#^ object value))


(defrecord ApartFailed
  "The program on the other thread failed: error = the exception it raised."
  (#^ BaseException error))


(defclass ApartLane []
  "The lane between the firing task and the VM on the other thread (header note). It is a resource, so not a value type. armed = the externally completed promise, hung by the firing task, that receives the next message
   (None = not hung) / asked = the Future of an effect that was handed over but has no answer yet (finished with ApartClosed on close -
   if the handed-over promise crosses with a cancellation, the other thread would wait forever for the answer) / closed = the mark that the firing task has left. The lock is a Condition between threads."

  (deff __init__ [self]  ; defk にできない: initialization of a resource class
    {:pre [(: self ApartLane)] :post [(: % None)]}
    (setv self.condition (threading.Condition)
          self.armed None
          self.asked None
          self.closed False))

  (deff arm [self promise]  ; defk にできない: a synchronous entry where the firing task (on the scheduler thread) hangs the receiver of the message
    {:pre [(: self ApartLane) (: promise ExternalPromise)] :post [(: % None)]}
    "To hang the promise that receives the next message, and wake the other thread waiting for a message."
    (with [_ self.condition]
      (setv self.armed promise)
      (.notify-all self.condition)))

  (deff deliver [self message]  ; defk にできない: a synchronous entry where the VM on the other thread puts a message (outside the VM)
    {:pre [(: self ApartLane) (: message (| ApartAsked ApartDone ApartFailed))] :post [(: % bool)]}
    "To hand over one message through the promise hung by the firing task (waits until one is hung). If closed, it hands nothing over and returns False."
    (with [_ self.condition]
      (while (and (is self.armed None) (not self.closed))
        (.wait self.condition))
      (when self.closed
        (return False))
      (setv promise self.armed
            self.armed None)
      (when (isinstance message ApartAsked)
        (setv self.asked message.reply)))
    (.complete promise message)
    True)

  (deff close [self]  ; defk にできない: a synchronous entry for when the firing task leaves (cleanup after cancellation)
    {:pre [(: self ApartLane)] :post [(: % None)]}
    "To close the lane (wake the other thread waiting for a message, hand over no further messages, and finish the effect that has no answer yet with ApartClosed)."
    (with [_ self.condition]
      (setv self.closed True
            self.armed None
            asked self.asked
            self.asked None)
      (.notify-all self.condition))
    (when (and (is-not asked None) (not (.done asked)))
      (.set-exception asked (ApartClosed "the firing task has left - the lane is closed")))))


(deff forwarding-handler [lane]  ; defk にできない: builds a responder to put in the VM on the other thread (waits on a Future outside the VM)
  {:pre [(: lane ApartLane)] :post [(: % Callable)]}
  "To build a responder that, in the VM on the other thread, sends every effect the program emits to the lane and resumes with the answer (or exception) the firing task returned."
  (defn forwarded [effect k]
    ;; Hand one effect to the firing task, wait for the answer, and resume (only this thread waits).
    (setv reply (Future))
    (if (.deliver lane (ApartAsked :effect effect :reply reply))
        (try
          (setv value (.result reply))
          (except [error BaseException]
            (return (yield (TransferThrow k error))))
          (else
            (return (yield (Transfer k value)))))
        (return (yield (TransferThrow k (ApartClosed "the firing task has left - the lane is closed"))))))
  (raw-handler forwarded))


(deff apart-thread [lane program]  ; defk にできない: the body of the daemon thread (starts a fresh VM from outside the VM)
  {:pre [(: lane ApartLane) (: program Program)] :post [(: % None)]}
  "To run the program in a fresh VM on another thread and put the answer or exception on the lane (Try is answered inside this VM - header note)."
  (try
    (setv value (.run (PyVM) (with-handlers [(forwarding-handler lane) try-handler] program)))
    (except [error BaseException]
      (.deliver lane (ApartFailed :error error))
      None)
    (else
      (.deliver lane (ApartDone :value value))
      None)))


(defk answered-here [asked]
  {:pre [(: asked ApartAsked)] :post [(: % None)] :tags {:context "scheduler" :role "foundation"}}
  "To answer an effect emitted by the program on the other thread with the firing task's handlers, and return the answer or exception to the Future. Cancellation of the firing task
   is raised (a Future with no answer yet is finished with ApartClosed by the lane close of run-apart)."
  (try
    (<- value asked.effect)
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (.set-exception asked.reply error))
    (else
      (.set-result asked.reply value)))
  None)


(defk next-message [lane]
  {:pre [(: lane ApartLane)] :post [(: % (| ApartAsked ApartDone ApartFailed))] :tags {:context "scheduler" :role "foundation"}}
  "To hang an externally completed promise on the lane and wait for the next message from the other thread (only the firing task waits)."
  (<- promise (CreateExternalPromise))
  (.arm lane promise)
  (<- message (Wait promise.future))
  message)


(defk _apart [program]
  {:tp [T] :pre [(: program (get Program #(T object)))] :post [(: % T)] :tags {:context "scheduler" :role "foundation"}}
  "To run the program in a fresh VM on another thread, answer the effects it emits with this task's handlers, and return the program's answer (header note).
   The program's exception is raised as is. If this task is cancelled, the lane is closed (ApartClosed goes to the program - cancellation item of the header note).
   A scheduled is required outside (CreateExternalPromise and Wait)."
  (val lane (ApartLane))
  (.start (threading.Thread :target (fn [] (apart-thread lane program)) :name "doeff-apart" :daemon True))
  (try
    (while True
      (<- message (next-message lane))
      (match message
        (ApartDone :value value) (return value)
        (ApartFailed :error error) (raise error)
        (ApartAsked) (<- (answered-here message))))
    (finally
      (.close lane))))


(defclass _ApartHandler [partial]
  "The type of run-apart: to give a partial that passes the wrapped program to _apart the handler mark _doeff_is_handler_fn (without the mark, with-handlers would
   re-wrap it as a responder of effects - attached the same way as BodyWrapper in doeff_records)."
  (setv _doeff_is_handler_fn True))


;; A handler that runs a program outside the scheduler thread of the task that fired it (header note): (run-apart program) is a program that runs the program in a fresh VM on another thread,
;; answers the effects the program emits with the handlers at the site where it is applied, and returns the program's answer. The program's exception is raised as is. A
;; scheduled is required outside (CreateExternalPromise and Wait). It holds no configuration or per-call state, so it is a marked handler value rather than a factory
;; (ADR-DOE-API-001 R3, R16, ADR-DOE-API-002 R1 - same as maybe and result). The type is ProgramHandler, same as writer and try-handler (it keeps the answer's type).
(setv #^ ProgramHandler run-apart (_ApartHandler _apart))

;; A declaration that it answers nothing (__doeff_handles__ = ()), re-emits the program's effects at the site where it is applied, and emits the lane waits (CreateExternalPromise, Wait)
;; around that (the reading of the body wrapper by doeff-effect-analyzer - same as records-signal-handler in doeff_records).
(setv run-apart.__doeff_handles__ #()
      run-apart.__doeff_effects__ #(CreateExternalPromise Wait))
