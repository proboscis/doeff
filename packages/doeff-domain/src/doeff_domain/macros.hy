;;; Hy declaration macro for doeff vocabulary domains.

(import hy)


(setv _DOMAIN-FIELDS
  (frozenset ["title" "effects" "includes" "terms" "handlers" "laws" "adrs" "docs"]))


(defn _kw-text [value]
  (setv text (str value))
  (if (.startswith text ":") (cut text 1 None) text))


(defn _pairs-to-dict [forms]
  (when (!= (% (len forms) 2) 0)
    (raise (SyntaxError "defdomain keyword arguments must be key/value pairs")))
  (setv result {})
  (for [index (range 0 (len forms) 2)]
    (setv field-name (_kw-text (get forms index)))
    (when (not-in field-name _DOMAIN-FIELDS)
      (raise (SyntaxError (+ "defdomain does not support field :" field-name))))
    (setv (get result field-name) (get forms (+ index 1))))
  (when (not-in "title" result)
    (raise (SyntaxError "defdomain requires :title")))
  result)


(defmacro defdomain [name #* forms]
  "Build and register a Domain under the declared Hy symbol."
  (setv data (_pairs-to-dict forms))
  `(do
     (import doeff_domain.registry [Domain register-domain])
     (setv ~name
       (register-domain
         (Domain
           :name ~(hy.models.String (str name))
           :title ~(get data "title")
           :effects ~(.get data "effects" `[])
           :includes ~(.get data "includes" `[])
           :terms ~(.get data "terms" `[])
           :handlers ~(.get data "handlers" `[])
           :laws ~(.get data "laws" `[])
           :adrs ~(.get data "adrs" `[])
           :docs ~(.get data "docs" `[]))))))
