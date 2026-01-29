;; Prototype: Hy macros for CESK DSL
;;
;; This demonstrates how Hy macros could reduce boilerplate in CESK handlers.
;; Would live in doeff/cesk/macros.hy and be imported into Python.

(require hyrule [defmacro! with-gensyms])

;; =============================================================================
;; Macro: define-handler
;; Eliminates boilerplate in handler definitions
;; =============================================================================

(defmacro define-handler [effect-type params #* body]
  "Define an effect handler with automatic destructuring.
   
   Usage:
     (define-handler StatePutEffect [eff ts store]
       (continue-value None
         :store {#** store (. eff key) (. eff value)}))
   
   Expands to:
     def handle_state_put(effect, task_state, store):
         return ContinueValue(
             value=None,
             env=task_state.env,
             store={**store, effect.key: effect.value},
             k=task_state.kontinuation)
  "
  (setv handler-name (hy.models.Symbol 
                       (+ "handle_" (.lower (.replace (str effect-type) "Effect" "")))))
  (setv [eff-param ts-param store-param] params)
  
  `(defn ~handler-name [~eff-param ~ts-param ~store-param]
     (import doeff.cesk.frames [ContinueValue ContinueProgram ContinueError])
     ~@body))


;; =============================================================================
;; Macro: continue-value
;; Shorthand for returning ContinueValue with current env/k
;; =============================================================================

(defmacro continue-value [value #** kwargs]
  "Return ContinueValue, inheriting env and k from task_state by default.
   
   Usage:
     (continue-value None :store new-store)
   
   Expands to:
     ContinueValue(value=None, env=task_state.env, store=new_store, k=task_state.kontinuation)
  "
  (setv env-expr (.get kwargs "env" '(. task_state env)))
  (setv store-expr (.get kwargs "store" 'store))
  (setv k-expr (.get kwargs "k" '(. task_state kontinuation)))
  
  `(ContinueValue
     :value ~value
     :env ~env-expr
     :store ~store-expr
     :k ~k-expr))


;; =============================================================================
;; Macro: continue-program  
;; Shorthand for returning ContinueProgram with frame push
;; =============================================================================

(defmacro continue-program [program frame #** kwargs]
  "Return ContinueProgram, pushing a frame onto the continuation.
   
   Usage:
     (continue-program sub-prog (LocalFrame (. task_state env)))
   
   Expands to:
     ContinueProgram(
       program=sub_prog,
       env=task_state.env,
       store=store,
       k=[LocalFrame(task_state.env)] + task_state.kontinuation)
  "
  (setv env-expr (.get kwargs "env" '(. task_state env)))
  (setv store-expr (.get kwargs "store" 'store))
  
  `(ContinueProgram
     :program ~program
     :env ~env-expr
     :store ~store-expr
     :k (+ [~frame] (. task_state kontinuation))))


;; =============================================================================
;; Macro: match-control
;; Pattern match on CESK control state
;; =============================================================================

(defmacro match-control [state #* clauses]
  "Pattern match on CESK state's control and continuation.
   
   Usage:
     (match-control state
       [(Value v) (empty K)]        (Done v S)
       [(Value v) (LocalFrame e) K] (continue-value v :env e :k K)
       [(Error ex) (SafeFrame e) K] (continue-value (Err ex) :env e :k K))
  "
  (with-gensyms [C E S K]
    `(do
       (setv ~C (. ~state C))
       (setv ~E (. ~state E))
       (setv ~S (. ~state S))
       (setv ~K (. ~state K))
       (cond
         ~@(lfor [pattern result] (partition clauses 2)
             (expand-match-clause C E S K pattern result))))))


;; =============================================================================
;; Example handlers written with these macros
;; =============================================================================

;; Current Python (handlers/core.py lines 127-138):
;;
;; def handle_state_put(effect: StatePutEffect, task_state: TaskState, store: Store) -> FrameResult:
;;     new_store = {**store, effect.key: effect.value}
;;     return ContinueValue(
;;         value=None,
;;         env=task_state.env,
;;         store=new_store,
;;         k=task_state.kontinuation,
;;     )
;;
;; With Hy macro:

(define-handler StatePutEffect [eff ts store]
  (continue-value None
    :store {#** store (. eff key) (. eff value)}))


;; Current Python (handlers/control.py lines 23-34):
;;
;; def handle_local(effect: LocalEffect, task_state: TaskState, store: Store) -> FrameResult:
;;     new_env = task_state.env | FrozenDict(effect.env_update)
;;     return ContinueProgram(
;;         program=effect.sub_program,
;;         env=new_env,
;;         store=store,
;;         k=[LocalFrame(task_state.env)] + task_state.kontinuation,
;;     )
;;
;; With Hy macro:

(define-handler LocalEffect [eff ts store]
  (import doeff._vendor [FrozenDict])
  (import doeff.cesk.frames [LocalFrame])
  (continue-program (. eff sub_program)
                    (LocalFrame (. ts env))
                    :env (| (. ts env) (FrozenDict (. eff env_update)))))


;; Current Python (handlers/control.py lines 37-47):
;;
;; def handle_safe(effect: ResultSafeEffect, task_state: TaskState, store: Store) -> FrameResult:
;;     return ContinueProgram(
;;         program=effect.sub_program,
;;         env=task_state.env,
;;         store=store,
;;         k=[SafeFrame(task_state.env)] + task_state.kontinuation,
;;     )
;;
;; With Hy macro:

(define-handler ResultSafeEffect [eff ts store]
  (import doeff.cesk.frames [SafeFrame])
  (continue-program (. eff sub_program)
                    (SafeFrame (. ts env))))


;; =============================================================================
;; Trade-off Analysis
;; =============================================================================
;;
;; LINES OF CODE COMPARISON (per handler):
;;   Python: 8-12 lines
;;   Hy:     3-5 lines
;;   Savings: ~60%
;;
;; BENEFITS:
;;   1. Eliminates repeated ContinueValue/ContinueProgram boilerplate
;;   2. Frame pushing is implicit in continue-program
;;   3. env/k inheritance is automatic (no task_state.env everywhere)
;;   4. Type annotations aren't needed (runtime checked anyway)
;;
;; COSTS:
;;   1. Team must learn Hy syntax
;;   2. IDE support is weak (no autocompletion in Hy files)
;;   3. Debugging shows compiled Python, not Hy source
;;   4. Macro errors can be cryptic
;;   5. Two-language codebase increases cognitive load
;;
;; VERDICT:
;;   The boilerplate reduction is real (~60% fewer lines per handler).
;;   However, the tooling/debugging cost may not be worth it unless:
;;   - You're adding handlers frequently
;;   - Team is comfortable with Lisp syntax
;;   - You value DSL expressiveness over IDE support
;;
;;   RECOMMENDATION: Keep handlers in Python, use match-based step function.
;;   The Python match approach gives most of the benefit with none of the cost.
