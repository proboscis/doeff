;;; event-loop — macro for the event-waiting worker's loop (design = agora-controllers docs/design/event-waits/README.md section 4).
;;;
;;;   (require doeff-events.macros [event-loop])
;;;   (event-loop [board (! (ReadBoard))]
;;;     (:stop reason)          (stop board)                      ; stop clause (exactly one required)
;;;     (BoardMoved keys)       (! (read-board-at board keys))    ; the board re-read at the signalled keys = next state
;;;     (TimerFired tag)        (if (= tag END) (stop board) board))
;;;
;;; - The tuple of types in each clause head is passed as-is to WaitForEvent, so the awaited types and the handled types cannot drift apart. The stop signal (the stop watcher emits
;;;   StopArrived on the same bus) is awaited with the same WaitForEvent, and whichever arrives first runs its clause (no task is spawned per wake-up to race them).
;;; - A clause body lives inside do! (<- and ! are available). The body's value is the next state. (stop value) may be written only at the last-value position of a body; it leaves the loop and
;;;   becomes the value of event-loop. The value of the stop clause also becomes the value of event-loop as is. The form without state (clauses only) uses the same macro.
;;; - Forms rejected at expansion: no stop clause or two of them / no awaited-type clause / a clause whose type cannot be derived (_, a bare name, | , a value expression) / two clauses of the same type /
;;;   a clause without a body / a stop outside the last-value position.
;;; - The waiting parts (stop wait, racing, per-event slog) are in doeff_events/_event_loop.py. The expansion refers to doeff-hy's <- and do!
;;;   directly via hy.R (it does not rely on the caller's require). Clause bodies and the initial value are wrapped in do!, so even when the ! in a defk body is rewritten in place to
;;;   (yield ...), the position that runs once per event does not change.
;;; - This module is placed at the public boundary without a leading _ in its name (ADR-DOE-API-001 R31 — Hy's require reads macros by module path,
;;;   so, like R14 and R15, it is a boundary outside R5).

(import hy)


(setv _STOP-TAIL-HEADS #{"do" "when" "unless" "let"})


(defn _el-fail [message]
  "To reject expansion-time mistakes in a form the author can fix (with one line showing the correct form)."
  (raise (SyntaxError (+ "event-loop: " message
                         "\n  form: (event-loop [state init] (:stop reason) body (type binding ...) body ...)"
                         " — one stop clause, one or more awaited-type clauses, and one body per clause"))))


(defn _el-symbol? [x]
  "To tell whether a clause head or binding is a name (symbol)."
  (isinstance x hy.models.Symbol))


(defn _el-head [form]
  "Spelling of an expression's head symbol (None when it is not an expression or the head is not a symbol)."
  (if (and (isinstance form hy.models.Expression) (> (len form) 0) (_el-symbol? (get form 0)))
      (str (get form 0))
      None))


(defn _el-split-state [args]
  "#(state-name type init clauses) — if the first element is [name init] or [name type init] it is the form with state (when a type is given, state and the exited value are checked against it and
   it is visible to the type checker); otherwise it is the form without state."
  (if (and args (isinstance (get args 0) hy.models.List))
      (let [binding (get args 0)]
        (when (not (and (in (len binding) #{2 3}) (_el-symbol? (get binding 0))))
          (_el-fail (+ "a state binding must be written as [name init] or [name type init]: " (hy.repr binding))))
        (if (= (len binding) 3)
            #((get binding 0) (get binding 1) (get binding 2) (cut args 1 None))
            #((get binding 0) None (get binding 1) (cut args 1 None))))
      #(None None None args)))


(defn _el-capture? [x]
  "To tell whether a name may be placed in a type clause binding (an attribute spelling .x would become a value comparison, so it is rejected)."
  (and (_el-symbol? x) (not (.startswith (str x) "."))))


(defn _el-class-args-ok? [args]
  "Sequence of bindings in a type clause: only positional names (symbols) and :field name pairs — a form that narrows by value cannot be written (an event of the type always matches some clause)."
  (cond
    (not args) True
    (isinstance (get args 0) hy.models.Keyword)
      (and (> (len args) 1) (_el-capture? (get args 1)) (_el-class-args-ok? (cut args 2 None)))
    (_el-capture? (get args 0)) (_el-class-args-ok? (cut args 1 None))
    True False))


(defn _el-pattern-kind [pattern]
  "Kind of clause head: \"stop\" (the stop clause) or \"event\" (a type clause). Any other form is rejected at expansion."
  (setv head (if (and (isinstance pattern hy.models.Expression) (> (len pattern) 0)) (get pattern 0) None))
  (cond
    (and (isinstance head hy.models.Keyword) (= head.name "stop"))
      (if (or (= (len pattern) 1) (and (= (len pattern) 2) (_el-symbol? (get pattern 1))))
          "stop"
          (_el-fail (+ "a stop clause must be written as (:stop) or (:stop reason-name): " (hy.repr pattern))))
    (and (_el-symbol? head) (not (in (str head) #{"_" "|"})))
      (if (_el-class-args-ok? (cut pattern 1 None))
          "event"
          (_el-fail (+ "a type clause binding may only be a name (positional) or :field name — do not narrow by value, branch in the body: " (hy.repr pattern))))
    True (_el-fail (+ "cannot derive the awaited type from the clause head (_, a bare name, | and value expressions cannot be written — write (type binding ...)): " (hy.repr pattern)))))


(defn _el-guard-end [items index]
  "To return the position after skipping the :as name and :if guard pair that follow a match pattern (to find the body position)."
  (if (and (< (+ index 1) (len items)) (isinstance (get items index) hy.models.Keyword)
           (in (. (get items index) name) #{"as" "if"}))
      (_el-guard-end items (+ index 2))
      index))


(defn _el-body-positions [items index]
  "To return the tuple of body positions of a match clause list (pattern [:as name] [:if guard] body ...)."
  (if (>= index (len items))
      #()
      (let [body (_el-guard-end items (+ index 1))]
        #(body #* (_el-body-positions items (+ body 1))))))


(defn _el-match-bodies [items ctor]
  "To rewrite only the bodies of match clauses with _el-tail (pattern and guard are left untouched)."
  (setv positions (_el-body-positions items 0))
  (lfor [index item] (enumerate items) (if (in index positions) (_el-tail item ctor) item)))


(defn _el-try-part [part ctor]
  "In a try, the last form of the except and else clauses is returned, and finally as is (the value of finally does not become the value of try)."
  (setv head (_el-head part))
  (if (and (in head #{"except" "else"}) (> (len part) 1))
      (hy.models.Expression [#* (cut part 0 -1) (_el-tail (get part -1) ctor)])
      part))


(defn _el-tail [form ctor]
  "Rewrite (stop value) at the last-value position of a body into (ctor value) (following the branches of do, when, unless, let, if, cond, match, try)."
  (setv head (_el-head form))
  (cond
    (= head "stop")
      (if (<= (len form) 2)
          (hy.models.Expression [ctor (if (= (len form) 2) (get form 1) (hy.models.Symbol "None"))])
          (_el-fail (+ "stop takes exactly one value: " (hy.repr form))))
    (and (in head _STOP-TAIL-HEADS) (> (len form) 1))
      (hy.models.Expression [#* (cut form 0 -1) (_el-tail (get form -1) ctor)])
    (= head "if")
      (hy.models.Expression [#* (cut form 0 2) #* (lfor branch (cut form 2 None) (_el-tail branch ctor))])
    (= head "cond")
      (hy.models.Expression [(get form 0) #* (lfor [index part] (enumerate (cut form 1 None))
                                               (if (% index 2) (_el-tail part ctor) part))])
    (and (= head "match") (> (len form) 2))
      (hy.models.Expression [#* (cut form 0 2) #* (_el-match-bodies (list (cut form 2 None)) ctor)])
    (= head "try")
      (let [parts (list (cut form 1 None))
            handlers (lfor part parts :if (in (_el-head part) #{"except" "else" "finally"}) part)
            body (lfor part parts :if (not (in (_el-head part) #{"except" "else" "finally"})) part)]
        (hy.models.Expression [(get form 0)
                               #* (if body [#* (cut body 0 -1) (_el-tail (get body -1) ctor)] [])
                               #* (lfor part handlers (_el-try-part part ctor))]))
    True form))


(defn _el-found [form heads]
  "To return the head spelling of an expression whose head is in heads, if any is in form (quotes and nested event-loop are not searched). None if none."
  (setv head (_el-head form))
  (cond
    (in head heads) head
    (in head #{"quote" "quasiquote" "event-loop"}) None
    (isinstance form #(hy.models.Expression hy.models.List hy.models.Tuple hy.models.Set hy.models.Dict))
      (next (gfor part form :setv found (_el-found part heads) :if (is-not found None) found) None)
    True None))


(defn _el-body [body ctor]
  "To turn a clause body into a sequence of do! statements: rewrite the stop at the last-value position, and reject if a stop remains at any other position.
   The body of (do ...) is laid out as do! statements as is (<- may be written at statement position)."
  (setv rewritten (_el-tail body ctor))
  (when (_el-found rewritten #{"stop"})
    (_el-fail (+ "stop may be written only at the last-value position of a clause body (it does not exit midway): " (hy.repr body))))
  (if (and (= (_el-head rewritten) "do") (> (len rewritten) 1))
      (list (cut rewritten 1 None))
      [rewritten]))


(defmacro event-loop [#* args]
  "Loop macro for the event-waiting worker — (event-loop [state type init] (:stop reason) body (type binding ...) body ...).

   Awaits the tuple of clause-head types and the stop signal (StopArrived) with one WaitForEvent, and runs the clause of whichever arrives first.
   The body is inside do! (<- and ! are available). The body's value is the next state. Writing (stop value) at the last-value position of a body (a branch of do, if, cond, match, try)
   leaves the loop and returns the value. The value of the stop clause is also the value of event-loop as is. The initial value is also inside do! (write reads as (! (Read...))).
   Writing the state type ([name type init]) checks the initial value, each clause's value and the exited value against it and makes it visible to the type checker (the untyped form [name init] is also allowed).
   The form without state lists only clauses with no binding (body values are discarded; the exit is a stop or the stop clause). Do not rewrite an outer var with := in a body.
   - Type clauses match in the order written: if a parent-type clause is written before a child-type clause, the parent clause also receives events of the child type.
   - When exiting on the stop signal, events left in the queue are not run (a signal only says \"where it changed\", so the next worker to wake re-reads the records and catches up).
   - Emits slog(\"event-loop\" :event type-name) per event — an outer slog handler is required.
   - The outer bus handler delivers StopArrived to the worker (subscribed_event_handler always subscribes to it).
   Waiting parts = doeff_events._event_loop (one stop watcher for the worker's lifetime, StopRequested once before waiting)."
  (import doeff_hy._match_fields [mangle_match_fields])
  (setv #(state-name state-type state-init clauses) (_el-split-state args))
  (when (% (len clauses) 2)
    (_el-fail "clauses must be listed as (head) body pairs — there is a clause without a body"))
  (setv pairs (lfor index (range 0 (len clauses) 2) #((get clauses index) (get clauses (+ index 1)))))
  (setv kinds (lfor [pattern _body] pairs (_el-pattern-kind pattern)))
  (setv stops (lfor [pair kind] (zip pairs kinds) :if (= kind "stop") pair))
  (setv events (lfor [pair kind] (zip pairs kinds) :if (= kind "event") pair))
  (when (not stops)
    (_el-fail "no stop clause (:stop reason) body — the worker could not be stopped"))
  (when (> (len stops) 1)
    (_el-fail "exactly one stop clause is allowed"))
  (when (not events)
    (_el-fail "no awaited-type clause — write one or more (type binding ...) body"))
  (setv heads (lfor [pattern _body] events (str (get pattern 0))))
  (when (!= (len heads) (len (set heads)))
    (_el-fail (+ "two clauses of the same type: " (.join " " heads))))
  (setv bind 'hy.R.doeff_hy/macros.<-
        program 'hy.R.doeff_hy/macros.do!
        begin (hy.gensym "el-begin")
        next-of (hy.gensym "el-next")
        end (hy.gensym "el-end")
        arrived (hy.gensym "el-arrived")
        loop-stop (hy.gensym "el-loop-stop")
        value-of (hy.gensym "el-value")
        watch (hy.gensym "el-watch")
        got (hy.gensym "el-got")
        out (hy.gensym "el-out")
        result (hy.gensym "el-result"))
  (setv #(stop-pattern stop-body) (get stops 0))
  (setv stop-binding (if (= (len stop-pattern) 2) [`(setv ~(get stop-pattern 1) (. ~got reason))] []))
  ;; Form with state type written: the initial value is checked against the state type; each clause's value against the state type or (stop value); the exited value against the state type.
  ;; Clause values are bound without type annotation and checked at runtime with assert (statically, they are read as the state type where the value is written back to the state name).
  ;; If bound with an annotation, the annotated type flows back into inference of the Program's answer type, and the caller's pyright strict reports a red Unknown that the author cannot fix
  ;; (named by the commit hook).
  (setv state-check (if state-type [state-type] [])
        out-check (if state-type [`(assert (isinstance ~out #(~state-type ~loop-stop))
                                            (+ "event-loop: clause value type is not the state type: " (repr ~out)))] [])
        result-check (if state-type [`(assert (isinstance ~result ~state-type)
                                               (+ "event-loop: exited value type is not the state type: " (repr ~result)))] []))
  (setv arms (lfor [pattern body] events
               [pattern `(do (~bind ~out (~program ~@(_el-body body loop-stop)))
                             ~@out-check)]))
  (setv next-state (if state-name [`(setv ~state-name ~out)] []))
  (setv start-state (if state-name [`(~bind ~state-name ~@state-check (~program ~state-init))] []))
  `(do
     (import doeff_events._event_loop [begin_watch :as ~begin next_event :as ~next-of end_watch :as ~end
                                      LoopStop :as ~loop-stop stopped_value :as ~value-of]
             doeff_events._effects [StopArrived :as ~arrived])
     ~@start-state
     (~bind ~watch (~begin))
     (try
       (while True
         (~bind ~got (~next-of #(~@(lfor [pattern _body] events (get pattern 0))) ~watch))
         (when (isinstance ~got ~arrived)
           ~@stop-binding
           (~bind ~out (~program ~@(_el-body stop-body loop-stop)))
           ~@out-check
           (setv ~result (~value-of ~out))
           ~@result-check
           (break))
         ~(mangle_match_fields
            `(match ~got
               ~@(lfor arm arms part arm part)
               _ (raise (TypeError (+ "event-loop: event matching no clause " (repr ~got))))))
         (when (isinstance ~out ~loop-stop)
           (setv ~result (~value-of ~out))
           ~@result-check
           (break))
         ~@next-state)
       (finally
         (~bind (~end ~watch))))
     ~result))
