;;; doeff-adr Hy macros.

(require doeff-hy.macros [deftest])

(import hy)

(defn _kw-text [x]
  (setv text (str x))
  (if (.startswith text ":") (cut text 1 None) text))

(defn _symbol-text [x]
  (.replace (str x) "-" "_"))

(defn _test-symbol [prefix name]
  (hy.models.Symbol (+ prefix (_symbol-text name))))

(defn _pairs-to-dict [forms]
  (when (!= (% (len forms) 2) 0)
    (raise (SyntaxError "keyword arguments must come in key/value pairs")))
  (setv result {})
  (for [idx (range 0 (len forms) 2)]
    (setv (get result (_kw-text (get forms idx))) (get forms (+ idx 1))))
  result)

(defn _expr-head-text [form]
  (if (and (isinstance form hy.models.Expression) (> (len form) 0))
      (str (get form 0))
      None))

(defn _inline-enforcement-name [form]
  (if (and (isinstance form hy.models.Expression) (> (len form) 1))
      (get form 1)
      None))

(defn _normalize-enforcements [items]
  (setv emitted []
        refs [])
  (for [item items]
    (setv head (_expr-head-text item))
    (cond
      (= head "deftest")
      (do
        (.append emitted item)
        (setv test-name (_inline-enforcement-name item))
        (.append emitted
          `(register-deftest-enforcement
             ~(hy.models.String (_symbol-text test-name))))
        (.append refs
          `(enforcement-ref
             ~(hy.models.String (_symbol-text test-name))
             :kind "deftest")))
      (= head "defsemgrep")
      (do
        (.append emitted item)
        (setv rule-name (_inline-enforcement-name item))
        (.append refs
          `(enforcement-ref
             ~(hy.models.String (_symbol-text rule-name))
             :kind "defsemgrep")))
      True
      (.append refs item)))
  #(emitted refs))

(defmacro defsemgrep [name #* forms]
  "Define a Semgrep enforcement with bad/good fixtures and a pytest check."
  (setv rule-id (hy.models.String (_symbol-text name)))
  (setv test-name (_test-symbol "test_" (+ (_symbol-text name) "_defsemgrep")))
  (if (and (= (len forms) 3) (not (isinstance (get forms 0) hy.models.Keyword)))
      (do
        (setv installed-rule-id (get forms 0))
        (setv hit-fixtures (get forms 1))
        (setv clean-fixtures (get forms 2))
        `(do
           (import doeff_adr.registry
             [register-semgrep-enforcement assert-semgrep-enforcement])
           (register-semgrep-enforcement
             ~rule-id
             :rule-id ~installed-rule-id
             :hit-fixtures ~hit-fixtures
             :clean-fixtures ~clean-fixtures)
           (defn ~test-name []
             (assert-semgrep-enforcement ~rule-id))))
      (do
        (setv data (_pairs-to-dict forms))
        (setv pattern (get data "pattern"))
        (setv languages (.get data "languages" `["generic"]))
        (setv message (.get data "message" (hy.models.String "ADR Semgrep enforcement failed")))
        (setv severity (.get data "severity" (hy.models.String "ERROR")))
        (setv bad (.get data "bad" `[]))
        (setv good (.get data "good" `[]))
        (setv mode (.get data "mode" (hy.models.String "green")))
        (setv config (.get data "config" (hy.models.String ".semgrep.yaml")))
        `(do
           (import doeff_adr.registry
             [register-semgrep-enforcement assert-semgrep-enforcement])
           (register-semgrep-enforcement
             ~rule-id
             :pattern ~pattern
             :config ~config
             :languages ~languages
             :message ~message
             :severity ~severity
             :bad ~bad
             :good ~good
             :mode ~mode)
           (defn ~test-name []
             (assert-semgrep-enforcement ~rule-id))))))

(defmacro defadr [name #* forms]
  "Define an executable ADR contract.

  Inline enforcement forms inside :enforcement are emitted before the ADR is
  registered, so a single defadr can contain its runnable checks.
  "
  (setv data (_pairs-to-dict forms))
  (setv adr-id (hy.models.String (str name)))
  (setv enforcement-items (list (.get data "enforcement" [])))
  (setv #(emitted-enforcements enforcement-refs)
        (_normalize-enforcements enforcement-items))
  (setv title (get data "title"))
  (setv status (get data "status"))
  (setv scope (.get data "scope" `[]))
  (setv problem (.get data "problem" `[]))
  (setv context (.get data "context" `[]))
  (setv decision (.get data "decision" `[]))
  (setv laws (.get data "laws" `[]))
  (setv plans (.get data "plans" `[]))
  (setv test-name (_test-symbol "test_" (+ (_symbol-text name) "_adr_contract")))
  `(do
     (import doeff_adr.registry
       [register-adr assert-adr-contract enforcement-ref
        register-deftest-enforcement])
     ~@emitted-enforcements
     (register-adr
       ~adr-id
       :title ~title
       :status ~status
       :scope ~scope
       :problem ~problem
       :context ~context
       :decision ~decision
       :laws ~laws
       :enforcement [~@enforcement-refs]
       :plans ~plans)
     (defn ~test-name []
       (assert-adr-contract ~adr-id))))

(defn fact [text #** extra]
  (import doeff_adr.registry [make-fact])
  (make-fact text #** extra))

(defn interpretation [text #** extra]
  (import doeff_adr.registry [make-interpretation])
  (make-interpretation text #** extra))

(defmacro rule [rule-id text #* forms]
  `(do
     (import doeff_adr.registry [make-rule])
     (make-rule ~(hy.models.String (str rule-id)) ~text ~@forms)))

(defn counterexample [text #** extra]
  (import doeff_adr.registry [make-counterexample])
  (make-counterexample text #** extra))

(defmacro law [law-id #* forms]
  (setv data (_pairs-to-dict forms))
  (setv extra-forms [])
  (for [idx (range 0 (len forms) 2)]
    (when (!= (_kw-text (get forms idx)) "statement")
      (.append extra-forms (get forms idx))
      (.append extra-forms (get forms (+ idx 1)))))
  `(do
     (import doeff_adr.registry [make-law])
     (make-law
       ~(hy.models.String (str law-id))
       ~(get data "statement")
       ~@extra-forms)))
