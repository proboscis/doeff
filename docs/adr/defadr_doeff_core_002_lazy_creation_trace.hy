;;; Executable ADR: capture of creation context on the effect-creation hot path is deferred and minimized.
;;; The per-effect cost of tracing (class 1 middleware) must not erode
;;; the hot path. Enforcement is added after the C1 spike (identifying the capture sites).

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest defk <-])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [Ask run])
(import linecache)


;; Hot-path probe for the R1 regression guard: an error-free Ask dispatch loop.
(defk _probe-hot-loop []
  {:pre [] :post [(: % int)]}
  (setv total 0)
  (for [_ (range 50)]
    (<- v (Ask "x"))
    (setv total (+ total v)))
  total)


(defadr ADR-DOE-CORE-002
  :title "lazy creation trace (regression guard): the effect creation / dispatch hot path does no line-text resolution (linecache), string formatting, or stack walk. Capture fires only from opt-in VM introspection instructions (GetTraceback / GetExecutionContext) and error paths — the C1 spike (2026-07-14) confirmed current main already has this property, so this ADR pins it as an invariant and guards it against future erosion"
  :status "proposed"
  :scope ["doeff/traceback.py"
          "packages/doeff-core-effects"
          "docs/adr/defadr_doeff_core_002_lazy_creation_trace.hy"]
  :problem
    [(fact
       "Downstream measurement (proboscis-ema, 2026-06): in the simulation interpreter of that time, per-effect-creation capture of creation context (stack inspection + line-text resolution) took about 32% of run time, a 3.7x overall slowdown with 4.1x function calls. That issue's conclusion rejected a Rust rewrite (the interpreter core is only ~9% of the total)."
       :evidence "proboscis-ema VAULT issue 'doeff Tracing Overhead Causes 3.7x Slowdown' (open as of 2026-07-14)")
     (fact
       "C1 spike (2026-07-14) verdict: this capture cost does not exist on current main. Capture fires only from opt-in VM introspection instructions (GetTraceback / GetExecutionContext, step.rs:320-341) and error paths (step_raise step.rs:115, unhandled_effect dispatch.rs:66-74), and never runs on the normal Perform→dispatch→Resume/Transfer path. Line-text resolution (linecache) happens only in doeff/traceback.py:105-112, whose caller is the except clause of run() (doeff/run.py:16-34) = render time only. A measured 100k-iteration Ask loop also made 0 linecache / stack-walk / string-formatting calls (breakdown: Rust VM core 48% / @do generator-rebuild glue ~39% / effect construction ~7% / user code ~7%)."
       :evidence "scratchpad bench_ask_loop.py + ask_loop.pstats (2026-07-14 C1 spike); packages/doeff-vm/src/python_generator_stream.rs:371-409; packages/doeff-vm-core/src/vm.rs:76-213")
     (fact
       "Only one literal non-deferral remains: the exception path of run(), _enrich_exception_traceback / _merge_python_frames (doeff/run.py:38-100), calls traceback.extract_tb without lookup_line=False, eagerly reading linecache once per uncaught exception. It is outside the hot path but contradicts the letter of R1."
       :evidence "doeff/run.py:38-100 (C1 spike 2026-07-14)")
     (fact
       "Positioning as a general rule: an instance of 'the price of class 1 middleware (tracing) is per-effect overhead'. Current main already avoids this by design, so this ADR's role is not a fix but a regression guard (protecting this property from future changes). The downstream 3.7x was measured against an old version and is expected to go away with a pin update — re-verifying the downstream issue is remaining work."
       :evidence "docs/22-capability-classes.md 'When Adoption Pays: The Evidence Rule'; C1 spike 2026-07-14")]
  :context
    [(interpretation
       "'You get the call tree automatically and for free' (benefit #3 in docs/20-why-effects-over-di.md) does not hold as long as creation-time capture is always on — it is free in code, not at run time. Defer what can be deferred (line text and formatting at render time), and reduce what cannot be deferred (stack info must be taken before the frame disappears) to minimal raw data.")
     (interpretation
       "Hypothesis before the C3 ruling: creation-context capture is an observability concern (tracing), and moving it onto the handler stack was considered. Post-mortem debugging of production failures takes 're-run with tracing enabled under memo replay' as the canonical path (a multi-shot approximation via Program-as-Value + class 1 replay).")
     (interpretation
       "Rescoping as a consequence of the C1 spike: R1 already holds on current main (not through deliberate deferral, but because capture was designed to be limited to opt-in VM introspection instructions + error paths). So this ADR's enforcement is not a 'fix it' test but a regression guard that 'fails if this property erodes'. The old R2 (opt-in migration to an observability handler) lost its premise and was shrunk — the remaining C3 point is only the independent design decision 'keep GetTraceback / GetExecutionContext as built-in VM instructions, or move them behind an observability handler'.")
     (interpretation
       "C3 ruling: GetTraceback / GetExecutionContext are not effects but DoCtrl VM control instructions (packages/doeff-vm-core/src/do_ctrl.rs:82,86), handled directly by the VM when classifying a yield (packages/doeff-vm/src/python_generator_stream.rs:659-664; GetTraceback resolves to a Pure value on the spot). They do not go through effect dispatch and are invisible from the handler stack, so they are VM introspection instructions of the same family as GetHandlers / GetBoundaries. Since the C1 spike removed the perf rationale and there is no current real need to deny introspection in a sandbox, per the Evidence Rule (docs/22-capability-classes.md) we do not pay the cost of a Rust change, a breaking change, or a privileged API for handlers.")]
  :decision
    [(rule R1 "The effect creation / dispatch hot path does no linecache reads, string formatting, line-text resolution, or stack walk. Capture fires only from opt-in VM introspection instructions (GetTraceback / GetExecutionContext) and error paths (pinning the property of current main as an invariant).")
     (rule R2 "The exception path of run(), _merge_python_frames, calls traceback.extract_tb with lookup_line=False and defers line-text resolution to render (format) time (closing the literal gap; a small fix outside the hot path).")
     (rule R3 "GetTraceback / GetExecutionContext stay as they are, as VM introspection instructions (the GetHandlers / GetBoundaries family), and are not moved onto an observability handler. If a real need to deny introspection in a sandbox arises in the future, design 'an execution mode that can deny introspection instructions' in a separate ADR. The @do generator-rebuild glue (measured ~39%) is out of scope for this ADR — file it as a separate issue.")]
  :laws
    [(law effect-creation-is-cheap
       :statement "for_all effect_creation: linecache_calls == 0 AND string_formatting == 0; human_readable_resolution happens_at render_time only"
       :counterexamples
         [(counterexample "every effect creation does a stack walk + resolves line text immediately with linecache.getline (the shape measured downstream as a 3.7x slowdown)")
          (counterexample "capture code runs even when tracing is disabled (the flag stops only the display, not the capture)")])]
  :enforcement
    [(deftest test-adr-doe-core-002-no-linecache-on-hot-path
       ;; Regression guard (pins the property confirmed by the C1 spike 2026-07-14):
       ;; zero line-text resolution (linecache.getline) in an error-free effect dispatch loop.
       ;; If per-effect creation-context capture comes back in the future, this turns red.
       (import doeff_core_effects [reader])
       (setv calls [])
       (setv orig linecache.getline)
       (defn _counting [#* a #** k]
         (.append calls a)
         (orig #* a #** k))
       (setv (. linecache getline) _counting)
       (try
         (setv result (run ((reader {"x" 1}) (_probe-hot-loop))))
         (finally (setv (. linecache getline) orig)))
       (assert (= result 50) f"probe result is wrong: {result}")
       (assert (= (len calls) 0)
               f"linecache.getline was called {(len calls)} times on the hot path — ADR-DOE-CORE-002 R1"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
