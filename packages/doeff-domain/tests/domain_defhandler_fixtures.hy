;;; Real defhandler products for doeff-domain derivation tests.
;;;
;;; Test-only module: tests MAY use doeff-hy to build genuine defhandler
;;; fixtures; the doeff-domain package sources must not import doeff-hy
;;; (ADR-DOE-DOMAIN-001 D1 — the two-layer derivation reads __doeff_body__
;;; by attribute duck-typing precisely to avoid that dependency).

(require doeff-hy.handle [defhandler])

(import domain_test_effects [FixtureAlpha FixtureBeta FixtureGamma FixtureDelta])


;; 無引数形 — handler 値そのものに __doeff_body__ が付く
(defhandler fixture-plain-handler
  (FixtureAlpha [] (resume 1))
  (FixtureBeta [] (resume 2)))


;; 引数形 — factory 関数に __doeff_body__ が付く
(defhandler fixture-factory-handler [limit]
  (FixtureAlpha [] (resume limit))
  (FixtureBeta [] (resume (+ limit 1))))


;; lazy 節あり — lazy は処理集合の導出でスキップされる
(defhandler fixture-lazy-handler
  (lazy token (str "fixture-token"))
  (FixtureGamma [] (resume token)))


;; :when ガードあり — ガード付き節も「処理に参加する宣言」として数える
;; (全域性の保証ではない — ADR-DOE-DOMAIN-001 の意味論)
(defhandler fixture-guarded-handler [threshold]
  (FixtureAlpha [] :when (> threshold 0) (resume threshold))
  (FixtureDelta [] (resume None)))
