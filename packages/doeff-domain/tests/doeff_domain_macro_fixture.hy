(require doeff-domain.macros [defdomain])

(import doeff_domain.registry [DomainLaw DomainTerm])
(import doeff_vm [EffectBase])


(defclass MacroEffect [EffectBase])


(defdomain sample-domain
  :title "Sample vocabulary"
  :effects [MacroEffect]
  :terms [(DomainTerm :name "is-terminal"
                      :home "sample.predicates"
                      :description "Canonical terminal predicate")]
  :laws [(DomainLaw :name "single-home"
                    :statement "Every effect has one introducing domain"
                    :counterexamples ["the same class is introduced twice"])]
  :adrs ["ADR-SAMPLE-001"]
  :docs ["docs/sample.md"])
