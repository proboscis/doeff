;;; Real defhandler products for doeff-domain derivation tests.
;;;
;;; Test-only module: tests MAY use doeff-hy to build genuine defhandler
;;; fixtures; the doeff-domain package sources must not import doeff-hy
;;; (ADR-DOE-DOMAIN-001 D1 — the two-layer derivation reads __doeff_body__
;;; by attribute duck-typing precisely to avoid that dependency).

(require doeff-hy.handle [defhandler])

(import domain_test_effects [FixtureAlpha FixtureBeta FixtureGamma FixtureDelta])


;; no-argument form — __doeff_body__ is attached to the handler value itself
(defhandler fixture-plain-handler
  (FixtureAlpha [] (resume 1))
  (FixtureBeta [] (resume 2)))


;; argument form — __doeff_body__ is attached to the factory function
(defhandler fixture-factory-handler [limit]
  (FixtureAlpha [] (resume limit))
  (FixtureBeta [] (resume (+ limit 1))))


;; with a lazy clause — lazy is skipped when deriving the handled set
(defhandler fixture-lazy-handler
  (lazy token (str "fixture-token"))
  (FixtureGamma [] (resume token)))


;; with a lazy-val clause — lazy clause heads are three kinds: lazy / lazy-val / lazy-var
;; (_is-lazy-clause in packages/doeff-hy/src/doeff_hy/handle.hy). All are skipped.
(defhandler fixture-lazy-val-handler
  (lazy-val token-val (str "fixture-token-val"))
  (FixtureAlpha [] (resume token-val)))


;; with a lazy-var clause
(defhandler fixture-lazy-var-handler
  (lazy-var token-var (str "fixture-token-var"))
  (FixtureBeta [] (resume token-var)))


;; with session val / session var clauses (ADR-DOE-HY-006) — declarations of session values, not handling declarations
(defhandler fixture-session-handler
  (session val token-session (str "fixture-token-session"))
  (session var count-session 0)
  (FixtureGamma [] (resume token-session)))


;; with a :when guard — guarded clauses also count as "declarations that participate in handling"
;; (not a totality guarantee — semantics of ADR-DOE-DOMAIN-001)
(defhandler fixture-guarded-handler [threshold]
  (FixtureAlpha [] :when (> threshold 0) (resume threshold))
  (FixtureDelta [] (resume None)))
