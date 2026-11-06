;;; Executable ADR: slog is an observability effect — its wire type is separated from Writer (Tell/Listen),
;;; the sink is "visible" by default, and collection is only through Listen's value flow (the .log attribute side-channel is removed entirely).

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-001
  :title "Separate slog from Writer by type as SlogEffect, make slog_handler = stderr terminal sink, and unify collection into the value flow of Listen(types=(SlogEffect,)) — a mutable collection attribute (.log) on a handler install is a removed API and is forbidden everywhere"
  :status "proposed"
  :scope ["packages/doeff-core-effects"
          "doeff/cli/run_services.py"
          "packages/doeff-preset"
          "packages/doeff-time"
          "packages/doeff-docker"
          "docs/adr/defadr_doeff_core_effects_001_slog_observability.hy"]
  :problem
    [(fact
       "The name slog (structured-logging vocabulary from Go's slog) sets the observability expectation 'yield it and you see it', yet the implementation shares the same wire type as Writer's Tell (Slog = WriterTellEffect), and slog_handler() silently collects into a list. The CLI's default_interpreter installs slog_handler() but nobody reads the collected result and it is thrown away — a user who yields slog sees nothing (measured 2026-07-13: UnhandledEffect under a bare run(), complete silence with slog_handler installed)."
       :evidence "doeff/cli/run_services.py:191; packages/doeff-core-effects/doeff_core_effects/_handlers.py:135-154; packages/doeff-core-effects/doeff_core_effects/_effects.py:110-134")
     (fact
       "In the default_interpreter stack, slog_handler sits inside writer(), so slog_handler consumes every WriterTellEffect first, including Tell(), and writer() collects nothing — the conflation of Writer and slog already does not work in the default configuration."
       :evidence "the wrap order (reversed) in doeff/cli/run_services.py:190-197")
     (fact
       "The .log side-channel, which grows a mutable collection attribute on a handler install, was supposed to be a removed API but survived/regrew for lack of enforcement (erosion). Producers: handlers.py:89 (writer) and handlers.py:153 (slog_handler). Readers: tests/test_core_effects.py:170-171, tests/effects/http_request_support.hy:85, packages/doeff-vm/tests/test_pyvm.py:210. The docstring at handlers.py:76 also mentions a handler_log that does not exist."
       :evidence "packages/doeff-core-effects/doeff_core_effects/_handlers.py:76,89,153; git log -S 'install.log' (no ADR recorded the removal)")
     (fact
       "The display presets are already broken against the current API: log_display.py:94 references effect.message, which does not exist on WriterTellEffect (the current fields are .msg/.kwargs), and it also uses the old Pass() signature. tests/test_pass_then_typed_handler.py:54 references the same attribute. sim_time.py:130 and docker dockerfile.hy:58 do a side isinstance on WriterTellEffect and are within the blast radius of the type separation."
       :evidence "packages/doeff-preset/src/doeff_preset/handlers/log_display.py:90-102; packages/doeff-time/src/doeff_time/_handlers/sim_time.py:130; packages/doeff-docker/src/doeff_docker/_handlers/dockerfile.hy:58")]
  :context
    [(interpretation
       "slog and Tell are distinct effects whose correct defaults are opposite: Writer (Tell) is a data flow that is correct only when it accumulates quietly; slog is observability that is correct only when it is visible. As long as they share one wire type, a configuration in which one handler betrays the other's expectation (logs silently vanish) can always be built. The industry's convergence teaches the same lesson: Go slog and Python logging (lastResort) converged on 'default sink = stderr', while Rust log / OCaml Logs with a nop default became well-known footguns.")
     (interpretation
       "Listen is a tee (observer), not a sink — listen_handler keeps passing effects outward with Pass after collecting them, and is a general observer whose types can be specified (handlers.py:205-236). Therefore changing the sink semantics and the need for collection are independent, and collection can be satisfied by Listen's value flow (result, collected). There is no reason to grow a mutable attribute on a handler object.")
     (interpretation
       "The VM's 'unhandled effect = error' is an invariant of the coupling core. Putting a Python-logging-style lastResort into the VM as a special case would be an erosion that breaks the invariant for just one effect type. The visibility guarantee is achieved by the entry points (CLI, test harness) installing a sink in the handler stack as standard.")
     (interpretation
       "2026-07-13 integration ruling (merge with main 0acce3a9): main had already carried out the same .log removal as 'State collection + writer_log()/slog_log() value flow + handlers as installers + no direct IO from handlers'. In the integrated form slog_handler is used only for display: slog_handler is a display-only observability IO boundary (a boundary handler of the same shape as the ShellRun handler, consistent with the no-direct-IO philosophy); Tell accumulation follows main's writer + writer_log() (State collection); and slog_log() is retired (a display-only sink has no state to read; slog collection is Listen's value flow). The handler shape is unified on main's installer form.")]
  :decision
    [(rule R1 "Wire type separation: slog(msg, **kwargs) returns the new SlogEffect(msg, **kwargs). The Slog alias is repointed to SlogEffect. WriterTellEffect goes back to being for Tell() only, and the default target of writer() / Listen (types=(WriterTellEffect,)) is unchanged. No inheritance relation is created between SlogEffect and WriterTellEffect (neither may be caught by the other's isinstance).")
     (rule R2 "slog_handler is a display-only terminal sink for SlogEffect (installer shape, consistent with main 0acce3a9): it writes a rich-independent one-line format (uses the level kwarg if present, otherwise INFO; remaining kwargs as key=value) to stderr and consumes the effect (Resume k None). Display is its only job; it does no collection at all (holds neither state nor attributes). 'Install slog_handler and logs are visible' is the handler contract. stdout is not used (it is reserved for the program's result). Writing to stderr is isolated in this one handler as the observability IO boundary.")
     (rule R3 "A side-channel that grows a mutable collection attribute (.log etc.) on a handler install is forbidden everywhere (formal record of a removed API). There are 2 sanctioned collection paths: for slog, the value flow (result, collected) of Listen(prog, types=(SlogEffect,)); for Tell, writer (State collection) + the value flow of the effect ReadWriterLog that writer answers (main 0acce3a9's design; the reading function writer_log() was rebuilt as the effect ReadWriterLog on 2026-10-27). slog_log() is retired — now that slog_handler is display-only, there is no slog state to read.")
     (rule R4 "An unhandled SlogEffect remains UnhandledEffect as before (no lastResort special case in the VM; Listen / Tell are equally loud without a handler). Visibility is guaranteed by what entry points install as standard: default_interpreter in doeff/cli/run_services.py installs slog_handler (stderr sink), and test harnesses (including a future doeff_interpreter fixture) also default to a display sink — pytest captures stderr, so test output stays clean, and only tests that assert on logs use Listen.")
     (rule R5 "Silence is an explicit opt-in: provide slog_discard_handler (an installer-shaped sink that silently consumes SlogEffect). slog_capture_handler is not introduced — its only way to return the collected result would be a side-channel attribute (an R3 violation), and it would completely duplicate Listen's value flow.")
     (rule R6 "Consumers that do a side isinstance on WriterTellEffect are aligned with the type separation: sim_time's log formatting targets SlogEffect instead; docker dockerfile.hy states its target type explicitly according to its purpose; doeff-preset's display code is either repaired to the current API (.msg/.kwargs, Pass(effect, k)) and offered as a rich sink replacement, or retired. Referencing the nonexistent effect.message attribute is forbidden.")]
  :laws
    [(law slog-tell-types-disjoint
       :statement "wire_type(slog) = SlogEffect and wire_type(Tell) = WriterTellEffect and SlogEffect is_not WriterTellEffect"
       :counterexamples
         [(counterexample "writer() collects slog output / slog_handler consumes Tell and Writer becomes decoration (what the old default_interpreter actually did)")
          (counterexample "SlogEffect is made a subclass of WriterTellEffect and is caught by isinstance for both")])
     (law slog-is-visible-by-default
       :statement "installed(slog_handler) => every_yielded_SlogEffect_reaches_stderr"
       :counterexamples
         [(counterexample "The sink only collects into a list without displaying, and the collected result is thrown away (default_interpreter before 2026-07-13)")
          (counterexample "The default sink writes to stdout and mixes with the program's result")])
     (law no-handler-side-channel
       :statement "handler_install_exposes_no_mutable_collection_attribute; capture_flows_as_values_via_Listen"
       :counterexamples
         [(counterexample "install.log = log, reading the collected result from a handler attribute (regrowth of a removed API)")
          (counterexample "A new slog_capture_handler is added for collection and returns the result via an attribute or a file")])
     (law unhandled-slog-is-loud
       :statement "no_handler_in_scope(SlogEffect) => UnhandledEffect; no_vm_lastresort_special_case"
       :counterexamples
         [(counterexample "The VM sends only SlogEffect to stderr and continues execution (a one-type exception to the invariant)")])]
  :enforcement
    ;; The real pytest suite landed in tests/test_slog_semantics.py (8 tests: stderr visibility /
    ;; level formatting / slog not mixed into Listen's default / slog_handler passes Tell through /
    ;; value-flow collection via Listen(types=(SlogEffect,)) / no .log attribute / unhandled is loud /
    ;; slog_discard_handler is silent). The installed semgrep rules are in .semgrep.yaml
    ;; (no-handler-log-attribute / no-effect-message-attribute /
    ;;  no-structured-payload-on-writer-tell / no-writer-tell-alias-slog).
    ;; This is the inline self-check of the static guards.
    [(defsemgrep no-handler-log-attribute
       :languages ["generic"]
       :pattern "install.log ="
       :message "A mutable collection attribute on a handler install violates ADR-DOE-CORE-EFFECTS-001 R3 (removed API). Collect through the value flow (result, collected) of Listen(prog, types=(SlogEffect,))."
       :bad ["install.log = log  # expose for inspection"]
       :good ["return install"])
     (defsemgrep no-effect-message-attribute
       :languages ["python"]
       :pattern "isinstance($E, WriterTellEffect) and isinstance($E.message, $T)"
       :message "WriterTellEffect / SlogEffect have no message attribute (the current fields are .msg / .kwargs). Referencing effect.message violates ADR-DOE-CORE-EFFECTS-001 R6 (stale API of the broken preset). Matched with an isinstance-guarded shape to distinguish it from the legitimate .message field of other domains' effects."
       :bad ["if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):\n    pass"]
       :good ["if isinstance(effect, SlogEffect):\n    pass"])
     (defsemgrep no-writer-tell-alias-slog
       :languages ["python"]
       :pattern "from doeff_core_effects._effects import WriterTellEffect as Slog"
       :message "Importing WriterTellEffect under the alias Slog regrows the conflation that ADR-DOE-CORE-EFFECTS-001 R1 retired (seen in memo_handlers on 2026-07-13). Observability is SlogEffect / slog()."
       :bad ["from doeff_core_effects._effects import WriterTellEffect as Slog"]
       :good ["from doeff_core_effects._effects import Slog"])
     (defsemgrep no-structured-payload-on-writer-tell
       :languages ["python"]
       :pattern "WriterTellEffect($MSG, **$KW)"
       :message "Constructing a WriterTellEffect with a structured payload violates ADR-DOE-CORE-EFFECTS-001 R1. Observability is SlogEffect(msg, **kwargs); Tell takes a single message only."
       :bad ["return WriterTellEffect(msg, **kwargs)"]
       :good ["return SlogEffect(msg, **kwargs)"])]
  :plans ["docs/doeff-2026-07-13-slog-semantics-architecture-plan.md"])
