;;; Executable ADR: doeff's answer to FP's "forest of pure small functions that belong nowhere" problem is
;;; defdomain, which binds cohesion at the declaration level of "vocabulary + laws", SEDA (CLI/MCP), which agents can
;;; query, and conformance checks. For an agent, documentation is
;;; a check that fails.
;;; R1 was implemented in packages/doeff-domain as E1 on 2026-07-17 — this file's
;;; enforcement has been replaced from designed-red sentinels with green behavior pins.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [DoExpr])
(import doeff_domain [DomainError GetDomain])


(defk failure-of [program]
  {:pre [(: program DoExpr)] :post [(: % (| DomainError KeyError))]
   :tags {:context "doeff-domain-adr" :role "program"}}
  "Red-side check: confirms that program ends with a failure thrown back from the domain table's handler (a DomainError subclass or KeyError) and returns that failure (red if it answers)."
  (try
    (<- answer program)
    (raise (AssertionError (+ "the program answered instead of failing: " (repr answer))))
    (except [failure #(DomainError KeyError)]
      failure)))


(defk read-domains [names]
  {:pre [(: names list)] :post [(: % list)]
   :tags {:context "doeff-domain-adr" :role "program"}}
  "Dogfood check: the list of domains obtained by looking up each name in the list with GetDomain one by one (the table is the domain-table handler the caller installed)."
  (var found [])
  (for [name names]
    (<- domain (GetDomain name))
    (:= found [#* found domain]))
  found)


(defadr ADR-DOE-DOMAIN-001
  :title "vocabulary cohesion: the doeff-domain subpackage provides defdomain, the declaration of a unit of cohesion (a bundle of effect types + laws + the expected handler surface), an in-process registry, and conformance checks; SEDA is made queryable by agents as CLI/MCP; and conformance checks (handler coverage of a domain, the set of effects a Program uses ⊆ the set an interpreter handles, no synonymous vocabulary defined outside a domain) are provided with defsemgrep/deftest. The IDE surface is not a priority — the writers are agents"
  :status "proposed"
  :scope ["packages/doeff-domain"
          "packages/doeff-effect-analyzer"
          ".semgrep.yaml"
          "docs/adr/defadr_doeff_domain_001_vocabulary_cohesion.hy"]
  :problem
    [(fact
       "A real-damage example (sister repo ACP, core #8): an agent swarm multiplied the `_issue-terminal?` predicate to 7 definitions, terminal-set to 6 definitions (with diverging values), and failure classifiers to 3 lineages — a typical lack of FP cohesion, where new code is added because the existing one cannot be found. Nothing failed. Corrected by ADR 0031 R2/R4 (2026-07-03), which consolidated them into the single canonical predicates.hy."
       :evidence "agent-control-plane docs/erosion-audit-2026-07-02.md core #8; ACP ADR 0031; apps/hypha/shared/predicates.hy")
     (fact
       "SEDA (doeff-effect-analyzer) is a WIP advertising 'static effect dependency analysis, incremental tree-structured reports, PyO3'. The seeds of a cohesion query surface ('which effects can this Program perform?', 'where is the handler for this effect?') already exist in-house."
       :evidence "packages/doeff-effect-analyzer/README.md; specs/effect-analyzer/")
     (fact
       "Writer agents do not use IDE completion. They respond to grep, file reads, skills, and failing checks. So cohesion is secured not by improving discoverability (the IDE dot experience) but by a queryable index and conformance checks that fail."
       :evidence "the agent-first premise; the fact that the multiplier in ACP core #8 was a swarm")]
  :context
    [(interpretation
       "What OOP cohesion really is, is not a run-time mechanism (classes) but discoverability. doeff already has implicit units of cohesion — effect type family = interface, handler = implementation, handler stack = object graph — but they are neither declared nor enforced. defdomain promotes this to a declaration — it binds only vocabulary and laws, not implementation or state (importing cohesion without the cause of OOP's tight coupling).")
     (interpretation
       "For an agent, documentation is a check that fails. A domain declaration is (1) the one file an agent is made to read first, and (2) what conformance checks match against. Docs get skipped, but red always gets read.")
     (interpretation
       "Ruling (2026-07-17, ii): the declaration in law vocabulary-has-single-home is read as 'introduction' — references through includes do not count toward the number of declarations. Uniqueness of introduction is enforced at registration time keyed on class identity (not name), and the coexistence of different classes with the same name is outside D3 (that is the territory of check (c), :terms, and naming semgrep rules).")
     (interpretation
       "Ruling (2026-07-17, iv): the honest limit of check (c) — true synonym detection for predicates is impossible. Forbidding orphan effects (forcing EffectBase descendants to belong to a domain) + canonical declarations in :terms + project-side naming-pattern semgrep rules is the ceiling, and nothing more is promised.")
     (interpretation
       "Implementation status (2026-07-17): R1 is implemented in packages/doeff-domain as E1 (Domain pure data + in-process registry + defdomain macro + handles annotation + two-layer derivation of the handled set + checks (a)(c) + doeff-core-effects dogfood). R2 (SEDA CLI/MCP) waits on E2, and R3's (b) (set a Program uses ⊆ set an interpreter handles) waits on E3 (depends on SEDA), so the status stays proposed. The dogfood detected real drift: MemoDeleteEffect / CacheDeleteEffect are defined and exported as vocabulary but no handler handles them — declared as known_uncovered (once covered, the stale check forces removal of the declaration); the remedy awaits a ruling.")
     (interpretation
       "Ruling (2026-07-18, i–ii): E2 (the CLI/MCP surface for SEDA/registry queries) is deferred. Real enforcement against core-#8-style multiplication is already provided by E1's orphan ban, coverage, and stale ratchet, proven all the way from detecting the real drift of MemoDeleteEffect / CacheDeleteEffect to ruling A and its resolution in PR #546. The declarations to be queried are gathered in the one file packages/doeff-domain/src/doeff_domain/core_effects_domains.hy and are greppable, and per the Evidence Rule (docs/22-capability-classes.md) no CLI/MCP cost is paid without evidence. The condition for resuming is observing 'a case where an agent actually failed to find vocabulary', at which point it is filed again. E3's set of effects a Program uses ⊆ set an interpreter handles keeps its value, but the prerequisite enumeration contract for interpreter→handler lists is reserved for deep design discussion between frontier and humans, must not be delegated to workers, and E3 starts after that design session. The 2026-07-18 frontier read-only survey (original: 2026-07-18-seda-survey-doeff-e2.md) confirmed that in the Python analyzer, 6 of 8 cargo integration tests fail with an empty set of detected effects, that it cannot recognize the current API's EffectBase subclasses + dataclasses because it relies only on string-prefix matching such as ask(, and that specs/features/effect-analyzer.md also assumes the old API. The specs/effect-analyzer/ referenced by the README / spec is a phantom reference that does not exist. On the other hand, the Hy analyzer outside the spec (hy_reader / hy_analyzer) is green on its unit tests 14/14, and its PascalCase recognition is a rough approximation of the current API, making it a candidate foundation for E3's left-hand-side inference. Handler location and an interpreter's handled set can be queried without static analysis from the doeff-domain registry's introducing_domain / handled_effects, but the latter still has the enumeration contract above outstanding. Note that MemoDeleteEffect / CacheDeleteEffect, which awaited a ruling in the 2026-07-17 implementation status, were resolved by ruling A (PR #546, 2026-07-18).")]
  :decision
    [(rule R1 "The doeff-domain subpackage (packages/doeff-domain) provides the declaration of units of cohesion: pure data Domain (name/title/effects/includes/terms/handlers/laws/adrs/docs), an in-process registry (registration at import; re-registering the same name raises immediately), the declaration macro defdomain (Hy module doeff_domain.macros), the opt-in declaration type Handles for raw Python handlers (changed from the function handles() on 2026-10-29 — R8), the two-layer derivation of the handled set, and conformance checks (a)(c). Reading and writing the table, deriving the handled set, and the conformance checks are exposed as effects plus domain_registry, a factory that returns a handler (R8). doeff-domain must not depend on doeff-hy / doeff-adr (depending on doeff_vm.EffectBase and hy is allowed; toward doeff-core-effects only one-way, from the dogfood declaration module).")
     [removed]
     (rule R3 "Provide conformance checks with defsemgrep / deftest: (a) handlers cover the effects introduced by their declared domain (includes are the responsibility of the introducing domain); (b) the set of effects a Program uses ⊆ the handled set of the interpreter that runs it (E3 — starts after the design session on the enumeration contract for interpreter→handler lists (reserved for frontier+humans, no delegation to workers)); (c) forbid defining synonymous vocabulary (predicates or effects with the same semantics) outside domain declarations — implemented as the check 'every EffectBase descendant of the named packages is introduced in some domain'.")
     (rule R4 "Implementing this ADR is Stage E of the plan and presupposes the completion of Track B (the canonical pytest gate) — conformance checks only mean something once there is a gate that runs them.")
     (rule R5 "Ruling (2026-07-17, i): defdomain is fully opt-in — it is an organizing strategy, not a required element of using doeff, and is not enforced in the default gates (run()/CLI/default interpreter). Adopting projects wire the conformance checks as their own tests. doeff itself adopts it as dogfood within its own package scope (doeff-core-effects).")
     (rule R6 "Ruling (2026-07-17, ii): introduce 1 / include ∞ — one effect class may appear in the effects (introduction) of exactly one domain overall. If a second domain tries to introduce the same class, registration raises immediately, naming both domains. Any number of references through includes is allowed. The key is class identity.")
     (rule R7 "Ruling (2026-07-17, iii): the handled set is derived in two layers — (1) annotation layer: read, in order, the declaration values Handles(handler, effect …) placed in the domain's handlers, and the __doeff_handles__ mark the handler carries itself (added by core installers) (opt-in declarations for raw Python handlers; handlers of other packages can also be declared without attaching anything to them; this is not a guarantee of totality — matching against reality is SEDA's job in E2/E3; the shape before the change: the function handles() attached __doeff_handles__ to the handler after the fact — changed to the type Handles by R8 on 2026-10-29). (2) structural layer: the __doeff_body__ of what defhandler produces (a quoted clause list; detected by attribute duck typing without importing doeff-hy; cannot drift). Lazy-initialization clauses (the 3 heads defhandler recognizes: lazy / lazy-val / lazy-var) are skipped, and clauses containing :when guards or conditional reperform still count as 'declarations that take part in handling'. Name resolution tries, in order, the attribute on sys.modules[handler.__module__] → a string match against the effect class names of the domain being checked, and if both fail it is a check error (fail loud). A handler with neither a declaration nor either attribute is also a check error.")
     (rule R8 "The 8 functions that put into, look up, and check the table (register_domain, isolated_registry, get_domain, handles, handled_effects, assert_domain_covered, assert_registered_domains_covered, assert_no_orphan_effects) became the effects RegisterDomain, GetDomain, HandledEffects, AssertDomainCovered, AssertRegisteredDomainsCovered, AssertNoOrphanEffects plus domain_registry, a factory returning a handler that answers from the table, and the functions moved inside (doeff_domain._registry, _checks, _introspect) (decided on 2026-10-29 to make them effects; ADR-DOE-API-001 R29). domain_registry() reads the process table (where defdomain registers at import) when answering, and domain_registry(domains=[Domain …]) answers from a separate table holding only the given domains — it replaces isolated_registry; RegisterDomain also registers into that table, and a missing name raises KeyError without falling back to the process table. The one installed further inside answers first, so a test can run just part of a program against a separate table. The separate table is created once when the factory is called, and RegisterDomain registrations persist as long as the same handler value is reused (call the factory again if each test needs a fresh table). A check failure is a DomainError subclass (a module that cannot be imported during the scan is also wrapped in DomainCheckError — the original exception is __cause__), a name not in the table is KeyError, and both are thrown back to where the effect was raised. handles is neither a factory that returns a marked handler when called (ADR-DOE-API-001 R3) nor a helper that returns a program (R18), but a declaration at import time, so it became the declaration type Handles rather than an effect (the same path as R19's check — the type is R2). defdomain, the declaration types, and the exceptions stay exposed as now from the entry point and doeff_domain.macros.")]
  :laws
    [(law vocabulary-has-single-home
       :statement "for_all semantic_predicate_or_effect v: canonical_declaration_count(v) == 1 AND declared_in_some_domain(v); duplicate_definitions_detected_by_conformance_check"
       :counterexamples
         [(counterexample "ACP core #8: 7 definitions of the terminal predicate and 6 of terminal-set coexist, and the judgment diverges per call site (found by the 2026-07-02 audit; nothing had failed)")
          (counterexample "A new agent cannot discover the existing domain vocabulary and adds a synonymous effect type in another package — the default behavior when there is no query surface (SEDA)")])]
  :enforcement
    [(deftest test-adr-doe-domain-001-defdomain-provided-by-doeff-domain
       ;; R1, R8 green pin: doeff-domain's entry point exports the declaration types, the exceptions, the table effects, and the factory returning a handler, and does not export the former 8 functions.
       ;; The defdomain macro itself is used at import time by the dogfood declarations (the dogfood pin below), and is proven by GetDomain answering from that table.
       (import doeff_domain)
       (import doeff_domain [Domain DomainLaw DomainTerm Handles
                             RegisterDomain GetDomain HandledEffects AssertDomainCovered
                             AssertRegisteredDomainsCovered AssertNoOrphanEffects domain-registry])
       (import doeff_domain.macros)
       (import doeff [EffectBase])
       (for [effect [RegisterDomain GetDomain HandledEffects AssertDomainCovered
                     AssertRegisteredDomainsCovered AssertNoOrphanEffects]]
         (assert (issubclass effect EffectBase)))
       (assert (. (domain-registry) _doeff-is-handler-fn))
       (for [gone ["register_domain" "isolated_registry" "get_domain" "handles" "handled_effects"
                   "assert_domain_covered" "assert_registered_domains_covered" "assert_no_orphan_effects"]]
         (assert (not (hasattr doeff_domain gone)) gone)))
     (deftest test-adr-doe-domain-001-introduce-once-include-freely
       ;; R6/D3 green pin: a second introduction raises immediately at registration with both domain names (thrown back to where RegisterDomain was raised); includes are free.
       ;; The table is an empty separate table from domain-registry — it does not touch the process table (where defdomain registers) (R8).
       (import doeff_domain [Domain RegisterDomain domain-registry DuplicateEffectIntroductionError])
       (import doeff [EffectBase])
       (defclass Adr001PinEffect [EffectBase])
       (val table (domain-registry :domains []))
       (<- home (table (RegisterDomain (Domain :name "adr-001-pin-home" :title "pin: introducing domain"
                                               :effects [Adr001PinEffect]))))
       (<- failure (failure-of (table (RegisterDomain (Domain :name "adr-001-pin-second" :title "pin: double introduction"
                                                             :effects [Adr001PinEffect])))))
       (assert (isinstance failure DuplicateEffectIntroductionError))
       (assert (in "adr-001-pin-home" (str failure)))
       (assert (in "adr-001-pin-second" (str failure)))
       ;; Any number of references through includes is allowed
       (<- includer (table (RegisterDomain (Domain :name "adr-001-pin-includer" :title "pin: including is free"
                                                   :includes [home]))))
       (<- includer-2 (table (RegisterDomain (Domain :name "adr-001-pin-includer-2" :title "pin: including is free (2)"
                                                     :includes [home]))))
       (assert (= includer.includes #(home)))
       (assert (= includer-2.includes #(home))))
     (deftest test-adr-doe-domain-001-coverage-check-red-green
       ;; R3(a) green pin: a fixture that fails on a domain with missing coverage, and a fixture that passes when covered. The raw handler is declared with Handles (R7, R8).
       (import doeff_domain [Domain Handles AssertDomainCovered domain-registry DomainCoverageError])
       (import doeff [EffectBase])
       (defclass Adr001CoveredEffect [EffectBase])
       (defclass Adr001UncoveredEffect [EffectBase])
       (defn adr-001-pin-handler [body] body)
       (val declared (Handles adr-001-pin-handler Adr001CoveredEffect))
       (val red (Domain :name "adr-001-pin-red" :title "pin: missing coverage"
                         :effects [Adr001CoveredEffect Adr001UncoveredEffect]
                         :handlers [declared]))
       (<- failure (failure-of ((domain-registry :domains [red]) (AssertDomainCovered red))))
       (assert (isinstance failure DomainCoverageError))
       (assert (in "Adr001UncoveredEffect" (str failure)))
       (val green (Domain :name "adr-001-pin-green" :title "pin: covered"
                           :effects [Adr001CoveredEffect]
                           :handlers [declared]))
       (<- _ ((domain-registry :domains [green]) (AssertDomainCovered "adr-001-pin-green")))
       (assert (not (hasattr adr-001-pin-handler "__doeff_handles__"))))
     (deftest test-adr-doe-domain-001-orphan-check-red-green
       ;; R3(c) green pin: an unowned EffectBase descendant fails, naming its class name + defining module,
       ;; and passes once introduced (both against a separate table from domain-registry).
       (import doeff_domain [Domain AssertNoOrphanEffects domain-registry OrphanEffectError])
       (import doeff [EffectBase])
       (import sys types)
       (val mod-name "adr_doe_domain_001_orphan_pin")
       (val mod (types.ModuleType mod-name))
       (val OrphanPin (type "Adr001OrphanPinEffect" #(EffectBase)
                             {"__module__" mod-name}))
       (setv (. mod Adr001OrphanPinEffect) OrphanPin)
       (setv (get sys.modules mod-name) mod)
       (try
         (<- failure (failure-of ((domain-registry :domains []) (AssertNoOrphanEffects [mod-name]))))
         (assert (isinstance failure OrphanEffectError))
         (assert (in "Adr001OrphanPinEffect" (str failure)))
         (assert (in mod-name (str failure)))
         (val home (Domain :name "adr-001-pin-orphan-home" :title "pin: introducing the orphan"
                            :effects [OrphanPin]))
         (<- _ ((domain-registry :domains [home]) (AssertNoOrphanEffects [mod-name])))
         (finally
           (del (get sys.modules mod-name)))))
     (deftest test-adr-doe-domain-001-dogfood-core-effects-green
       ;; R5/D8 green pin: the dogfood declarations are registered into the process table by defdomain, domain-registry() answers GetDomain,
       ;; and (c) is green across all of doeff-core-effects.
       (import doeff_domain._core_effects_domains)
       (import doeff_domain [AssertNoOrphanEffects domain-registry])
       (val table (domain-registry))
       (<- found (table (read-domains ["doeff-reader" "doeff-state" "doeff-writer" "doeff-slog"
                                       "doeff-error" "doeff-scope" "doeff-listen" "doeff-await"
                                       "doeff-scheduler" "doeff-http" "doeff-memo" "doeff-cache"
                                       "doeff-outcome" "doeff-file" "doeff-http-server" "doeff-sql"
                                       "doeff-process" "doeff-warm-process" "doeff-process-exit" "doeff-channel"
                                       "doeff-compute" "doeff-stop-signal" "doeff-vm-probe"])))
       (assert (all (gfor domain found domain.title)))
       (<- _ (table (AssertNoOrphanEffects ["doeff_core_effects"]))))
     (defsemgrep domain-001-no-hy-adr-dependency
       "doeff-domain-no-hy-adr-dependency"
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/_introspect.py"
         "source" "import doeff_hy\n"}
        {"relative-path" "packages/doeff-domain/src/doeff_domain/macros.hy"
         "source" "(require doeff-hy.handle [defhandler])\n"}]
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/_introspect.py"
         "source" "import hy\n"}
        {"relative-path" "packages/doeff-domain/tests/test_domain_introspect.py"
         "source" "import doeff_hy\n"}])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
