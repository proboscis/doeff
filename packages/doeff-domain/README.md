# doeff-domain

Vocabulary cohesion domains for doeff (ADR-DOE-DOMAIN-001, E1).

A **domain** declares a cohesion unit of the effect system as pure data: the
effect classes it *introduces*, the vocabulary it *includes* by reference from
other domains, its canonical terms, the handlers expected to cover it, and its
laws. Declarations are queryable from an in-process registry and checkable by
opt-in conformance checks — the target reader is a writer agent, for whom
documentation means a failing check.

```hy
(require doeff-domain.macros [defdomain])
(import doeff_domain [DomainTerm DomainLaw handles])

(defdomain my-reader
  :title "Reader vocabulary"
  :effects [Ask]                 ;; real class references — introduction is unique per class
  :handlers [reader lazy-ask]    ;; defhandler products or handles()-annotated callables
  :adrs ["ADR-DOE-DOMAIN-001"])
```

Key semantics (adjudicated, binding):

- **Introduce once / include freely (D3).** An effect class may appear in the
  `effects` of exactly ONE registered domain, keyed by class identity.
  A second introduction raises at registration time, naming both domains.
  `includes` references are unlimited.
- **Two-layer handled-effects derivation (D6).** `handled_effects(h)` reads
  `__doeff_handles__` (the `handles(...)` opt-in annotation) first; otherwise
  it derives the set from `__doeff_body__` — the quoted clause list attached
  by doeff-hy's `defhandler` — by attribute duck-typing, WITHOUT importing
  doeff-hy. `lazy` clauses are skipped; `:when`-guarded clauses count as
  participation declarations (not totality guarantees). Unresolvable clause
  heads fail loud.
- **Everything is opt-in (D7).** Nothing in doeff forces domains on users.
  Adopting projects wire the checks as their own tests:
  - `assert_domain_covered(domain)` — union of the domain handlers' handled
    sets must cover the *introduced* effects (includes are the introducing
    domain's responsibility).
  - `assert_no_orphan_effects(packages)` — every `EffectBase` subclass
    defined in the named packages must be introduced by some registered
    domain.
  Both accept an explicit known-gap parameter with ratchet semantics (stale
  entries fail).

doeff dogfoods the checks over `doeff-core-effects` in
`doeff_domain.core_effects_domains` (requires the `dogfood` extra).

This package must not depend on doeff-hy or doeff-adr (enforced by semgrep
rule `doeff-domain-no-hy-adr-dependency`). Dependency on `doeff_vm.EffectBase`
and on `hy` (for the macro module) is allowed.
