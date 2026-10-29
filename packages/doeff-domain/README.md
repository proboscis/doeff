# doeff-domain

Vocabulary cohesion domains for doeff (ADR-DOE-DOMAIN-001, E1).

A **domain** declares a cohesion unit of the effect system as pure data: the
effect classes it *introduces*, the vocabulary it *includes* by reference from
other domains, its canonical terms, the handlers expected to cover it, and its
laws. Declarations are registered into a process table at import time,
queried through effects, and checked by opt-in conformance checks — the target
reader is a writer agent, for whom documentation means a failing check.

```hy
(require doeff-domain.macros [defdomain])
(require doeff-hy.macros [<-])
(import doeff_domain [DomainTerm DomainLaw Handles
                      GetDomain AssertDomainCovered AssertNoOrphanEffects domain-registry])

(defdomain my-reader
  :title "Reader vocabulary"
  :effects [Ask]                       ;; real class references — introduction is unique per class
  :handlers [(Handles reader Ask)      ;; raw handlers are declared with the effects they answer
             my-defhandler-reader]     ;; defhandler products are read from their clauses
  :adrs ["ADR-DOE-DOMAIN-001"])

;; in a program (a test), under the table handler:
(<- domain (GetDomain "my-reader"))
(<- _ (AssertDomainCovered domain))
(<- _ (AssertNoOrphanEffects ["my_package"]))
```

The public surface is the declaration types, the effects on the domain table,
and the factory of the handler that answers them (ADR-DOE-API-001 R29):

| Name | What it is |
|---|---|
| `Domain`, `DomainTerm`, `DomainLaw`, `Handles` | the declaration as pure data |
| `RegisterDomain(domain)` → `Domain` | register into the table |
| `GetDomain(name)` → `Domain` | look a domain up by name (`KeyError` when it is not in the table) |
| `HandledEffects(handler, vocabulary=…)` → `frozenset[type]` | the effects a handler entry declares to handle |
| `AssertDomainCovered(domain_or_name, known_uncovered=…)` | check (a) for one domain |
| `AssertRegisteredDomainsCovered(known_uncovered=…)` | check (a) for every domain of the table |
| `AssertNoOrphanEffects(packages, known_orphans=…)` | check (c) |
| `domain_registry()` / `domain_registry(domains=[…])` | the handler: the process table, or a table of its own |
| `DomainError` and its children | what registration and the checks raise |
| `defdomain` (in `doeff_domain.macros`) | declare and register at import time |

Key semantics (adjudicated, binding):

- **Introduce once / include freely (D3).** An effect class may appear in the
  `effects` of exactly ONE registered domain, keyed by class identity.
  A second introduction raises at registration time, naming both domains.
  `includes` references are unlimited.
- **The table is swappable.** `domain_registry()` answers from the process
  table that `defdomain` registers into (read when an effect is answered);
  `domain_registry(domains=[…])` answers from a separate table holding only the
  given domains — `RegisterDomain` registers into it, and a name missing from
  it is a `KeyError`, not the outer table's answer. The inner handler answers
  first, so a test runs part of a program against another table. Failures are
  thrown back where the effect was performed.
- **Two-layer handled-effects derivation (D6).** A `Handles(handler, …)` entry
  declares the effects of a raw handler (the declaration lives in the domain;
  nothing is set on the handler); a handler carrying its own
  `__doeff_handles__` mark is read from it; otherwise the set is derived from
  `__doeff_body__` — the quoted clause list attached by doeff-hy's
  `defhandler` — by attribute duck-typing, WITHOUT importing doeff-hy.
  Session-value clauses are skipped; `:when`-guarded clauses count as
  participation declarations (not totality guarantees). Unresolvable clause
  heads fail loud.
- **Everything is opt-in (D7).** Nothing in doeff forces domains on users.
  Adopting projects perform the checks in their own tests:
  - `AssertDomainCovered(domain)` — union of the domain handlers' handled
    sets must cover the *introduced* effects (includes are the introducing
    domain's responsibility).
  - `AssertNoOrphanEffects(packages)` — every `EffectBase` subclass
    defined in the named packages must be introduced by some domain of the
    table.
  Both accept an explicit known-gap parameter with ratchet semantics (stale
  entries fail).

doeff dogfoods the checks over `doeff-core-effects` in the internal module
`doeff_domain._core_effects_domains` (requires the `dogfood` extra).

This package must not depend on doeff-hy or doeff-adr (enforced by semgrep
rule `doeff-domain-no-hy-adr-dependency`). Dependency on `doeff_vm.EffectBase`
and on `hy` (for the macro module) is allowed.
