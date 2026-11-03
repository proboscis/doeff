;;; Executable ADR: Spawn inherits the spawn-site boundary stack (handler + observer).

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-001
  :title "Spawn captures and reinstalls WithObserve boundaries just like handlers (preserving interleave order)"
  :status "accepted"
  :scope ["doeff-core-effects" "scheduler.Spawn" "doeff-vm.GetBoundaries" "WithObserve"]
  :problem
    [(fact
       "A WithObserve placed inside scheduled() observes none of the effects performed by child tasks created with Spawn. A handler (WithHandler) at the same position is inherited by the child, but the observer alone is dropped (silent drop — a violation of the fail-fast principle)."
       :evidence "reproduction in issues/scheduler-spawn-drops-observer-boundary.md @ cacc4b85; tests/test_spawn_observer_inheritance.py has 5 failures on the pre-fix HEAD")
     (fact
       "Mechanism: Spawn captures only prompt boundaries via get_inner_handlers (= GetHandlers(k)) and reinstalls them on the child task. Intercept (observer) boundaries are not captured. Observer invocation walks the fiber parent chain, and a Spawn child fiber has the scheduler's evaluation context as its parent, so the observer boundaries between the spawn site and the scheduler are not on that chain."
       :evidence "Spawn branch in packages/doeff-core-effects/doeff_core_effects/_scheduler.py; doeff/handler_utils.py; call_all_observers in packages/doeff-vm-core/src/vm/step.rs")
     (fact
       "Practical impact: slog inside doeff-traverse parallel workers does not appear in observer-based output (proboscis-ema, 2026-04). If OpenTelemetry effect→span auto-instrumentation is written scoped to a subtree, sections containing Spawn end up 'instrumented but invisible'."
       :evidence "the issue's motivation section")]
  :context
    [(interpretation
       "As an observation expectation, the inheritance rules for handlers and observers should be symmetric. No specification document justifying the asymmetry existed (issue point 1).")
     (interpretation
       "Relative order is also part of the semantics: in the arrangement handler(WithObserve(obs, body)), effects emitted by the handler body are invisible to obs. A flat reinstall that 'stacks observers outermost' produces over-observation (duplicate spans, etc.). Therefore capture and reinstall preserve order, interleaved (innermost-first).")
     (interpretation
       "The primitive is the opt-in query GetBoundaries(k). It is symmetric with GetHandlers(k) and does not consume the continuation (multi-shot safe). The existing GetHandlers and its many call sites (try_handler, doeff-traverse, etc.) are unchanged (adopting the opt-in option from issue point 2, while the scheduler inherits by default).")]
  :decision
    [(rule R1 "Add GetBoundaries(k) to the doeff-vm bridge: it returns [\"handler\"|\"observer\", callable] from the spawn site up to the catching handler, innermost-first. It includes the catching handler's prompt boundary as the final entry (symmetric with GetHandlers).")
     (rule R2 "doeff.handler_utils.get_inner_boundaries(k) returns GetBoundaries(k) with the final entry (the caller itself) dropped. If the final entry is not of kind handler, raise RuntimeError (fail-fast).")
     (rule R3 "The scheduler's Spawn captures the boundary stack with get_inner_boundaries, and when rebuilding the child task reinstalls WithHandler / WithObserve according to kind, innermost-first. The relative nesting order of handlers and observers is preserved.")
     (rule R4 "Reinstalled boundaries are owned by the child task's program and are released by _release_task_refs at task termination (completion, failure, cancellation). Scheduler-internal effects (TaskCompleted from wrap_task, etc.) are performed outside the boundaries and are not observed by subtree observers (issue point 3).")
     (rule R5 "Mask boundaries are out of scope for this ADR (as before, not inherited by Spawn). If inheritance becomes necessary, revise this ADR by adding a kind to GetBoundaries.")]
  :laws
    [(law spawn-observer-inheritance
       :statement "spawn_site_boundary(observer or handler) => reinstalled_on_child preserving_relative_order"
       :counterexamples
         [(counterexample "with scheduled(WithObserve(obs, h(main))), the child task's effects are not included in observed")
          (counterexample "observers are stacked flat at the outermost position, so even effects emitted by the handler body get observed")
          (counterexample "the Spawn path regresses to get_inner_handlers and only the observer is dropped")])]
  :enforcement
    [(defsemgrep scheduler-spawn-boundary-capture
       "doeff-scheduler-spawn-must-capture-boundaries"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/_scheduler.py"
         "source" "inner_handlers = yield get_inner_handlers(k)\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/_handlers.py"
         "source" "inner_hs = yield get_inner_handlers(k)\n"}])]
  :plans ["docs/adr/defadr_doeff_core_001_spawn_boundary_inheritance.hy"
          "tests/test_spawn_observer_inheritance.py"
          "packages/doeff-vm-core/src/continuation.rs (boundary_callables)"
          "packages/doeff-vm/src/do_expr.rs (GetBoundaries pyclass)"
          "packages/doeff-core-effects/doeff_core_effects/_scheduler.py (Spawn boundary capture)"])
