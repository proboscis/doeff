;;; Executable ADR: the final control instruction of a handler clause (Transfer/Resume/Pass/Delegate)
;;; is executed from the handler's top-level frame — if the final instruction runs in the middle of a nested @do delegation,
;;; the delegating ancestor frames are held forever and the terminal task sweep structurally breaks down.
;;; A tail resume is a Transfer (immediate release), not a Resume (GC-dependent).

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-002
  :title "handler frame reclamation: the final control instruction of a handler clause (Transfer/TransferThrow/Resume/Pass/Delegate) is always yielded from the top-level frame of the handler body — executing the final instruction deep inside a `yield sub-@do(effect, k)` delegation leaves every delegating ancestor frame unreclaimable and, via the Task handle weakref, blocks the scheduler's terminal-entry sweep forever. A tail resume uses Transfer/TransferThrow (frames parked on Resume are reclaimed only by waiting for the cycle GC)"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/_handlers.py"
          "packages/doeff-time/src/doeff_time/_handlers"
          "packages/doeff-google-secret-manager/src/doeff_google_secret_manager/_handlers/production.py"
          "packages/doeff-secret/src/doeff_secret/_handlers.py"
          "tests/test_handler_tail_transfer_reclamation.py"
          "docs/adr/defadr_doeff_core_effects_002_handler_frame_reclamation.hy"]
  :problem
    [(fact
       "In live operation (proboscis-ema burst-taker X5'/X6), detect-to-submit latency degraded in proportion to elapsed time (2.87ms@0h → 79.31ms@4.5h), RSS +97MB/2.5h. A process restart resets it. On the Rust side detect_internal_ms is flat — all of the drift is on the Python/doeff side. py-spy on-CPU is dominated by gc_collect_main + PyObject_GetAttr + generator machinery."
       :evidence "proboscis-ema ISSUE-TRD-185; experiments/2026-07-13-doeff-scheduler-accum-repro/")
     (fact
       "Domain-independent minimal reproduction: a 50ms-beat loop of `Spawn x2 keepalive(Await asyncio.sleep) + Gather + Delay` accumulates exactly linearly +2 task-dict entries and +5 suspended generators per beat (200/400/600 beats → tasks 400/800/1200, generators 1003/2003/3003). The promise sweep works (bounded at ~600)."
       :evidence "proboscis-ema experiments/2026-07-13-doeff-scheduler-accum-repro/repro_accum_pure_doeff_20260713.hy + .out.txt")
     (fact
       "Forensics with co_filename on 2026-07-14 identified the held frames: every leftover generator belonged to doeff-time async_time.py — the wrapper `handler` (old line 92, parked on the yield of `return (yield runtime.handle(effect, k))`) at +4 per beat, and the dispatcher `handle` (old line 71, parked on the yield of `yield self._handle_delay(effect, k)`) at +1. The frames of the flat await_handler in doeff_core_effects/_handlers.py were reclaimed by gc.collect (even with a tail Resume)."
       :evidence "Result of the 2026-07-14 diagnostic script: ('async_time.py','handler',92) 480/120beats, ('async_time.py','handle',71) 120/120beats; converting handlers.py alone (before the fix) left the leak rate unchanged at 5/beat")
     (fact
       "The held frames survive a full gc.collect() (the census counts after a collect every time). The locals of the parked frames strongly reference the Task handle, so the 'all handle weakrefs dead' condition of scheduler.sweep_terminal_unobserved_entries never holds — every one of the 400 terminal task entries has exactly 1 live weakref (to the Task)."
       :evidence "proboscis-ema repro_accum_forensics_20260713.hy: all 400 tasks terminal, alive=1 for every key of handle_refs")
     (fact
       "Control experiment: a flat (non-delegating) handler shows zero growth in the beat loop even with a tail `Resume` (counted after gc.collect). However, reclamation depends on the cycle GC. A Transfer tail has the same shape as defhandler's TCO (doeff-hy handle.hy `_tail-resume-to-transfer`; _tco-seq is applied to every clause), and the frame is released immediately by reference counting."
       :evidence "tests/test_handler_tail_transfer_reclamation.py measured 2026-07-14 (flat-Resume control growth=0); packages/doeff-hy/src/doeff_hy/handle.hy:399")
     (fact
       "With nested delegation removed (flattened) + a Transfer tail, the same beat loop is completely flat (generators fixed at 3, tasks ~390 with the sweep working, objs slightly decreasing)."
       :evidence "proboscis-ema repro_accum_transfer_fix_20260713.hy (re-verified on doeff main f445a7d7 on 2026-07-14); tests/test_handler_tail_transfer_reclamation.py")]
  :context
    [(interpretation
       "The VM releases the frame that itself executed the final control instruction (Transfer/Pass etc.), but ancestor frames that are at that moment delegating via `yield <sub-program>` remain reachable from live roots even though they will 'never be driven again'. So the main cause of the leak is not Resume vs. Transfer but the frame structure of the handler body (the depth of delegation). Abandoned ancestor frames are also a latent issue on the VM side, but changes to the doeff VM's frame/fiber machinery are the highest-alert area of the Change Protocol, and restoring the invariant through a handler-side contract (final instruction from the top-level frame) is the correct fix at the owning layer. Improving the VM to reclaim abandoned ancestor frames is a separate future issue.")
     (interpretation
       "Sub-program delegation itself is not forbidden: value-returning sub-programs that complete before the final instruction (`(yield attempt())` or `_wait_for_time`) are safe because they leave no parked frame on the stack when Transfer executes. What must be forbidden is the dispatcher-decomposition pattern of 'delegating to a sub-@do that receives effect and k and itself goes all the way to resume/transfer/pass'.")
     (interpretation
       "The 22 Resume sites in scheduler.py (handle_scheduler_effect) are out of scope for this ADR: the TailEval trampoline drives its frames itself, and the ISSUE-TRD-185 forensics also found zero leftover scheduler frames. Rewriting the most delicate code 'for consistency' without a reproducing failure violates the Change Protocol.")
     (interpretation
       "Handlers built with Hy defhandler already conform to this ADR, because the `_tail-resume-to-transfer` TCO is applied to clause bodies (handle.hy:399) and clause bodies expand into a single frame. The exposed surface is hand-written Python @do handlers. The `return (yield Resume(k, ...))` sites (about 90) in unconverted packages such as doeff-traverse/gemini/agentic/openai remain as R2 violations — they are outside the burst-taker live stack and are not fixed in this change set; when they are converted, this ADR's enforcement is promoted to the whole repo.")]
  :decision
    [(rule R1 "The final control instruction of a handler clause (Transfer/TransferThrow/Resume/ResumeThrow/Pass/Delegate) is yielded directly from the top-level @do frame of the installed handler callable. A `yield` delegation to a sub-@do that is passed effect and k and executes the final instruction deep down (wrapper/dispatcher decomposition) is forbidden.")
     (rule R2 "A tail resume (the final instruction resumes k and nothing follows it) uses Transfer / TransferThrow. Tail use of Resume / ResumeThrow is forbidden on the hot path, because even with flat frames it means delayed reclamation that waits for the cycle GC. Resume is legitimate only for a true non-tail where work remains after the resume (e.g. the wrapping in the old try_handler).")
     (rule R3 "To decompose a handler, use the shape 'value-returning sub-program + Transfer in the top-level frame': `return (yield Transfer(k, (yield runtime.get-secret effect)))`. The sub-program does not receive k and always completes before the final instruction. (doeff-google-secret-manager production.py is the reference implementation.)")
     (rule R4 "The Resume/ResumeThrow sites inside scheduler.py are managed by the TailEval trampoline and are out of scope for this ADR. Do not change them without reproducing evidence of leftover frames.")
     (rule R5 "The run-time enforcement of this contract is the 2 tests in tests/test_handler_tail_transfer_reclamation.py (flatness of the stock stack + ability to detect the nested-delegation counterexample). For changes that add handlers to the stock stack, that test is the regression net.")]
  :laws
    [(law handler-final-instruction-from-top-frame
       :statement "for_all handler_clause: frame_yielding(final_control_instruction) == top_level_handler_frame; delegating_ancestor_frames_at_final_instruction == 0"
       :counterexamples
         [(counterexample "A wrapper @do delegates to a dispatcher with `return (yield runtime.handle(effect, k))` and the leaf does the Transfer (all 3 doeff-time handlers before 2026-07-14) — wrapper+dispatcher frames are left behind forever every beat, the terminal task sweep stops, live dts 2.9ms→79ms/4.5h")
          (counterexample "The `return (yield runtime.handle_get_secret(effect, k))` shape in GSM production_handlers — a dispatcher frame is left behind on every GetSecret")])
     (law tail-resume-is-transfer
       :statement "for_all handler_clause: last_action_resumes_k => uses(Transfer | TransferThrow); Resume_parked_flat_frames are not uncollectable but depend on GC cycles and are not allowed on the hot path"
       :counterexamples
         [(counterexample "`result = yield Resume(k, value); return result` (25 sites such as await_handler/state/lazy_ask in handlers.py before 2026-07-14) — when flat they are reclaimed by GC, but reclamation is rate-limited by the cycle GC frequency and increases allocation pressure on the hot path")
          (counterexample "The asymmetry of having defhandler's TCO only on the Hy side (Hy handlers transfer, Python handlers resume)")])
     (law terminal-task-entries-are-swept
       :statement "under beat_loop(Spawn+Await+Gather+Delay), the number of terminal entries in the scheduler tasks-dict and of suspended handler generators is bounded (per-beat growth 0)"
       :counterexamples
         [(counterexample "Linear accumulation of tasks +2 / generators +5 per beat (repro_accum_pure_doeff_20260713.hy) — tens of thousands of objects within hours, with full-GC scan time added directly to latency")])]
  :enforcement
    ;; Run-time enforcement is tests/test_handler_tail_transfer_reclamation.py
    ;; (the pair of an assert on stock-stack flatness + an assert that the nested-delegation
    ;; counterexample is detected). Below is the inline self-check of the static guards — promotion to the installed
    ;; .semgrep.yaml happens after the conversion of the packages that still violate R2 (doeff-traverse etc.)
    ;; is complete (applying it to the whole repo now would make the unconverted packages violate immediately).
    [(defsemgrep no-resume-tail-oneliner-in-handlers
       :languages ["python"]
       :pattern "return (yield Resume($K, $V))"
       :message "Per ADR-DOE-CORE-EFFECTS-002 R2 a tail resume uses Transfer: `return (yield Transfer(k, value))`. Resume is legitimate only for a true non-tail where work remains after the resume (which never takes this shape)."
       :bad ["return (yield Resume(k, value))"]
       :good ["return (yield Transfer(k, value))"])
     (defsemgrep no-effect-k-delegation-in-handler
       :languages ["python"]
       :pattern "return (yield $RT.$METHOD(effect, k))"
       :message "Dispatcher decomposition that passes effect and k to a deep sub-@do and delegates the final instruction to it violates ADR-DOE-CORE-EFFECTS-002 R1 — the delegating ancestor frames are left behind forever. Rewrite as a value-returning sub-program + Transfer in the top-level frame (R3)."
       :bad ["return (yield runtime.handle_get_secret(effect, k))"]
       :good ["return (yield Transfer(k, (yield runtime.get_secret(effect))))"])]
  :plans [])
