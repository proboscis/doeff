;;; doeff-adr Hy macros.

(require doeff-hy.macros [deftest])

(import hy)
;; Entry point for pytest item records (the record shape is defined in doeff_hy/_pytest_items.py)
(import doeff-hy._pytest-items [record-function :as _record-test-function])

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
        ;; The deftest check is registered at import time under adr_registry as the effect RegisterDeftest.
        (.append emitted
          `(_doeff-adr-run
             ((_doeff-adr.adr-registry)
               (_doeff-adr.RegisterDeftest ~(hy.models.String (_symbol-text test-name))))))
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
  ;; The expansion runs registration at import time, and the check inside the generated test, as the effects RegisterSemgrep and AssertSemgrep under the handler
  ;; semgrep_enforcements — it does not depend on the project's doeff_interpreter fixture, so a defsemgrep in an outside repo
  ;; works just by bumping the doeff version. Names are read as _doeff-adr-run and _doeff-adr., which do not collide with names in the user's module.
  (setv semgrep-imports
    ['(import doeff [run :as _doeff-adr-run])
     '(import doeff-adr :as _doeff-adr)])
  (setv rule-id (hy.models.String (_symbol-text name)))
  (setv test-name (_test-symbol "test_" (+ (_symbol-text name) "_defsemgrep")))
  (setv item-record (_record-test-function test-name [] []))
  (if (and (>= (len forms) 3) (not (isinstance (get forms 0) hy.models.Keyword)))
      ;; Installed form: (defsemgrep name "rule-id" [hit …] [clean …] [:config "path"] [:expand-hy True])
      ;; :config absent → the nearest `.semgrep.yaml` above pytest's cwd (legacy).
      ;; :config relative → resolved against the directory of the file that
      ;; declares this defsemgrep, so the rule file can sit next to its ADR.
      ;; :expand-hy True → `.hy` fixtures are macro-expanded to Python before the
      ;; scan (doeff_adr._semgrep_hy), so `languages: [python]` rules read Hy code.
      (do
        (setv installed-rule-id (get forms 0))
        (setv hit-fixtures (get forms 1))
        (setv clean-fixtures (get forms 2))
        (setv options (_pairs-to-dict (cut forms 3 None)))
        (for [key options]
          (when (not-in key ["config" "expand-hy"])
            (raise (SyntaxError
                     (.format "defsemgrep {}: unknown option :{} (the installed form accepts :config and :expand-hy)"
                              name key)))))
        (setv config-forms
          (+ (if (in "config" options)
                 `[:config ~(get options "config") :declared-in __file__]
                 `[])
             (if (in "expand-hy" options)
                 `[:expand-hy ~(get options "expand-hy")]
                 `[])))
        `(do
           ~@semgrep-imports
           (_doeff-adr-run
             ((_doeff-adr.semgrep-enforcements)
               (_doeff-adr.RegisterSemgrep
                 ~rule-id
                 :rule-id ~installed-rule-id
                 :hit-fixtures ~hit-fixtures
                 :clean-fixtures ~clean-fixtures
                 ~@config-forms)))
           (defn ~test-name []
             (_doeff-adr-run ((_doeff-adr.semgrep-enforcements) (_doeff-adr.AssertSemgrep ~rule-id))))
           ~item-record))
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
           ~@semgrep-imports
           (_doeff-adr-run
             ((_doeff-adr.semgrep-enforcements)
               (_doeff-adr.RegisterSemgrep
                 ~rule-id
                 :pattern ~pattern
                 :config ~config
                 :languages ~languages
                 :message ~message
                 :severity ~severity
                 :bad ~bad
                 :good ~good
                 :mode ~mode)))
           (defn ~test-name []
             (_doeff-adr-run ((_doeff-adr.semgrep-enforcements) (_doeff-adr.AssertSemgrep ~rule-id))))
           ~item-record))))

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
  (setv item-record (_record-test-function test-name [] []))
  ;; Registration of deftest checks and verification of the ADR shape run as the effects RegisterDeftest and AssertAdrContract under the handler adr_registry
  ;; — like defsemgrep, they do not depend on the project's doeff_interpreter fixture, and names are read as _doeff-adr-run and _doeff-adr.,
  ;; which do not collide with names in the user's module. ADR registration also runs at import time as the effect RegisterAdr under the same handler.
  `(do
     (import doeff_adr._registry [enforcement-ref])
     (import doeff [run :as _doeff-adr-run])
     (import doeff-adr :as _doeff-adr)
     ~@emitted-enforcements
     (_doeff-adr-run
       ((_doeff-adr.adr-registry)
         (_doeff-adr.RegisterAdr
           ~adr-id
           :title ~title
           :status ~status
           :scope ~scope
           :problem ~problem
           :context ~context
           :decision ~decision
           :laws ~laws
           :enforcement [~@enforcement-refs]
           :plans ~plans)))
     (defn ~test-name []
       (_doeff-adr-run ((_doeff-adr.adr-registry) (_doeff-adr.AssertAdrContract ~adr-id))))
     ~item-record))

(defn #^ (get dict #(str object)) fact [#^ str text #^ object #** extra]
  "Build an ADR fact item in the registry shape (list it in defadr :facts)."
  (import doeff_adr._registry [make-fact])
  (make-fact text #** extra))

(defn #^ (get dict #(str object)) interpretation [#^ str text #^ object #** extra]
  "Build an ADR interpretation item in the registry shape (list it in defadr :interpretations)."
  (import doeff_adr._registry [make-interpretation])
  (make-interpretation text #** extra))

(defmacro rule [rule-id text #* forms]
  `(do
     (import doeff_adr._registry [make-rule])
     (make-rule ~(hy.models.String (str rule-id)) ~text ~@forms)))

(defn #^ (get dict #(str object)) counterexample [#^ str text #^ object #** extra]
  "Build an ADR counterexample item in the registry shape (list it in a law's :counterexamples)."
  (import doeff_adr._registry [make-counterexample])
  (make-counterexample text #** extra))

(defmacro law [law-id #* forms]
  (setv data (_pairs-to-dict forms))
  (setv extra-forms [])
  (for [idx (range 0 (len forms) 2)]
    (when (!= (_kw-text (get forms idx)) "statement")
      (.append extra-forms (get forms idx))
      (.append extra-forms (get forms (+ idx 1)))))
  `(do
     (import doeff_adr._registry [make-law])
     (make-law
       ~(hy.models.String (str law-id))
       ~(get data "statement")
       ~@extra-forms)))
