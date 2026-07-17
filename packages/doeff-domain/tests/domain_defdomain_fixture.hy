;;; defdomain macro fixture — module-level declaration registers at import time.

(require doeff-domain.macros [defdomain])

(import domain_test_effects [FixtureGamma])
(import doeff_domain.registry [DomainLaw DomainTerm])


(defdomain fixture-macro-domain
  :title "defdomain macro fixture domain"
  :effects [FixtureGamma]
  :terms [(DomainTerm :name "fixture-term"
                      :home "domain_test_effects"
                      :description "canonical fixture term")]
  :laws [(DomainLaw :name "fixture-law"
                    :statement "for_all x: fixture(x)"
                    :counterexamples ["none — fixture"])]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "macro fixture for import-time registration tests")
