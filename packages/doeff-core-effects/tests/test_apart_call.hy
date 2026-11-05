;;; Check for run-apart (apart_call.hy): the program's computation runs outside the scheduler thread of the task that fired it, and the effects the program issues are
;;; answered by the handler at the place that fired it.
;;;   - Other tasks of the run that fired it keep running during the computation (the clock tick advances at least MIN-TICKS times during COMPUTE-SECONDS). The computation runs in another thread, and the effect
;;;     answers come from the handler of the firing thread. Counterexample: firing the same program without run-apart, the tick does not advance during the computation (it occupies the thread).
;;;   - Failures pass in both directions: an effect refused by the responder is thrown to the program as an exception (the program can catch it), and an exception of the program is the exception of run-apart.
;;;   - The program of Try stays in another thread (it is not sent back to where it was fired).
;;;   - Cancelling the task that fired it closes the channel, and ApartClosed is thrown to the program that was waiting for an answer (the program's cleanup runs).
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.handle [defhandler])
(import threading)
(import time)
(import dataclasses [dataclass])
(import doeff [with_handlers EffectBase Program])
(import doeff_core_effects [Spawn Wait Cancel TaskCancelledError])
(import doeff_core_effects [Try])
(import doeff_core_effects [await-handler])
(import doeff_time [Delay async-time-handler])
(import doeff_core_effects [ApartClosed run-apart])

;; The seconds the program computes without issuing an effect, the seconds of the tick of the other tasks of the run that fired it, the lower bound of ticks that must advance during the computation, and the upper bound in seconds for waiting for the program's cleanup after cancelling
;; (seconds).
(val COMPUTE-SECONDS 0.5)
(val TICK-SECONDS 0.01)
(val MIN-TICKS 10)
(val CLOSE-SECONDS 5.0)


(defclass [(dataclass :frozen True)] AnsweringThread [EffectBase]
  "An effect that asks for the number of the thread that answered (to read whether the handler at the firing place answered).")


(defclass [(dataclass :frozen True)] Refused [EffectBase]
  "An effect that the responder at the firing place refuses (to read whether the refusal is passed to the program).")


(defhandler caller-answers
  ;; A responder that exists only at the firing place -- to read that the effects of a program in another thread are answered here.
  (AnsweringThread [] (resume (threading.get-ident)))
  (Refused [] (raise (ValueError "the responder at the firing place refused"))))


(defk next-count [n]
  {:pre [(: n int)] :post [(: % int)]}
  "One step of computation (a Program that issues no effect)."
  (+ n 1))


(defk computed-on [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)]}
  "To compute for seconds seconds without issuing an effect, then issue an effect once at the end, and return #(number of steps, thread that computed, thread that answered)."
  (val until (+ (time.monotonic) seconds))
  (var steps 0)
  (while (< (time.monotonic) until)
    (<- counted int (next-count steps))
    (:= steps counted))
  (<- answered int (AnsweringThread))
  #(steps (threading.get-ident) answered))


(defk ticking [stop]
  {:pre [(: stop threading.Event)] :post [(: % int)]}
  "To count clock ticks until stop, as another task of the run that fired it."
  (var ticks 0)
  (while (not (.is-set stop))
    (<- (Delay TICK-SECONDS))
    (:= ticks (+ ticks 1)))
  ticks)


(defk with-ticker [program]
  {:pre [(: program Program)] :post [(: % tuple)]}
  "To start a task that counts ticks on the side, fire the program, and return #(the program's answer, number of ticks)."
  (val stop (threading.Event))
  (<- ticker (Spawn (ticking stop)))
  (<- answer program)
  (.set stop)
  (<- ticks int (Wait ticker))
  #(answer ticks))


(deftest test-the-program-computes-apart-and-its-effects-are-answered-here
  (<- seen tuple (with_handlers [await-handler (async-time-handler) caller-answers]
                                (with-ticker (run-apart (computed-on COMPUTE-SECONDS)))))
  (val answer (get seen 0))
  (val ticks (get seen 1))
  (val here (threading.get-ident))
  (assert (> (get answer 0) 0) answer)
  (assert (!= (get answer 1) here) "the computation ran in the firing thread")
  (assert (= (get answer 2) here) "the handler at the place that issued the effect did not answer")
  (assert (>= ticks MIN-TICKS) (.format "the tick of the run that fired it advanced only {} times during the computation" ticks)))


(deftest test-computing-in-place-holds-the-thread
  ;; Counterexample: the same program without run-apart stops the other tasks of the run that fired it during the computation.
  (<- seen tuple (with_handlers [await-handler (async-time-handler) caller-answers]
                                (with-ticker (computed-on COMPUTE-SECONDS))))
  (assert (<= (get seen 1) 1) (.format "the tick advanced {} times during the in-place computation" (get seen 1))))


(defk refusal-caught []
  {:pre [] :post [(: % str)]}
  "To fire an effect that the responder refuses, catch the refusal exception, and return its text."
  (try
    (<- (Refused))
    "not refused"
    (except [error ValueError]
      (str error))))


(defk broken []
  {:pre [] :post [(: % str)]}
  "A program that fails."
  (raise (KeyError "a program in another thread failed")))


(deftest test-failures-cross-in-both-directions
  (<- caught str (with_handlers [caller-answers] (run-apart (refusal-caught))))
  (assert (= caught "the responder at the firing place refused") caught)
  (var raised None)
  (try
    (<- (run-apart (broken)))
    (except [error KeyError]
      (:= raised error)))
  (assert (is-not raised None) "the program's exception does not rise from run-apart"))


(defk computed-thread []
  {:pre [] :post [(: % int)]}
  "To return the number of the thread that computed (run as the program of Try)."
  (threading.get-ident))


(defk thread-inside-try []
  {:pre [] :post [(: % int)]}
  "To return the number of the thread that computed inside the program of Try."
  (<- attempted (Try (computed-thread)))
  attempted.value)


(deftest test-try-runs-apart
  ;; Try answers inside the VM of another thread (if it were sent back to the firing place, the program of Try would run in the firing thread).
  (<- inside int (run-apart (thread-inside-try)))
  (assert (!= inside (threading.get-ident)) "the program of Try ran in the firing thread"))


(defk waits-long [closed]
  {:pre [(: closed threading.Event)] :post [(: % str)]}
  "To fire a long wait and set closed when the channel closes (to read whether the cleanup after cancelling reaches the program)."
  (try
    (<- (Delay 30.0))
    "woke up"
    (except [error ApartClosed]
      (.set closed)
      (raise))))


(defk cancelled-while-waiting [closed]
  {:pre [(: closed threading.Event)] :post [(: % bool)]}
  "To cancel the task that fired run-apart while the program waits for an effect answer, and return whether it ended by the cancellation."
  (<- task (Spawn (run-apart (waits-long closed))))
  (<- (Delay 0.2))
  (<- (Cancel task))
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError]
      True)))


(deftest test-cancelling-the-caller-closes-the-lane
  (val closed (threading.Event))
  (<- cancelled bool (with_handlers [await-handler (async-time-handler)] (cancelled-while-waiting closed)))
  (assert cancelled "the task that fired it did not end by the cancellation")
  (assert (.wait closed CLOSE-SECONDS) "the channel closed but ApartClosed did not reach the program"))


(deftest test-the-apart-thread-ends-without-raising
  ;; Whether an answer or an exception, the other thread does not end with an exception such as a postcondition violation.
  (val raised [])
  (val previous threading.excepthook)
  (setv threading.excepthook (fn [args] (.append raised args.exc-value)))
  (try
    (<- (run-apart (next-count 1)))
    (try
      (<- (run-apart (broken)))
      (except [error KeyError]
        None))
    (for [thread (threading.enumerate)]
      (when (= thread.name "doeff-apart")
        (.join thread CLOSE-SECONDS)))
    (finally
      (setv threading.excepthook previous)))
  (assert (= raised []) raised))
