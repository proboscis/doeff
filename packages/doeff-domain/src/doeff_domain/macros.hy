;;; doeff-domain Hy macros — defdomain (ADR-DOE-DOMAIN-001 D4).
;;;
;;; defdomain is sugar over Domain construction + registry registration,
;;; provided as a Hy module inside doeff_domain (the same shape as doeff-adr
;;; providing defadr). This package must not depend on doeff-hy (D1); plain
;;; hy is sufficient for macro modules.

(import hy)


(defn _kw-text [x]
  (setv text (str x))
  (if (.startswith text ":") (cut text 1 None) text))


(defn _pairs-to-dict [forms]
  (when (!= (% (len forms) 2) 0)
    (raise (SyntaxError "defdomain keyword arguments must come in key/value pairs")))
  (setv result {})
  (for [idx (range 0 (len forms) 2)]
    (setv (get result (_kw-text (get forms idx))) (get forms (+ idx 1))))
  result)


(setv _DEFDOMAIN-KEYS
  #{"title" "effects" "includes" "terms" "handlers" "laws" "adrs" "docs"})


(defmacro defdomain [name #* forms]
  "Declare a vocabulary cohesion domain and register it in the process registry.

   (defdomain doeff-reader
     :title \"Reader vocabulary\"
     :effects [Ask]
     :includes []
     :terms [(DomainTerm :name \"ask\" :home \"pkg.effects\" :description \"...\")]
     :handlers [reader lazy-ask]
     :laws [(DomainLaw :name \"...\" :statement \"...\")]
     :adrs [\"ADR-DOE-DOMAIN-001\"]
     :docs \"...\")

   Binds NAME to the registered Domain value. Registration happens at import
   time of the declaring module; same-name re-registration (D2) and a second
   introduction of an effect class (D3) raise immediately."
  (setv data (_pairs-to-dict forms))
  (setv unknown (sorted (lfor key (.keys data) :if (not-in key _DEFDOMAIN-KEYS) key)))
  (when unknown
    (raise (SyntaxError (+ "defdomain: unknown key(s) " (str unknown)
                           " — expected one of " (str (sorted _DEFDOMAIN-KEYS))))))
  (when (not-in "title" data)
    (raise (SyntaxError "defdomain requires :title")))
  `(do
     (import doeff_domain.registry :as _doeff-domain-registry)
     (setv ~name
       (_doeff-domain-registry.register-domain
         (_doeff-domain-registry.Domain
           :name ~(hy.models.String (str name))
           :title ~(get data "title")
           :effects ~(.get data "effects" '[])
           :includes ~(.get data "includes" '[])
           :terms ~(.get data "terms" '[])
           :handlers ~(.get data "handlers" '[])
           :laws ~(.get data "laws" '[])
           :adrs ~(.get data "adrs" '[])
           :docs ~(.get data "docs" ""))))))
