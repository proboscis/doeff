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
(import doeff-hy.clause-ops [clause-op node-names])
(import doeff-hy.expansion-names [alias-symbol import-form effect-symbol continuation-symbol WRITER-VISIBLE])
(import doeff-hy.binding-forms [parse-declaration Timing Mutability SessionName BodyKind
                                legacy-session-finding legacy-session-message])

(defn _do-import []
  "handler の本体を包む `_doeff-do` の import。型検査のための展開(doeff_hy/static_view.py)
   では型付きの doeff_hy.static_types.do を取る — 同じ module の defk と同じ名前に 2 つの
   型が付くと、pyright から見た `_doeff_do` が両者の union になり誤検出を出すため
   (macros.hy の `_helper-imports` と同じ決め)。実行時の do の置き場は補助の表の `_runtime-do`(card ki-973b4e44d7bc)。"
  (if (static-view-enabled)
      ;; 型付きの do は doeff-hy-check が module の頭に 1 度だけ import する(static_view.py の STATIC_HELPER_IMPORTS —
      ;; 節ごとに出すと 1 つの名の宣言が 64 を超えた module で pyright が Unknown にする・agora-redesign #1686)。
      '(do)
      (do
        (import doeff-hy.macros [_runtime-do-import])
        (_runtime-do-import '_doeff-do))))


(defn _clause-endings-import [name alias]
  "節の終わり方の検め(clause_endings.hy の name)を alias で引く import。型検査のための展開では、型付きの
   doeff_hy.static_types の同じ名を doeff-hy-check が module の頭に 1 度だけ import する(static_view.py の
   HANDLER_STATIC_NAMES — clause_endings は .hy なので pyright から型が見えない・agora-redesign #2279)。"
  (if (static-view-enabled)
      '(do)
      `(import doeff-hy.clause-endings [~name :as ~alias])))


(defn _vm-imports []
  "handler の展開が使う doeff の node の import — 節の操作の node(clause_ops.hy の表の nodes / static-nodes)と、handler 自身が
   使う Pass(番の外れた effect とどの節にも当たらない effect を外へ渡す)と WithHandler。型検査のための展開の resume / transfer は
   typed-resume / typed-transfer になり Resume / Transfer を使わないので、表の static-nodes に載せない(使わない import は strict の
   赤 — agora-redesign #2279)。

   名は `_doeff_<名>` の別名で束ねる(doeff_hy/expansion_names.py)。素の名で束ねると、書き手が自分の effect を同じ名にした時
   (振込の Transfer)、節の頭の isinstance が VM の node を指して effect が外の handler へ黙って抜け、module の段では書き手の
   名が上書きされた(card ki-16180480129e)。展開がこれらの node を読む綴りも、alias-symbol で同じ別名を引く。"
  (import-form (+ (node-names (static-view-enabled)) ["Pass" "WithHandler"])))


(defn _handler-fn-form [endings-check]
  "handler を本文に被せる関数 __doeff-handler-fn__ の定義。型検査のための展開では、本文を
   `HandlerBody[HandledAnswer]`、答えを `HandledScope[HandledAnswer]`(本文の答えの型をそのまま運ぶ)と注記する
   (doeff_hy/static_types.pyi・agora-redesign #2279)。実行時の展開は注記を付けない。"
  (if (static-view-enabled)
      `(defn #^ (get _doeff_HandledScope _doeff_HandledAnswer) __doeff-handler-fn__
             [#^ (get _doeff_HandlerBody _doeff_HandledAnswer) __doeff-body__]
         ~endings-check
         (~(alias-symbol "WithHandler") __doeff-handler-data__ __doeff-body__))
      `(defn __doeff-handler-fn__ [__doeff-body__]
         ~endings-check
         (~(alias-symbol "WithHandler") __doeff-handler-data__ __doeff-body__))))


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

(defn _names-symbol [form sym-name]
  "Check if form's AST contains the Symbol sym-name, or a dotted Symbol headed by it (`effect.amount`).
   Same traversal as _references-symbol."
  (cond
    (isinstance form Symbol) (or (= (str form) sym-name) (.startswith (str form) (+ sym-name ".")))
    (isinstance form Sequence)
      (any (gfor f form (_names-symbol f sym-name)))
    True False))

(defn _clause-aliases [clause lazy-defs]
  "書き手が節の本体・番に書いた `effect`・`k`(expansion_names.py の WRITER-VISIBLE の行)のうち、この節が束ねる物の行の list。

   展開は節を回す関数の引数と節の操作を `_doeff_effect`・`_doeff_k` で束ねて読むので、素の `effect`・`k` は書き手の名前空間のまま。
   書き手が `(reperform effect)`・`:when (isinstance effect X)` のように書いた節だけが、その名を節の effect・続きとして読める
   (`_alias-forms` の束ね・型検査のための展開の effect は `_narrowed-alias-bindings` の束ねと `_clause-test` の絞り)。節の欄・session の名が同じ名なら束ねない —
   書き手が欄・session で決めた名が優先する。点つきの記号(`effect.amount`)も名を使うと数える。名が本体の中の局所の束ね
   (`(lfor k …)`)にだけ現れても数える — 余分な束ねは害が無い(型検査のための展開は束ねた名を読む)。"
  (import doeff-hy.clause-endings [parse-clause-options])
  (setv options (parse-clause-options (list (cut clause 2 None)) "handle clause"))
  (setv taken (+ (lfor f (get clause 1) (str f))
                 (lfor #(lname _ _mut _legacy) (or lazy-defs []) (str lname))))
  (setv written-forms (+ (if (is (. options guard) None) [] [(. options guard)]) (. options body)))
  (lfor row WRITER-VISIBLE
        :if (and (not-in row.written taken)
                 (any (gfor form written-forms (_names-symbol form row.written))))
        row))

(defn _alias-forms [row]
  "節の本体の頭に置く、書き手の名 row.written の束ね。実行時の展開は `(setv effect _doeff_effect)`。型検査のための展開は、
   名に型を宣言し(`k: _doeff_Continuation`)、束ねた名を読む(`_ = k` — 書き手が使わない名は reportUnusedVariable の赤になる。
   欄の束ねの `_ = field` と同じ)。節の `isinstance` が絞る effect は、ここでは束ねない: 節を回す関数の頭で絞る前の値に束ね
   (`_narrowed-alias-bindings`)、節の判定が絞って読む(`_clause-test`)。"
  (setv written (Symbol row.written))
  (cond
    (not (static-view-enabled)) [`(setv ~written ~(Symbol row.internal))]
    row.narrowed []
    True [`(setv (annotate ~written ~(Symbol row.declared)) ~(Symbol row.internal))
          `(setv _ ~written)]))

(defn _narrowed-alias-bindings [clauses lazy-defs]
  "型検査のための展開で、節を回す関数の頭(節の判定の連なりより前)に置く、書き手の名の束ねの list(`effect = _doeff_effect`)。
   絞る名(row.narrowed)を書いた節が 1 つでも在る handler にだけ置く。実行時の展開は、書いた節の本体が束ねる(`_alias-forms`)。

   節の `isinstance` が絞る effect は、絞った後に代入すると、絞った総称型(`EffectBase[Unknown]`)を代入先の変数が受けて
   reportUnknownVariableType の赤になる(代入先に型を宣言しても同じ)。絞る前の値に束ねておき、書いた節の判定が書き手の名も絞る
   (`_clause-test`)。判定の中で束ねる形(`isinstance((effect := _doeff_effect), T) and isinstance(_doeff_effect, T)`)は採らない:
   walrus の右辺を絞るかは pyright の版で変わり(1.1.411 は左辺だけ・1.1.414 は右辺も)、右辺も絞る版では 2 つ目が「いつも真」
   (reportUnnecessaryIsInstance)になる — 書いた節ごとに 1 件で、書き手は直せない(card ki-a06b0fbce50d)。"
  (lfor row WRITER-VISIBLE
        :if (and row.narrowed
                 (any (gfor clause clauses (in row (_clause-aliases clause lazy-defs)))))
        `(setv ~(Symbol row.written) ~(Symbol row.internal))))

(defn _effect-form-binding [etype fields rewritten]
  "型検査のための展開で、節の `isinstance` が絞った `_doeff_effect` を、effect の型の型引数をすべて Any で解いた型に束ね直す形の list
   (実行時の展開と、束ね直す要らない節は空 — card ki-fede97890f70)。

   pyright は総称 class の絞りの型引数を Unknown にする(`isinstance(object の値, Race)` は `Race[Unknown]`)。節の欄の束ね
   (`tasks = _doeff_effect.tasks`)も typed_resume / typed_transfer の effect の引数も一部 Unknown の赤になり、書き手は直せない
   (`(Race [tasks] …)`・`(Spawn [program] …)` — 欄の名は effect の定義で決まり、赤は展開が作る名と呼びの物)。絞る元の型を
   `EffectBase[Any]` にすると基底から解ける型引数だけが埋まり、`Spawn` の `_E` のように基底に無い型引数は Unknown のままで、
   型変数の既定値は isinstance の絞りでは使われない。型引数の数は展開の時に知れない(macro は import した名の class を引けない)ので、
   構成子の呼び `_doeff_any_specialized(Race, *_doeff_any_args, **_doeff_any_kwargs)` で型を作る(doeff_hy/static_types.pyi)。構成子の
   引数がすべて Any なので、型引数がすべて Any で解かれ、欄の型は effect の定義の型のまま(`tuple[Task[Any] | Future[Any], ...]`)、
   総称でない class は絞りと同じ型になる。型検査のための展開は走らせず pyright が読むだけなので、呼びの値は要らない。

   束ね直すのは節が effect を読む時だけ: 欄を束ねるか、書き換えた本体が `_doeff_effect` を読む(typed_resume / typed_transfer・
   `(pass)`)。何も読まない節(`(Spawn [] (reperform effect))`)には何も足さない。head が名でない節(`#(A B)`・`(| A B)`)は構成子として
   呼べないので束ね直さない。"
  (if (and (static-view-enabled)
           (isinstance etype Symbol)
           (or (> (len fields) 0)
               (any (gfor form rewritten (_references-symbol form (str (effect-symbol)))))))
      [`(setv ~(effect-symbol) (_doeff_any_specialized ~etype #* _doeff_any_args #** _doeff_any_kwargs))]
      []))

(defn _clause-test [etype aliases]
  "節の判定 — 受けた effect が節の effect の型か。型検査のための展開で、絞る名(row.narrowed)を書き手が使う節は、書き手の名も
   同じ型へ絞る(`isinstance(_doeff_effect, T) and isinstance(effect, T)`)。書き手の名は節を回す関数の頭で絞る前の値に束ねてある
   (`_narrowed-alias-bindings`)ので、判定は別の名を 1 度ずつ絞る — 絞るのは代入でないので絞った型が一部 Unknown でも赤が出ず、
   どの名も 2 度は絞らないので「いつも真」の isinstance にならない。`_doeff_effect` は続く `typed_resume` と欄の束ねが読む。"
  (setv narrowed (lfor row aliases :if row.narrowed row))
  (if (and (static-view-enabled) narrowed)
      `(and (isinstance ~(effect-symbol) ~etype)
            ~@(lfor row narrowed `(isinstance ~(Symbol row.written) ~etype)))
      `(isinstance ~(effect-symbol) ~etype)))

(defn _build-lazy-init-forms [handler-name lazy-name lazy-body [legacy? True]]
  "Build the yield-based lazy init code for one lazy def.
   Returns list of Hy forms (already in yield IR — no <- needed).

   Generated pattern (Get / Put / Some are the `_doeff_` aliases — expansion_names.py):
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
        `(yield (~(alias-symbol "Put") ~key-var (~(alias-symbol "Some") ~val-var)))
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
       `(setv ~cached-var (yield (~(alias-symbol "Get") ~key-var)))
       `(if (isinstance ~cached-var ~(alias-symbol "Some"))
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
  "Check if a single form always reaches a clause operation (resume/transfer/finish/reperform/raise — the table in
   clause_ops.hy) — on every path (each if / cond / match / try branch; when / unless / and / or / loops never guarantee it)."
  (setv head (_head-name form))
  (cond
    (is head None) False

    ;; Direct terminals — every clause operation ends the clause
    (is-not (clause-op form) None) True

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
  "Replace a tail-position clause operation with the operation its table row names as tail (clause_ops.hy —
   (resume expr) → (transfer expr)). Recurses into if/cond/match/do/let but NOT into try (need frame for except)."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [hname (_sym-name (get form 0))
            op (clause-op form)]
        (cond
          ;; (resume expr) → (transfer expr) — the row's tail
          (and (is-not op None) (is-not op.tail None) (= (len form) (+ 1 op.arity)))
            (Expression [(Symbol op.tail) #* (cut form 1 None)])

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
  "Recursively rewrite the clause operations in a handler clause body with the table's rewrites (clause_ops.hy):
   (resume expr)      → (yield (Resume _doeff_k expr))  — and marks the clause as resumed
   (transfer expr)    → (yield (Transfer _doeff_k expr))
   (finish expr)      → (return expr)  — drop the continuation, the handled scope answers expr
   (reperform effect) → (yield (Pass effect _doeff_k))     — OCaml 5 aligned
   (pass)             → (yield (Pass _doeff_effect _doeff_k))      — deprecated, use reperform
   (the clause's effect and continuation are `_doeff_effect` / `_doeff_k` — expansion_names.py — so a clause field named
   `k` or `effect` does not overwrite them; the Resume / Transfer / Pass above are the `_doeff_` aliases too)
   Only a form whose argument count matches the operation's arity is rewritten; raise (no rewrite) and every
   other form are walked into."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [op (clause-op form)
            args (list (cut form 1 None))]
        (cond
          (or (is op None) (is op.rewrite None) (!= (len args) op.arity))
            (Expression (lfor f form (_rewrite-ops f)))
          ;; 続きが終わると節へ戻る操作(表の returns — resume)は、戻った後で「再開した」と記す(値の式や続きで出た例外を節が
          ;; 握りつぶして抜けた道は印が付かず、実行の時に誤りになる・ADR-DOE-CORE-EFFECTS-003 R15)。印の名は _build-clause の
          ;; 実行の時の検めとこの関数だけが知る — 表の行の書き換えは VM へ渡す form だけを作る。
          op.returns
            `(do (setv _doeff_resumed_value ~(op.rewrite args _rewrite-ops))
                 (setv _doeff_clause_resumed True)
                 _doeff_resumed_value)
          True
            (op.rewrite args _rewrite-ops)))))


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
  ;; handler の直下のセッションの名前は、本体の前から束縛されている名前(effect・k は書き手が本体・番に書いた時だけ
  ;; 展開が束ねる予約された名 — _clause-aliases)。
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

  ;; Field bindings: (setv field (. _doeff_effect field))
  ;; 型検査のための展開(doeff_hy/static_view.py — 走らせず pyright が読むだけ・agora-redesign #2514)では、束ねの直後に
  ;; `_ = field` を置いて名を「読んだ」ことにする。欄の名で束ねるので、本体が使わない欄(例 `[stored mark]` の mark)は
  ;; reportUnusedVariable の赤になり、書き手は名を変えられない(`_` で始めると欄に当たらない)。本体が名を参照するかを
  ;; 記号の走査で決めると `stored.version` のような点つきの記号や macro が作る参照を取り逃がすので、全部の欄に置く。
  ;; 欄の名の綴りの誤りは `(. _doeff_effect field)` の属性の読みで今までどおり赤になる。実行時の展開は変えない。
  ;; 欄は節の effect を `_doeff_effect` から読む: 欄の名が `effect`・`k` でも、受けた effect と続きを上書きしない
  ;; (card ki-6af9862aed32)。書き手が本体・番に書いた `effect`・`k` の束ねは、欄の束ねより前に置く(欄が同じ名なら束ねず、欄が優先)。
  (setv bindings
    (+ (lfor row (_clause-aliases clause lazy-defs)
             form (_alias-forms row)
         form)
       (lfor f fields
             form (if (static-view-enabled)
                      [`(setv ~f (. ~(effect-symbol) ~(Symbol (str f))))
                       `(setv _ ~f)]
                      [`(setv ~f (. ~(effect-symbol) ~(Symbol (str f))))])
         form)))

  ;; Rewrite resume/transfer/finish/pass (lazy-prefix is already in yield IR,
  ;; but _rewrite-ops only touches the clause operations — safe to pass through)
  (setv rewritten (+ lazy-prefix (lfor form cbody (_rewrite-ops form))))

  ;; 総称の effect の節: 絞った `_doeff_effect` の型引数が Unknown にならないよう、欄の束ねより前に束ね直す(型検査のための展開だけ)。
  (setv bindings (+ (_effect-form-binding etype fields rewritten) bindings))

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
                 (yield (~(alias-symbol "Pass") ~(effect-symbol) ~(continuation-symbol)))
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
    (.append cond-forms (_clause-test etype (_clause-aliases clause lazy-defs)))
    (.append cond-forms body))

  ;; Default: pass unmatched
  (.append cond-forms 'True)
  (.append cond-forms `(yield (~(alias-symbol "Pass") ~(effect-symbol) ~(continuation-symbol))))

  ;; 型検査のための展開(agora-redesign #2279): effect は object(節の isinstance が節ごとの型へ絞る — 節の型の和で
  ;; 注記すると最後の節の isinstance が「いつも真」の赤になる)、k は続き、答えは ClauseRun(doeff_hy/static_types.pyi)。
  ;; 書き手が `effect` を書いた節の在る handler は、節の判定より前に `effect` を絞る前の値に束ねる(_narrowed-alias-bindings)。
  ;; 実行時の展開は VM の型の絞り込みのために effect を節の型で注記する(_effect-parameter)。
  (setv dispatcher
    (if (static-view-enabled)
        `(_doeff-do (fn #^ _doeff_ClauseRun [(annotate ~(effect-symbol) object)
                                             (annotate ~(continuation-symbol) _doeff_Continuation)]
                      ~@(_narrowed-alias-bindings clauses lazy-defs)
                      (cond ~@cond-forms)))
        `(_doeff-do (fn [~@(_effect-parameter (lfor clause clauses (get clause 0))) ~(continuation-symbol)]
                      (cond ~@cond-forms)))))
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
    (not etypes) [(effect-symbol)]
    (>= sys.version-info #(3 14)) [`(annotate ~(effect-symbol) ~(if (= (len etypes) 1) (get etypes 0) `(| ~@etypes)))]
    True [`(annotate ~(effect-symbol) ~(.join " | " (lfor t etypes (.join "." (map hy.mangle (.split (str t) "."))))))]))


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
   ending is checked against its effect's declared resumption when the handle form is evaluated.

   関数の本体の中(defk の本体など)と class の本体では、import・節の終わり方の検め・handler の組み立てを、すぐ呼ぶ
   引数なしの関数に入れ、その関数が返す「本文に handler を被せる関数」を本文 body に当てる。関数の中で import した名は
   関数全体の局所の名になるので、並べたままだと同じ本体で先に読む traverse / for/do の `_doeff_do` が UnboundLocalError
   で落ちた(card ki-907f779b3290・tests/test_handle_scope.py)。class の本体で import した名は class の属性になり、節の
   関数は名を引く時に class の段を飛ばすので、節の Transfer・Resume・Pass が NameError で落ちた(card ki-edab7c8fbb55)。
   すぐ呼ぶ関数の中で import すれば、節の関数はその関数の閉包から名を読み、書いた場所の module の globals にも書き手の
   同じ名にも依らない。本文は外の do の文脈に属する(`!` の yield は外の生成器の物)ので、すぐ呼ぶ関数の中に入れず、
   書いた場所で評価する。module の段は今までどおり並べる(import は module の globals に置く)。

   被せる関数は、本文より前の文で一意の名(gensym)に束ねてから本文に当てる。Hy は本文の文の部分(`(do (f) prog)` の
   `(f)`・let の束ね・`!` の yield)を、それを含む式より前の文へ出す。すぐ呼ぶ関数を本文と同じ式の中で呼ぶと、本文の
   文の部分が検めより先に走り、検めが落ちる handler でも本文の effect が先に出ていた(設計検証 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W
   の盲検 A)。外の関数に残る名はこの一意の名だけで、先に読む所が無いので未束縛にならない。"
  (import doeff-hy.macros [_module-names _in-function-body? _in-class-body?])
  (setv specs [])
  (setv h-expr (_build-handler-expr clauses :module-names (_module-names _hy-compiler) :specs specs))
  (setv prelude [(_do-import)
                 (_vm-imports)
                 (_clause-endings-import 'check-clause-endings '_doeff-check-clause-endings)
                 `(_doeff-check-clause-endings "handle" [~@specs])])
  (locate-synthesized (if (or (_in-function-body? _hy-compiler) (_in-class-body? _hy-compiler))
      ;; gensym はこの枝だけで引く — module の段の展開の一意の名の番号を前と同じに保つ
      (let [install (hy.gensym "handle_install")]
        `(do
           (setv ~install ((fn []
                             ~@prelude
                             (setv __doeff-handler-data__ ~h-expr)
                             (fn [__doeff-body__] (~(alias-symbol "WithHandler") __doeff-handler-data__ __doeff-body__)))))
           (~install ~body)))
      `(do
         ~@prelude
         (~(alias-symbol "WithHandler") ~h-expr ~body)))))


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

  (import doeff-hy.macros [_module-names _scoped-definition _in-class-body?])
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
  ;; 型検査のための展開では Get を使わない(名は初期化の値に束ねる — _build-lazy-init-forms)。Put・Some・キーの session-key は
  ;; `_doeff_<名>` で引き、doeff-hy-check が module の頭で読む時だけ import する(static_view.py の SESSION_STATIC_IMPORTS)。
  ;; ここで handler ごとに出すと、:= も set! も無い handler の module で Put / Some が「import が使われていない」の赤になっていた
  ;; (再輸出の綴り X :as X は Hy が同じ名の別名を落とすので pyright に届かない)。
  ;; Get・Put・Some は `_doeff_<名>` の別名で束ねる(書き手の KV の Put・Get などの effect を隠さない — expansion_names.py)。
  (setv lazy-imports
    (cond
      (or (not lazy-defs) (static-view-enabled)) `(do)
      True
        `(do ~(import-form ["Some" "Get" "Put"])
             (import doeff-hy.session [session-key :as _doeff-session-key]))))

  ;; defhandler produces a Program -> Program function instead of exposing a
  ;; raw handler dispatcher. The inner dispatcher is stored for introspection
  ;; and installed with the Rust VM WithHandler node.
  ;; 関数の本体の中に書いた defhandler は、import と定義をすぐ呼ぶ関数に入れて name だけを束ねる(_scoped-definition —
  ;; 外の関数で先に読む traverse / for/do の `_doeff_do` を未束縛にしない・card ki-907f779b3290)。
  ;; class の本体では、import を handler を組む関数(引数の無い handler はすぐ呼ぶ関数・引数の在る handler は name の
  ;; 関数そのもの)の中へ移す。class の本体の import は class の属性になり、組む関数・節の関数・本文に被せる関数は名を
  ;; 引く時に class の段を飛ばすので、_doeff_do・Transfer などが NameError で落ちた(card ki-edab7c8fbb55)。中で
  ;; import すれば、それらの関数は組む関数の局所と閉包から名を読む。name の定義は class の段に残す(`__qualname__`)。
  (setv imports [(_do-import)
                 (_vm-imports)
                 (_clause-endings-import 'check-clause-endings-once '_doeff-check-clause-endings-once)
                 lazy-imports]
        #(outer-imports inner-imports) (if (_in-class-body? _hy-compiler) #([] imports) #(imports [])))
  (locate-synthesized (_scoped-definition _hy-compiler name (if (is params None)
      [#* outer-imports
       `(setv ~name
         ((fn []
            ~@inner-imports
            (setv __doeff-handler-data__ ~handler-expr)
            (setv __doeff-clause-endings__ (fn [] [~@specs]))
            ~(_handler-fn-form endings-check)
            (setattr __doeff-handler-fn__ "__doc__" ~docstring)
            (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
            (setattr __doeff-handler-fn__ "__doeff_handler_data__"
                  __doeff-handler-data__)
            __doeff-handler-fn__)))
       `(setattr ~name "__doeff_body__" ~quoted-body)
       `(setattr ~name "__doeff_name__" ~(str name))
       #* declared]
      [#* outer-imports
       `(defn ~name [~@params]
         ~@inner-imports
         (setv __doeff-handler-data__ ~handler-expr)
         (setv __doeff-clause-endings__ (fn [] [~@specs]))
         ~(_handler-fn-form endings-check)
         (setattr __doeff-handler-fn__ "__doc__" ~docstring)
         (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
         (setattr __doeff-handler-fn__ "__doeff_handler_data__"
               __doeff-handler-data__)
         (setattr __doeff-handler-fn__ "__doeff_name__" ~(str name))
         __doeff-handler-fn__)
       `(setattr ~name "__doc__" ~docstring)
       `(setattr ~name "__doeff_body__" ~quoted-body)
       `(setattr ~name "__doeff_name__" ~(str name))
       #* declared]))))
