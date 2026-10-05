;;; Koka-style pattern-matching effect handlers for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.handle [handle defhandler])
;;;   ;; No extra imports needed — macros inject their own runtime deps.
;;;
;;; Macros:
;;;   (handle body (Effect [fields] body...) ...)             — inline handler
;;;   (defhandler name [params?] ["docstring"] (Effect [fields] body...) ...)
;;;                                                            — named handler
;;;   (with-handler [handler ...] body)                       — handler stack syntax
;;;
;;; Handler clause operations (terminal — handler gives up control):
;;;   (resume value)        — Resume k with value, handler stays installed
;;;   (transfer value)      — Resume k with value, handler removed (tail-call)
;;;   (finish value)        — Drop k; the handled scope answers value (ADR-DOE-CORE-EFFECTS-003 R15).
;;;                           Raise / Absent clauses finish freely; an ordinary effect's clause
;;;                           needs :finish-reason "…" (e.g. a deadline that stops the whole run)
;;;   (reperform effect)    — Forward effect+k to outer handler (OCaml 5 reperform)
;;;   (pass)                — DEPRECATED: use (reperform effect)
;;;
;;; Handler clause operations (non-terminal — handler keeps control):
;;;   (<- result effect)    — Delegate effect to outer handler, bind result, continue
;;;                           The handler pauses, outer handler resolves, handler resumes.
;;;
;;; Key distinction:
;;;   (reperform effect)    → terminal: "I can't handle this, pass it up" → handler done
;;;   (<- x (SomeEff ...))  → non-terminal: "ask outer, get result back, keep going"
;;;
;;; Clause guards:
;;;   (EffectType [fields] :when pred body...)  — auto-reperform if pred is false
;;;
;;; Checks (ADR-DOE-CORE-EFFECTS-003 R15 — a forgotten resume never ends a scope silently):
;;;   - Every clause body must reach resume/transfer/finish/reperform/raise on ALL branches
;;;     (each if / cond / match / try branch; cond ends with True, match with a bare _) →
;;;     SyntaxError at macro expansion
;;;   - A clause's ending must match its effect's declared resumption (Resumption): no resume of
;;;     Raise / Absent, no finish of an ordinary effect without :finish-reason — by name at
;;;     expansion, by the effect type when the handler first wraps a body (ClauseEndingError)
;;;   - A clause that leaves without resume / finish at run time → RuntimeError
;;;
;;; S-expr preservation:
;;;   - defhandler stores __doeff_body__ for introspection (like defk)

(import hy.models [Expression Symbol List Keyword Sequence String])
(import doeff-hy.positions [locate-synthesized])
(import doeff-hy.declarations [DECLARATION-KEYS refuse-unknown-keys declaration-setters])
(import hy.models [Dict])
(import doeff-hy.static-view [static-view-enabled report-findings])
(import doeff-hy.binding-forms [parse-declaration Timing Mutability SessionName BodyKind
                                legacy-session-finding legacy-session-message])

(defn _do-import []
  "handler の本体を包む `_doeff-do` の import。型検査のための展開(doeff_hy/static_view.py)
   では型付きの doeff_hy.static_types.do を取る — 同じ module の defk と同じ名前に 2 つの
   型が付くと、pyright から見た `_doeff_do` が両者の union になり誤検出を出すため
   (macros.hy の `_helper-imports` と同じ決め)。"
  (if (static-view-enabled)
      ;; 型付きの do は doeff-hy-check が module の頭に 1 度だけ import する(static_view.py の STATIC_HELPER_IMPORTS —
      ;; 節ごとに出すと 1 つの名の宣言が 64 を超えた module で pyright が Unknown にする・agora-redesign #1686)。
      '(do)
      `(import doeff.do [do :as _doeff-do])))


(defn _clause-endings-import [name alias]
  "節の終わり方の検め(clause_endings.hy の name)を alias で引く import。型検査のための展開では、型付きの
   doeff_hy.static_types の同じ名を doeff-hy-check が module の頭に 1 度だけ import する(static_view.py の
   HANDLER_STATIC_NAMES — clause_endings は .hy なので pyright から型が見えない・agora-redesign #2279)。"
  (if (static-view-enabled)
      '(do)
      `(import doeff-hy.clause-endings [~name :as ~alias])))


(defn _vm-imports []
  "handler の展開が使う doeff の node の import。型検査のための展開の resume / transfer は typed-resume /
   typed-transfer になり Resume / Transfer を使わないので、Pass と WithHandler だけを出す(使わない import は strict の
   赤 — agora-redesign #2279)。"
  (if (static-view-enabled)
      '(do (import doeff [Pass])
           (import doeff_vm [WithHandler]))
      '(do (import doeff [Resume Transfer Pass])
           (import doeff_vm [WithHandler]))))


(defn _handler-fn-form [endings-check]
  "handler を本文に被せる関数 __doeff-handler-fn__ の定義。型検査のための展開では、本文を
   `HandlerBody[HandledAnswer]`、答えを `HandledScope[HandledAnswer]`(本文の答えの型をそのまま運ぶ)と注記する
   (doeff_hy/static_types.pyi・agora-redesign #2279)。実行時の展開は注記を付けない。"
  (if (static-view-enabled)
      `(defn #^ (get _doeff_HandledScope _doeff_HandledAnswer) __doeff-handler-fn__
             [#^ (get _doeff_HandlerBody _doeff_HandledAnswer) __doeff-body__]
         ~endings-check
         (WithHandler __doeff-handler-data__ __doeff-body__))
      `(defn __doeff-handler-fn__ [__doeff-body__]
         ~endings-check
         (WithHandler __doeff-handler-data__ __doeff-body__))))


;; ---------------------------------------------------------------------------
;; Lazy clause support — per-session effectful lazy init via Get/Put + Some
;; ---------------------------------------------------------------------------

(defn _is-lazy-clause [form]
  "Check if form is (lazy name body...) or (lazy-val name body...) or (lazy-var name body...)."
  (and (isinstance form Expression)
       (>= (len form) 3)
       (isinstance (get form 0) Symbol)
       (in (str (get form 0)) #{"lazy" "lazy-val" "lazy-var"})))

(defn _lazy-mutability [form]
  "Return :var for lazy-var, :val for lazy/lazy-val."
  (if (= (str (get form 0)) "lazy-var") :var :val))

(defn _parse-lazy [form]
  "Parse (lazy[-val|-var] name body...) → #(name-sym body-forms mutability legacy?)."
  (assert (_is-lazy-clause form))
  #((get form 1) (list (cut form 2 None)) (_lazy-mutability form) True))

(defn _extract-lazy-clauses [clauses [handler-name None]]
  "Split clauses into (lazy-defs, effect-clauses).
   lazy-defs: list of #(name-sym body-forms mutability legacy?) — session val / session var
   (ADR-DOE-HY-006)と旧い lazy / lazy-val / lazy-var の両方。旧い形は動きを変えず、
   DeprecationWarning と doeff-hy-check の所見で session val / session var への移行を案内する。
   effect-clauses: remaining clauses."
  (setv lazys []
        effects [])
  (for [c clauses]
    (setv decl (parse-declaration c))
    (cond
      (and (is-not decl None) (= (. decl timing) Timing.SESSION))
        (.append lazys #((. decl symbol) [(. decl init)]
                         (if (= (. decl mutability) Mutability.VAR) :var :val)
                         False))
      (is-not decl None)
        (raise (SyntaxError (+ "\ndefhandler " (str handler-name) ": (" (. decl spelled) " "
                               (. decl name) " …) は節の本体の中に書きます(その節の 1 回の実行の間の値)。\n"
                               "  handler の直下(節と並べる所)に置くのは、セッションの間で共有する "
                               "(session val " (. decl name) " 式) / (session var " (. decl name) " 式) です。"
                               " [ADR-DOE-HY-006]\n")))
      (_is-lazy-clause c)
        (do
          (import warnings)
          (warnings.warn (legacy-session-message c (str handler-name)) DeprecationWarning
                         :stacklevel 3)
          (report-findings [(legacy-session-finding c (str handler-name))])
          (.append lazys (_parse-lazy c)))
      True
        (.append effects c)))
  #(lazys effects))

(defn _session-names [lazy-defs]
  "節の本体の書き換え(binding_forms)へ渡す、セッションに持つ名前の一覧。"
  (lfor #(lname _ mut legacy?) lazy-defs
    (SessionName (str lname) (if (= mut :var) Mutability.VAR Mutability.VAL) legacy?)))

(defn _is-set-bang [form]
  "Check if form is (set! name expr)."
  (and (isinstance form Expression)
       (= (len form) 3)
       (isinstance (get form 0) Symbol)
       (= (str (get form 0)) "set!")))

(defn _check-set-bang-violations [lazy-defs clause-forms]
  "Raise SyntaxError if set! is used on a lazy-val name."
  (setv val-names
    (set (gfor #(lname _ mut _legacy) lazy-defs :if (= mut :val) (str lname))))
  (defn walk [form]
    (when (isinstance form Expression)
      (when (_is-set-bang form)
        (setv target (str (get form 1)))
        (when (in target val-names)
          (raise (SyntaxError
            (.format "set! on lazy-val '{name}': lazy-val is immutable. Use lazy-var instead."
                     :name target)))))
      (for [child form]
        (walk child))))
  (for [f clause-forms]
    (walk f)))

(defn _fresh-lazy-tmp [lazy-name suffix]
  "lazy の初期化の一時の名を作るため。Hy の gensym で作る — 名の番号は compile の口の正準化
   (doeff_hy_bytecode_guard.records.canonical_gensyms)が module の中の順へ振り直すので、compile の順に依らない
   (自前の数えを持つと、その番号が compile の順で変わっていた — agora-redesign #3667)。"
  (hy.gensym (+ "lazy_" (str lazy-name) "_" suffix)))

(defn _references-symbol [form sym-name]
  "Check if form's AST contains a Symbol with the given name.
   Traverses any hy.models.Sequence (Expression, List, Tuple, Set, Dict,
   FString, FComponent) so references inside tuple/set/dict literals are
   detected — required for lazy init injection when the lazy name appears
   only inside a tuple constructor like ``#(lazy-name other)``."
  (cond
    (isinstance form Symbol) (= (str form) sym-name)
    (isinstance form Sequence)
      (any (gfor f form (_references-symbol f sym-name)))
    True False))

(defn _build-lazy-init-forms [handler-name lazy-name lazy-body [legacy? True]]
  "Build the yield-based lazy init code for one lazy def.
   Returns list of Hy forms (already in yield IR — no <- needed).

   Generated pattern:
     (setv _cached (yield (Get key)))
     (if (isinstance _cached Some)
         (setv name (. _cached value))
         (do ...init-body...
             (setv _val last-form)
             (yield (Put key (Some _val)))
             (setv name _val)))"
  (import doeff-hy.macros [_expand-bangs _is-bind _bind-parts])

  ;; Expand <- and ! in lazy body
  (setv expanded-body
    (_expand-handler-binds lazy-body
                           (+ "lazy " (str handler-name) "/" (str lazy-name))))

  ;; Separate init steps from value expression
  (setv init-forms (list (cut expanded-body 0 -1)))
  (setv value-expr (get expanded-body -1))

  ;; Generate temp vars
  (setv cached-var (_fresh-lazy-tmp lazy-name "cached"))
  (setv val-var (_fresh-lazy-tmp lazy-name "val"))

  ;; Build state key: __name__ + "/handler-name/lazy-name"
  ;; 旧い lazy は今までの式のまま。session val / session var は公開の session-key 1 点でキーを作る
  ;; (同じ文字列 — 外の handler と検査も doeff_hy.session.session_key で同じキーを引く)。
  (setv key-suffix (+ "/" (str handler-name) "/" (str lazy-name)))
  (setv key-expr
    (if legacy?
        `(+ __name__ ~key-suffix)
        `(_doeff-session-key __name__ ~(str handler-name) ~(str lazy-name))))

  ;; Key variable for set! macro to reference
  (setv key-var (Symbol (+ "_lazy_" (str lazy-name) "_key")))

  ;; Build the init forms
  (setv else-body
    (+ init-forms
       [`(setv ~val-var ~value-expr)
        `(yield (Put ~key-var (Some ~val-var)))
        `(setv ~(Symbol (str lazy-name)) ~val-var)]))

  ;; Build full lazy init sequence
  ;; 型検査のための展開(doeff_hy/static_view.py — 走らせず pyright が読むだけ・agora-redesign #2293): 名は初期化の式の値に
  ;; 束ねる。実行時は Get の答え(Some の中身)か初期化の値のどちらかだが、状態に置くのは同じ初期化の値なので型は同じ —
  ;; Get の答えは型を持たないので、そのまま写すと名とそれを使う式が全部 Unknown になり、書き手に直せない赤が出ていた。
  ;; キーの名は := の Put が引くので残す。
  (if (static-view-enabled)
      (+ [`(setv ~key-var ~key-expr)]
         init-forms
         [`(setv ~val-var ~value-expr)
          `(setv ~(Symbol (str lazy-name)) ~val-var)])
      [`(setv ~key-var ~key-expr)
       `(setv ~cached-var (yield (Get ~key-var)))
       `(if (isinstance ~cached-var Some)
            (setv ~(Symbol (str lazy-name)) (. ~cached-var value))
            (do ~@else-body))]))


;; ---------------------------------------------------------------------------
;; Termination analysis — every code path must hit resume/transfer/pass/raise
;; ---------------------------------------------------------------------------

(defn _sym-name [form]
  "Get symbol name as string, or None."
  (if (isinstance form Symbol) (str form) None))

(defn _head-name [form]
  "Get head symbol name of an Expression, or None."
  (when (and (isinstance form Expression) (> (len form) 0))
    (_sym-name (get form 0))))

(defn _terminates [form]
  "Check if a single form always reaches resume/transfer/finish/reperform/raise — on every path
   (each if / cond / match / try branch; when / unless / and / or / loops never guarantee it)."
  (setv head (_head-name form))
  (cond
    (is head None) False

    ;; Direct terminals
    (in head ["resume" "transfer" "finish" "pass" "reperform" "raise"]) True

    ;; (if test then else) — both branches must terminate
    (= head "if")
      (and (>= (len form) 4)
           (_terminates (get form 2))
           (_terminates (get form 3)))

    ;; (cond test1 body1 test2 body2 ...) — all bodies must terminate and the last test is True
    ;; (otherwise a value that matches no test falls through)
    (= head "cond")
      (let [pairs (cut form 1 None)
            tests (cut pairs 0 None 2)
            bodies (cut pairs 1 None 2)]
        (and (> (len bodies) 0)
             (= (len tests) (len bodies))
             (in (str (get tests -1)) #{"True" "else" ":else"})
             (all (gfor b bodies (_terminates b)))))

    ;; (match subject pattern body ...) — every arm terminates and the last arm is irrefutable
    (= head "match")
      (do (import doeff-hy.clause-endings [match-terminates?])
          (match-terminates? form))

    ;; (do form1 form2 ...) — sequence terminates if any form terminates
    (= head "do")
      (_seq-terminates (cut form 1 None))

    ;; (let [...] body...) / (with [...] body...) — check body forms
    (in head ["let" "with"])
      (and (>= (len form) 3)
           (_seq-terminates (cut form 2 None)))

    ;; (try body (except ...)) — the body and every except clause terminate
    (= head "try")
      (do (import doeff-hy.clause-endings [try-terminates?])
          (try-terminates? form))

    ;; One-branch and short-circuit forms and loops never guarantee termination; a resume inside a
    ;; nested function or comprehension does not run as the clause's step at all
    (in head ["when" "unless" "and" "or" "for" "while"
              "fn" "fn/a" "defn" "defn/a" "lfor" "gfor" "dfor" "sfor" "quote" "quasiquote"]) False

    ;; Anything else: check if it recursively contains a terminal
    True (any (gfor f (cut form 1 None) (_terminates f)))))

(defn _seq-terminates [forms]
  "Check if a sequence of forms terminates (any single form terminates)."
  (any (gfor f forms (_terminates f))))

(defn _check-clause-terminates [etype-str clause-body]
  "Raise SyntaxError if clause body doesn't terminate on all branches."
  (when (not (_seq-terminates clause-body))
    (raise (SyntaxError
      (+ "handle clause for " etype-str
         ": missing resume/transfer/finish/reperform on some branch — every if / cond / match / try"
         " branch must end the clause (cond needs a final True, match a final _); a clause that should"
         " end its scope without resuming says so with (finish value) [ADR-DOE-CORE-EFFECTS-003 R15]")))))


;; ---------------------------------------------------------------------------
;; TCO: tail-position (resume expr) → (transfer expr)
;; ---------------------------------------------------------------------------

(defn _match-cases-to-transfer [form]
  "Rewrite the tail resume of every arm body of (match subject pattern [:if guard] body ...).
   The arms are split by clause_endings' match-arms — the same split the clause-ending check
   uses, so the two never disagree on where a body is. Only bodies are in tail position
   (patterns and guards are left as written); a malformed match is left untouched."
  (import doeff-hy.clause-endings [match-arms])
  (setv arms (match-arms form))
  (if (is arms None)
      form
      (Expression
        (+ [(get form 0) (get form 1)]
           (lfor arm arms
                 part (+ [arm.pattern]
                         (if (is arm.guard None) [] [(Keyword "if") arm.guard])
                         [(_tail-resume-to-transfer arm.body)])
                 part)))))


(defn _tail-resume-to-transfer [form]
  "Replace tail-position (resume expr) with (transfer expr).
   Recurses into if/cond/match/do/let but NOT into try (need frame for except)."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [hname (_sym-name (get form 0))]
        (cond
          ;; (resume expr) → (transfer expr)
          (and (= hname "resume") (= (len form) 2))
            (Expression [(Symbol "transfer") (get form 1)])

          ;; (if test then else) → optimize both branches
          (and (= hname "if") (>= (len form) 4))
            (Expression [(get form 0) (get form 1)
                         (_tail-resume-to-transfer (get form 2))
                         (_tail-resume-to-transfer (get form 3))])

          ;; (cond p1 b1 p2 b2 ...) → optimize body (odd-index) forms
          (= hname "cond")
            (let [items (list (cut form 1 None))
                  result [(get form 0)]]
              (for [#(i item) (enumerate items)]
                (.append result
                  (if (% i 2)
                      (_tail-resume-to-transfer item)
                      item)))
              (Expression result))

          ;; (do ...) → optimize last form
          (and (= hname "do") (> (len form) 1))
            (Expression
              (+ (list (cut form 0 -1))
                 [(_tail-resume-to-transfer (get form -1))]))

          ;; (let [...] body...) → optimize last body form
          (and (= hname "let") (>= (len form) 3))
            (Expression
              (+ (list (cut form 0 -1))
                 [(_tail-resume-to-transfer (get form -1))]))

          ;; (match subject pattern [:as name] [:if guard] body ...) → optimize each case's body.
          ;; Without this a resume at the end of a match branch stayed a Resume: the handler's
          ;; generator frame was kept (holding the resumed value) for as long as the handled program
          ;; ran — a long-running loop under the handler piled one frame per handled effect
          ;; (agora-redesign #3530: an intake reader held every page it read).
          (and (= hname "match") (>= (len form) 4))
            (_match-cases-to-transfer form)

          ;; (try ...) → do NOT optimize (need frame for except)
          (= hname "try") form

          ;; Anything else → don't touch
          True form))))

(defn _tco-seq [forms]
  "Apply tail-resume TCO to the last form in a sequence."
  (if (= (len forms) 0)
      forms
      (+ (list (cut forms 0 -1))
         [(_tail-resume-to-transfer (get forms -1))])))


;; ---------------------------------------------------------------------------
;; Rewriting: resume/transfer/pass → yield expressions
;; ---------------------------------------------------------------------------

(defn _rewrite-ops [form]
  "Recursively rewrite resume/transfer/finish/pass/reperform in handler clause body.
   (resume expr)      → (yield (Resume k expr))  — and marks the clause as resumed
   (transfer expr)    → (yield (Transfer k expr))
   (finish expr)      → (return expr)  — drop the continuation, the handled scope answers expr
   (reperform effect) → (yield (Pass effect k))     — OCaml 5 aligned
   (pass)             → (yield (Pass effect k))      — deprecated, use reperform"
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [head (get form 0)
            hname (_sym-name head)]
        (cond
          ;; 型検査のための展開(doeff_hy/static_view.py): core の typed_resume / typed_transfer
          ;; で、答えの値を effect の答えの型(EffectBase[T] の T)と突き合わせる。`effect` は
          ;; 節の `(isinstance effect EffectType)` で絞られている。実行時の展開は Resume / Transfer。
          ;; 非末尾の resume は続きが終わると節へ戻る — 戻った後で「再開した」と記す(値の式や続きで出た例外を節が
          ;; 握りつぶして抜けた道は印が付かず、実行の時に誤りになる・ADR-DOE-CORE-EFFECTS-003 R15)。
          (and (= hname "resume") (= (len form) 2) (static-view-enabled))
            `(do (setv _doeff_resumed_value
                       (yield (do (import doeff [typed-resume])
                                  (typed-resume effect k ~(_rewrite-ops (get form 1))))))
                 (setv _doeff_clause_resumed True)
                 _doeff_resumed_value)

          (and (= hname "transfer") (= (len form) 2) (static-view-enabled))
            `(yield (do (import doeff [typed-transfer])
                        (typed-transfer effect k ~(_rewrite-ops (get form 1)))))

          (and (= hname "resume") (= (len form) 2))
            `(do (setv _doeff_resumed_value (yield (Resume k ~(_rewrite-ops (get form 1)))))
                 (setv _doeff_clause_resumed True)
                 _doeff_resumed_value)

          ;; (finish value) — 続きを捨て、handler を置いたスコープの答えを value にする(再開しない終わり方)。
          (and (= hname "finish") (= (len form) 2))
            `(return ~(_rewrite-ops (get form 1)))

          (and (= hname "transfer") (= (len form) 2))
            `(yield (Transfer k ~(_rewrite-ops (get form 1))))

          ;; (reperform expr) — OCaml 5 aligned forwarding
          (and (= hname "reperform") (= (len form) 2))
            `(yield (Pass ~(_rewrite-ops (get form 1)) k))

          ;; (pass) — deprecated, use (reperform effect) instead
          (and (= hname "pass") (= (len form) 1))
            (do
              (import warnings)
              (warnings.warn
                "(pass) is deprecated in defhandler — use (reperform effect) instead"
                DeprecationWarning)
              '(yield (Pass effect k)))

          True
            (Expression (lfor f form (_rewrite-ops f)))))))


;; ---------------------------------------------------------------------------
;; Clause parsing & handler construction
;; ---------------------------------------------------------------------------

(defn _parse-guard [body-forms]
  "Extract :when guard from body forms if present.
   Returns #(guard-expr remaining-body) or #(None body-forms)."
  (if (and (>= (len body-forms) 2)
           (isinstance (get body-forms 0) Keyword)
           (= (str (get body-forms 0)) ":when"))
      #((get body-forms 1) (list (cut body-forms 2 None)))
      #(None (list body-forms))))

(defn _expand-handler-binds [forms [owner "handler clause"]]
  "Expand <- and ! in handler clause body.
   (<- name expr) → (setv name <open-bind の束ね>)  — delegate to outer handler(outcome-forms の open-form)
   (<- name Type expr) → same + isinstance assert (macros.hy _bind-yield —
   the single definition point shared with <- / do! / defp / deftest / for/do)
   (! expr) → (yield expr) in place [ADR-DOE-HY-003]"
  (import doeff-hy.macros [_is-bind _expand-bangs])
  (import doeff-hy.outcome-forms [bind-form :as _bind-form])

  (setv expanded [])
  (for [form forms]
    (setv rewritten (_expand-bangs form owner))
    (if (_is-bind rewritten)
        (.append expanded (_bind-form rewritten))
        (.append expanded rewritten)))
  expanded)


(defn _build-clause [clause [lazy-defs None] [handler-name None] [module-names None] [specs None]]
  "Parse one handler clause: (EffectType [fields] [:when guard] [:finish-reason \"…\"] body...).
   Validates termination and the clause's ending against the effect (ADR-DOE-CORE-EFFECTS-003 R15).
   Returns #(effect-type cond-body). When `specs` is a list, appends the clause's ending
   description for the definition-time check (clause_endings.check-clause-endings).
   If lazy-defs is provided, inject lazy init for referenced lazy names."
  (import doeff-hy.clause-endings [parse-clause-options clause-ops check-clause-by-name ending-spec-form])
  (assert (isinstance clause Expression)
          "handle clause must be an expression")
  (assert (>= (len clause) 3)
          "handle clause needs (EffectType [fields] body...)")

  (setv etype (get clause 0))
  (setv fields (get clause 1))
  (setv raw-body (list (cut clause 2 None)))
  (setv where (if (is handler-name None)
                  (+ "handle clause " (str etype))
                  (+ "defhandler " (str handler-name) " clause " (str etype))))

  (assert (isinstance fields List)
          "handle clause fields must be [field1 ...]")

  ;; Extract :when guard and :finish-reason
  (setv options (parse-clause-options raw-body where))
  (setv guard (. options guard))
  (setv cbody (. options body))
  (setv cbody-written cbody)

  ;; Termination check BEFORE rewriting (on original body)
  (_check-clause-terminates (str etype) cbody)

  ;; The clause's ending against what the effect declares — by name at expansion (Raise / Absent,
  ;; finish without a reason), by the effect type's declaration when the handler is defined.
  (setv ops (clause-ops cbody))
  (check-clause-by-name etype ops options where)
  (when (is-not specs None)
    (.append specs (ending-spec-form etype ops options)))

  ;; TCO: tail-position (resume expr) → (transfer expr)
  ;; Must run BEFORE _expand-handler-binds and _rewrite-ops so it sees
  ;; the original (resume ...) forms, not the rewritten (yield (Resume ...)).
  (setv cbody (_tco-seq cbody))

  ;; val / var / lazy val / lazy var / := の書き換え(ADR-DOE-HY-006)。節の欄・effect・k と
  ;; handler の直下のセッションの名前は、本体の前から束縛されている名前。
  (import doeff-hy.macros [_rewrite-bindings])
  (setv cbody
    (_rewrite-bindings cbody
                       (if (is handler-name None)
                           (+ "handle clause " (str etype))
                           (+ "defhandler " (str handler-name) " clause " (str etype)))
                       BodyKind.CLAUSE
                       (+ (lfor f fields (str f)) ["effect" "k"])
                       (_session-names (or lazy-defs []))
                       :module module-names))

  ;; Expand <- and ! bindings → (setv name (yield expr))
  (setv cbody
    (_expand-handler-binds cbody
                           (if (is handler-name None)
                               (+ "handler clause " (str etype))
                               (+ "defhandler " (str handler-name)
                                  " clause " (str etype)))))

  ;; Check set! violations on lazy-val names
  (when lazy-defs
    (_check-set-bang-violations lazy-defs raw-body))

  ;; Inject lazy init for referenced lazy names (after bind expansion,
  ;; before rewrite-ops — lazy init forms are already in yield IR)
  ;; session の値(session val / var・旧い lazy-val / lazy-var)の取り出しは、その名前を使う所の前に置く:
  ;; :when の番が読む名前は番の前(guard-prefix)、本体だけが読む名前は番が通った後(lazy-prefix)。
  ;; 番も「使う所」なので、番で初めて使えばそこで作る(lazy の意味のまま)。番の前に置かないと番の中の名前は
  ;; 未定義(節の関数の局所変数 = UnboundLocalError)だった。番が読まない名前は今までどおり番が外れた効果では作らない。
  (setv guard-prefix [])
  (setv lazy-prefix [])
  (when (and lazy-defs handler-name)
    (for [#(lname lbody _mut legacy?) lazy-defs]
      ;; Symbol scan: only inject if clause body references this lazy name
      (cond
        (and (is-not guard None) (_references-symbol guard (str lname)))
          (.extend guard-prefix
            (_build-lazy-init-forms handler-name lname lbody legacy?))
        (any (gfor form cbody-written (_references-symbol form (str lname))))
          (.extend lazy-prefix
            (_build-lazy-init-forms handler-name lname lbody legacy?)))))

  ;; Field bindings: (setv field (. effect field))
  ;; 型検査のための展開(doeff_hy/static_view.py — 走らせず pyright が読むだけ・agora-redesign #2514)では、束ねの直後に
  ;; `_ = field` を置いて名を「読んだ」ことにする。欄の名で束ねるので、本体が使わない欄(例 `[stored mark]` の mark)は
  ;; reportUnusedVariable の赤になり、書き手は名を変えられない(`_` で始めると欄に当たらない)。本体が名を参照するかを
  ;; 記号の走査で決めると `stored.version` のような点つきの記号や macro が作る参照を取り逃がすので、全部の欄に置く。
  ;; 欄の名の綴りの誤りは `(. effect field)` の属性の読みで今までどおり赤になる。実行時の展開は変えない。
  (setv bindings
    (lfor f fields
          form (if (static-view-enabled)
                   [`(setv ~f (. effect ~(Symbol (str f))))
                    `(setv _ ~f)]
                   [`(setv ~f (. effect ~(Symbol (str f))))])
      form))

  ;; Rewrite resume/transfer/finish/pass (lazy-prefix is already in yield IR,
  ;; but _rewrite-ops only touches the clause operations — safe to pass through)
  (setv rewritten (+ lazy-prefix (lfor form cbody (_rewrite-ops form))))

  ;; A clause that leaves without resume / finish (transfer / reperform / raise never return) is an
  ;; error at run time — the VM would otherwise end the handled scope silently with the clause's value
  ;; (ADR-DOE-CORE-EFFECTS-003 R15).
  (setv checked
    `(do (setv _doeff_clause_resumed False)
         (setv _doeff_clause_value (do ~@rewritten))
         (if _doeff_clause_resumed
             _doeff_clause_value
             (raise (do ~(_clause-endings-import 'fell-through '_doeff-fell-through)
                        (_doeff-fell-through ~(if (is handler-name None) "handle" (str handler-name))
                                             ~(str etype)))))))

  ;; Build body: bindings first, then guard, then logic
  (setv full-body
    (if (is guard None)
        `(do ~@bindings ~checked)
        `(do ~@bindings
             ~@guard-prefix
             (if (not ~guard)
                 (yield (Pass effect k))
                 ~checked))))

  #(etype full-body))


(defn _build-handler-expr [clauses [lazy-defs None] [handler-name None] [module-names None] [specs None]]
  "Build handler expression from clauses. Returns _doeff-do wrapped fn.
   When `specs` is a list, appends each clause's ending description — the caller emits the check
   of those against the effects' declared resumption (ADR-DOE-CORE-EFFECTS-003 R15 — catches what
   the name alone cannot, e.g. an aliased Raise).
   If lazy-defs is provided, lazy init is injected into clauses that reference them."
  (setv cond-forms [])

  (for [clause clauses]
    (setv #(etype body) (_build-clause clause
                                       :lazy-defs lazy-defs
                                       :handler-name handler-name
                                       :module-names module-names
                                       :specs specs))
    (.append cond-forms `(isinstance effect ~etype))
    (.append cond-forms body))

  ;; Default: pass unmatched
  (.append cond-forms 'True)
  (.append cond-forms '(yield (Pass effect k)))

  ;; 型検査のための展開(agora-redesign #2279): effect は object(節の isinstance が節ごとの型へ絞る — 節の型の和で
  ;; 注記すると最後の節の isinstance が「いつも真」の赤になる)、k は続き、答えは ClauseRun(doeff_hy/static_types.pyi)。
  ;; 実行時の展開は VM の型の絞り込みのために effect を節の型で注記する(_effect-parameter)。
  (setv dispatcher
    (if (static-view-enabled)
        `(_doeff-do (fn #^ _doeff_ClauseRun [#^ object effect #^ _doeff_Continuation k] (cond ~@cond-forms)))
        `(_doeff-do (fn [~@(_effect-parameter (lfor clause clauses (get clause 0))) k] (cond ~@cond-forms)))))
  (setv passed (_passed-effects-source clauses (_session-names (or lazy-defs []))))
  (if (is passed None)
      dispatcher
      `((do (import doeff_vm._effect_types [declare-passes :as _doeff-declare-passes]) _doeff-declare-passes)
        ~dispatcher ~passed)))


(defn _passed-effects-source [clauses session-names]
  "The thunk telling the VM which effects a catch-all handler passes on untouched, or None.
   A handler whose one EffectBase clause is guarded by (not (isinstance effect X)) — the fence shape: answer
   everything except X — passes an X effect that no earlier clause names straight to (Pass effect k). The
   annotation cannot say \"everything except X\", so the VM entered the handler for every effect (agora-redesign
   #2008 — the doeff-cluster fence and dead-process gate: 18,000 entries in one invariant test). The thunk answers
   #(X #(earlier clause types)) at install (doeff_vm._effect_types.declare-passes) and the VM skips the handler for
   an X effect outside those types — the same as the guard failing. Any other guard shape, fields on the clause, or
   an X that reads a session name (bound only inside the clause) → None (the handler sees every effect)."
  (setv catch-alls (lfor #(index clause) (enumerate clauses) :if (= (str (get clause 0)) "EffectBase") index))
  (when (!= (len catch-alls) 1)
    (return None))
  (setv index (get catch-alls 0))
  (setv clause (get clauses index))
  (setv #(guard _body) (_parse-guard (list (cut clause 2 None))))
  (setv test (if (and (isinstance guard Expression) (= (len guard) 2) (= (str (get guard 0)) "not"))
                 (get guard 1)
                 None))
  (setv shaped (and (isinstance test Expression)
                    (= (len test) 3)
                    (= (str (get test 0)) "isinstance")
                    (= (str (get test 1)) "effect")
                    (= (len (get clause 1)) 0)))
  (when (or (not shaped)
            (_references-symbol (get test 2) "effect")
            (any (gfor name session-names (_references-symbol (get test 2) (str name)))))
    (return None))
  `(fn [] #(~(get test 2) #(~@(lfor earlier (cut clauses 0 index) (get earlier 0))))))


(defn _effect-parameter [etypes]
  "The handler's effect parameter, annotated with the clauses' effect types — the VM reads the annotation
   once at install (doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER) and skips this handler for any
   other effect without calling into Python, the same as the handler passing it on first thing
   (agora-redesign #1931 — the handler list walk was 28〜39% of an invariant test's CPU). The clauses
   already answer only their own types (every other effect falls to (Pass effect k)), so the
   annotation changes nothing but the cost. On Python 3.14+ the annotation is evaluated lazily (PEP 649),
   so an effect type defined later or in an enclosing function resolves. Before 3.14 an annotation is
   evaluated when the function is made, so it is written as text and evaluated at install in the module's
   globals — a name it cannot resolve there leaves the handler unfiltered (today's behaviour) with a warning,
   never an import error. No clauses → no annotation."
  (import sys)
  (cond
    (not etypes) ['effect]
    (>= sys.version-info #(3 14)) [`(annotate effect ~(if (= (len etypes) 1) (get etypes 0) `(| ~@etypes)))]
    True [`(annotate effect ~(.join " | " (lfor t etypes (.join "." (map hy.mangle (.split (str t) "."))))))]))


;; ---------------------------------------------------------------------------
;; Public macros
;; ---------------------------------------------------------------------------

(defmacro handle [_hy-compiler body #* clauses]
  "Inline pattern-matching effect handler.

   (handle body
     (EffectType [field1 field2]
       (resume (compute field1 field2)))
     (OtherEffect [x]
       :when (pred x)
       (resume (+ x 1))))

   Wraps body with the Rust handler node. Unmatched effects auto-Pass.
   Compile-time error if any clause branch lacks resume/transfer/finish/reperform; each clause's
   ending is checked against its effect's declared resumption when the handle form is evaluated."
  (import doeff-hy.macros [_module-names])
  (setv specs [])
  (setv h-expr (_build-handler-expr clauses :module-names (_module-names _hy-compiler) :specs specs))
  (locate-synthesized `(do
     ~(_do-import)
     ~(_vm-imports)
     ~(_clause-endings-import 'check-clause-endings '_doeff-check-clause-endings)
     (_doeff-check-clause-endings "handle" [~@specs])
     (WithHandler ~h-expr ~body))))


(defmacro with-handler [handlers body]
  "Apply a non-empty handler stack to a Program body.

   (with-handler [outer inner] body)

   expands to:

     (outer (inner body))

   The list order is scope order: leftmost is outermost, rightmost is
   innermost. Handler values are Program -> Program functions such as the
   values produced by defhandler."
  (when (not (isinstance handlers List))
    (raise (SyntaxError
             "with-handler requires a handler vector: (with-handler [h1 h2] body)")))
  (when (= (len handlers) 0)
    (raise (SyntaxError
             "with-handler requires a non-empty handler vector: (with-handler [h] body)")))
  (setv wrapped body)
  (for [h (reversed (list handlers))]
    (setv wrapped `(~h ~wrapped)))
  wrapped)


(defmacro defhandler [_hy-compiler name #* rest]
  "Named handler with optional parameters. Preserves s-expr body.

   ;; No params — plain handler value
   (defhandler my-handler
     (Effect [field] (resume (compute field))))

   ;; With docstring
   (defhandler my-handler
     \"Handle MyEffect values.\"
     (Effect [field] (resume (compute field))))

   ;; With params — handler factory function
   (defhandler my-handler [config timeout]
     (Effect [field] (resume (process field config))))

   ;; With guard
   (defhandler filtered-handler [cost]
     (Effect [field recompute-cost]
       :when (matches-cost recompute-cost cost)
       (resume (compute field))))

   ;; セッションの間で共有する値(状態の効果 Get / Put を通す — ADR-DOE-HY-006)
   (defhandler my-handler
     (session val client (Client :password (! (Ask \"api_key\"))))
     (session var calls 0)
     (Effect [field]
       (:= calls (+ calls 1))
       (resume (.fetch client field))))
   ;; 旧い (lazy …) / (lazy-val …) / (lazy-var …) / (set! …) は動きを変えずに受け、
   ;; DeprecationWarning で session val / session var / := への移行を案内する。

   ;; Terminal operations:
   ;;   (resume value)      — resume k, handler stays installed
   ;;   (transfer value)    — resume k, handler removed (tail-call optimized)
   ;;   (reperform effect)  — forward effect+k to outer handler (handler done)
   ;;
   ;; Non-terminal operation:
   ;;   (<- result effect)  — delegate to outer handler, get result back, continue

   The handler's __doeff_body__ stores the original s-expr clauses."

  (setv params None)
  (setv docstring None)
  (setv clauses rest)

  ;; Optional docstring may appear immediately after name for no-arg handlers
  ;; or after params for parameterized handlers.
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) String))
    (setv docstring (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  ;; First item after name: List = params, Expression = first clause
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) List))
    (setv params (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  (when (and (> (len clauses) 0) (isinstance (get clauses 0) String))
    (setv docstring (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  ;; Optional declaration dict {:effects [...] :tags {:context … :role …}} (agora-redesign #800 — doeff_hy.declarations).
  ;; A handler has no :pre / :post, so only :effects and :tags are accepted.
  (setv declarations None)
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) Dict))
    (setv declarations (get clauses 0))
    (setv clauses (cut clauses 1 None))
    (refuse-unknown-keys declarations DECLARATION-KEYS (+ "defhandler " (str name))))
  (setv declared (declaration-setters name declarations (+ "defhandler " (str name))))

  ;; Separate lazy defs from effect clauses
  (setv #(lazy-defs effect-clauses) (_extract-lazy-clauses clauses name))

  (import doeff-hy.macros [_module-names])
  (setv module-names (_module-names _hy-compiler))
  (setv specs [])
  (setv handler-expr
    (if lazy-defs
        (_build-handler-expr effect-clauses
                             :lazy-defs lazy-defs
                             :handler-name name
                             :module-names module-names
                             :specs specs)
        (_build-handler-expr effect-clauses :handler-name name :module-names module-names :specs specs)))
  ;; 節の終わり方と effect の再開の宣言の照合は、handler を初めて本文に被せた時に 1 回(定義より後に書いた effect の型も
  ;; 引けるように — ADR-DOE-CORE-EFFECTS-003 R15)。
  (setv endings-check
    `(_doeff-check-clause-endings-once __doeff-handler-data__ ~(str name) __doeff-clause-endings__))

  ;; Preserve s-expr body as a list of all clauses (including lazy) — carried as one hy.repr string and read back
  ;; on first access (macros._quoted-forms: a quote here compiled into code that rebuilt the model tree on every import).
  (import doeff-hy.macros [_quoted-forms])
  (setv quoted-body (_quoted-forms clauses))

  ;; Extra imports needed when lazy is used
  ;; 型検査のための展開では Get を使わない(名は初期化の値に束ねる — _build-lazy-init-forms)。Put と Some は session var の := だけが
  ;; 使うので、使わない handler で「import が使われていない」の赤にならない再輸出の綴り(X :as X)で取る(agora-redesign #2293)。
  (setv lazy-imports
    (cond
      (not lazy-defs) `(do)
      (static-view-enabled)
        `(do (import doeff [Some :as Some])
             (import doeff_core_effects.effects [Put :as Put])
             (import doeff-hy.session [session-key :as _doeff-session-key]))
      True
        `(do (import doeff [Some])
             (import doeff_core_effects.effects [Get Put])
             (import doeff-hy.session [session-key :as _doeff-session-key]))))

  ;; defhandler produces a Program -> Program function instead of exposing a
  ;; raw handler dispatcher. The inner dispatcher is stored for introspection
  ;; and installed with the Rust VM WithHandler node.
  (locate-synthesized (if (is params None)
      `(do
         ~(_do-import)
         ~(_vm-imports)
         ~(_clause-endings-import 'check-clause-endings-once '_doeff-check-clause-endings-once)
         ~lazy-imports
         (setv ~name
           ((fn []
              (setv __doeff-handler-data__ ~handler-expr)
              (setv __doeff-clause-endings__ (fn [] [~@specs]))
              ~(_handler-fn-form endings-check)
              (setattr __doeff-handler-fn__ "__doc__" ~docstring)
              (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
              (setattr __doeff-handler-fn__ "__doeff_handler_data__"
                    __doeff-handler-data__)
              __doeff-handler-fn__)))
         (setattr ~name "__doeff_body__" ~quoted-body)
         (setattr ~name "__doeff_name__" ~(str name))
         ~@declared)
      `(do
         ~(_do-import)
         ~(_vm-imports)
         ~(_clause-endings-import 'check-clause-endings-once '_doeff-check-clause-endings-once)
         ~lazy-imports
         (defn ~name [~@params]
           (setv __doeff-handler-data__ ~handler-expr)
           (setv __doeff-clause-endings__ (fn [] [~@specs]))
           ~(_handler-fn-form endings-check)
           (setattr __doeff-handler-fn__ "__doc__" ~docstring)
           (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
           (setattr __doeff-handler-fn__ "__doeff_handler_data__"
                 __doeff-handler-data__)
           (setattr __doeff-handler-fn__ "__doeff_name__" ~(str name))
           __doeff-handler-fn__)
         (setattr ~name "__doc__" ~docstring)
         (setattr ~name "__doeff_body__" ~quoted-body)
         (setattr ~name "__doeff_name__" ~(str name))
         ~@declared))))
