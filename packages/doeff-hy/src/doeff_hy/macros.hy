;;; doeff-hy standard macros — effect composition for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.macros [do! defk deff fnk <- ! <-> set! defp defpp deftest
;;;                             defpipeline traverse for/do
;;;                             defhandler handle with-handler defmcp-tool
;;;                             validate check])   ; validate / check は doeff-validation を入れて使う
;;;   (import doeff [do :as _doeff-do])
;;;
;;; Core effects are imported from doeff_core_effects:
;;;   (import doeff_core_effects [Ask Try slog])
;;;
;;; Contract rules:
;;;   - deff:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defk:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defp/defpp:  {:post [...]} is REQUIRED, :pre not allowed
;;;   - fnk:         no contracts (anonymous)
;;;   - do!:         :pre/:post optional, supports (: name Type) shorthand
;;;   - (: name Type) in :pre/:post expands to (isinstance name Type)
;;;   - Arbitrary expressions can be mixed with (: ...) in the same list

;; ---------------------------------------------------------------------------
;; Internal: .hyk/.hyp extension enforcement
;; ---------------------------------------------------------------------------

(import os.path)
(import inspect)
(import doeff-hy.positions [locate-synthesized])

;; Re-export handle macros so users only need one require line.
;; Without this, forgetting (require doeff-hy.handle [defhandler]) causes
;; (defhandler ...) to compile as a function call, silently leaking yield
;; from nested <- to the enclosing defn scope. See #387.
(require doeff-hy.handle [defhandler handle with-handler])

(defn _compiling-file-ext []
  "Return the file extension of the .hy/.hyk/.hyp file being compiled.
   Walks the call stack to find the Hy source_to_code path argument."
  (try
    (for [frame-info (inspect.stack)]
      ;; Hy's _hy_source_to_code has 'path' as a local variable
      (setv loc (. frame-info [0] f_locals))
      (when (in "path" loc)
        (setv path (get loc "path"))
        (when (isinstance path str)
          (setv ext (get (os.path.splitext path) 1))
          (when (in ext [".hyk" ".hyp"])
            (return ext)))))
    (return "")
    (except [e Exception] (return ""))))

(defn _enforce-no-defp-in-hyk [macro-name fn-name]
  "Raise SyntaxError if a defp/defpp macro is used in a .hyk file."
  (setv ext (_compiling-file-ext))
  (when (= ext ".hyk")
    (raise (SyntaxError (.format "
{macro} {name}: cannot define a Program entrypoint in a .hyk file.

  .hyk files are for kleisli functions (defk, deff, defhandler).
  Move this {macro} to a .hyp file instead.
" :macro macro-name :name fn-name)))))

(defn _warn-defk-in-hyp [macro-name fn-name]
  "Warn if defk/deff is used in a .hyp file."
  (setv ext (_compiling-file-ext))
  (when (= ext ".hyp")
    (import warnings)
    (warnings.warn
      (.format "{macro} {name}: .hyp files are for Program entrypoints (defp). Consider moving {macro} to a .hyk file."
               :macro macro-name :name fn-name)
      UserWarning
      :stacklevel 4)))

;; ---------------------------------------------------------------------------
;; Internal: contract extraction
;; ---------------------------------------------------------------------------

(defn _extract-contracts [body]
  "Parse optional {:pre [...] :post [...]} from front of body forms.
   Skips leading docstring if present.
   Returns #(pre-checks post-checks real-body).
   pre-checks/post-checks are None if not specified, [] if specified but empty."
  (setv pre-checks None
        post-checks None
        real-body body)
  ;; Find the contract dict — may be body[0] or body[1] (after docstring)
  (setv contract-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv contract-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
  (when (is-not contract-idx None)
    (setv contract (get body contract-idx)
          real-body (+ (cut body 0 contract-idx) (cut body (+ contract-idx 1) None)))
    (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
      (when (= (str k) ":pre")
        (setv pre-checks (list v)))
      (when (= (str k) ":post")
        (setv post-checks (list v)))))
  #(pre-checks post-checks real-body))

(defn _is-type-check [form]
  "Check if form is (: name Type) — type contract shorthand.
   In Hy, bare : is read as Keyword(''), not Symbol(':')."
  (and (isinstance form hy.models.Expression)
       (>= (len form) 3)
       (isinstance (get form 0) hy.models.Keyword)
       (= (str (get form 0)) ":")))

(defn _type-check-target [form]
  "If form is (: name Type), return name as string. Otherwise None."
  (when (_is-type-check form)
    (str (get form 1))))

(defn _extract-param-names [params]
  "Extract parameter names from a defk/deff param list.
   Handles: [x y], [x * [timeout 30]], [x #^ int y], etc.
   Returns set of name strings (excludes * separator)."
  (setv names (set))
  (for [p params]
    (cond
      ;; * is keyword-only separator
      (and (isinstance p hy.models.Symbol) (= (str p) "*"))
        None
      ;; [name default] — keyword arg with default
      (isinstance p hy.models.List)
        (when (> (len p) 0)
          (.add names (str (get p 0))))
      ;; #^ type name — annotated param (annotation is previous form, name follows)
      ;; Hy puts annotation as FComponent, we see Symbol
      (isinstance p hy.models.Symbol)
        (when (!= (str p) "&rest")
          (.add names (str p)))))
  names)

(setv _HANDLER-PARAM-NAMES #{"effect" "eff" "k"})

(setv _DEFHANDLER-EXAMPLE "
  Use defhandler instead of defk for effect handlers:

    (require doeff-hy.macros [defk <- defhandler])
    ;; Simple handler — pattern match on effects, unmatched auto-Pass
    (defhandler my-handler
      (MyEffect [field1 field2]
        (resume (compute field1 field2)))
      (OtherEffect [x]
        (resume (+ x 1))))

    ;; Handler with parameters — returns a handler factory
    (defhandler my-handler [config]
      (MyEffect [field]
        (resume (process field config))))

    ;; Handler with guard — auto-reperform when guard is false
    (defhandler my-handler
      (MyEffect [field]
        :when (> field 0)
        (resume field)))

    ;; Conditional forwarding — (reperform effect) is terminal
    (defhandler my-handler
      (MyEffect [field]
        (if (can-handle field)
            (resume (process field))
            (reperform effect))))

    ;; Delegation — (<- result effect) is non-terminal (keeps control)
    (defhandler my-handler
      (MyEffect [field]
        (<- base-result (MyEffect :field field))
        (resume (* base-result 2))))

    ;; Install through explicit handler stack syntax
    (with-handler [my-handler] body)
    (with-handler [(my-handler config)] body)
")

(defn _reject-handler-signature [fn-name params]
  "Reject defk with handler-like parameter names (effect, eff, k).
   These should use defhandler instead."
  (setv param-names (_extract-param-names params))
  (setv handler-params (sorted (& param-names _HANDLER-PARAM-NAMES)))
  (when handler-params
    (setv found (.join ", " handler-params))
    (raise (SyntaxError (+ (.format "
defk {name}: parameter names [{found}] look like an effect handler signature.
" :name fn-name :found found) _DEFHANDLER-EXAMPLE)))))

(setv _USELESS-TYPES #{"object" "Any"})

(defn _reject-useless-types [fn-name phase checks]
  "Reject (: name Type) where Type is object or Any — too broad to be useful.
   String literals are allowed (documentation-only annotations).
   Raises SyntaxError with a fix suggestion."
  (for [check checks]
    (when (_is-type-check check)
      (setv tp (get check 2))
      (when (isinstance tp hy.models.String) (continue))  ; string = doc annotation, skip
      (setv tp-str (str tp)
            target-str (str (get check 1)))
      (when (in tp-str _USELESS-TYPES)
        (raise (SyntaxError (+ (.format "
defk {name}: {phase} type `{tp}` on `{target}` is too broad to be a useful contract.

  Use a specific type instead of `{tp}`:

    (: {target} SomeConcreteType)

  Contracts exist to catch bugs early — `{tp}` matches everything and catches nothing.

  If this is an effect handler, the return type depends on the wrapped program
  and cannot be expressed as a :post contract.
" :name fn-name :phase phase :tp tp-str :target target-str) _DEFHANDLER-EXAMPLE)))))))

(defn _validate-pre-type-checks [fn-name params pre-checks]
  "Validate that :pre has a (: param Type) for every parameter.
   Raises SyntaxError if any parameter lacks a type check."
  (_reject-useless-types fn-name ":pre" pre-checks)
  (setv param-names (_extract-param-names params))
  (when (not param-names) (return))  ; zero-arg function — nothing to check
  (setv checked (set))
  (for [check pre-checks]
    (setv target (_type-check-target check))
    (when (and target (in target param-names))
      (.add checked target)))
  (setv missing (sorted (- param-names checked)))
  (when missing
    (setv missing-str (.join ", " missing))
    (setv all-params (sorted param-names))
    (setv full-pre (.join " " (lfor p all-params (+ "(: " p " SomeType)"))))
    (raise (SyntaxError (.format "
defk {name}: :pre must have a (: param Type) for every parameter.

  Missing: {missing}

  Fix — add type checks for each parameter:

    (defk {name} [{params}]
      {{:pre [{pre}]
       :post [(: % ReturnType)]}}
      ...)
" :name fn-name :missing missing-str
  :params (.join " " all-params)
  :pre full-pre)))))

(defn _validate-post-type-check [fn-name post-checks]
  "Validate that :post has at least one (: % Type) return type check.
   Raises SyntaxError if missing."
  (_reject-useless-types fn-name ":post" post-checks)
  (setv has-return-type False)
  (for [check post-checks]
    (setv target (_type-check-target check))
    (when (= target "%")
      (setv has-return-type True)
      (break)))
  (when (not has-return-type)
    (raise (SyntaxError (.format "
defk {name}: :post must include a return type check (: % Type).

  Fix:

    (defk {name} [...]
      {{:pre [...]
       :post [(: % dict)]}}              ;; ← add return type
      ...)

  (: % Type) checks isinstance on the return value.
  You can add extra validations too:
    {{:post [(: % pd.DataFrame) (> (len %) 0)]}}
" :name fn-name)))))

(defn _runtime-type [tp]
  "型の式を isinstance に渡せる形へ写す(契約の `(: x T)` と `(<- x T e)` の 1 点)。

   型の注記では `None` は「None という値の型」を意味する(PEP 484)が、isinstance の第 2
   引数に `None` は渡せない(`TypeError: isinstance() arg 2 must be a type ...`)。
   `(: % None)` が実行時に型エラーになっていた(2026-09-23 `sim_clock.hy` の
   `clock-driver` で実測)ので、ここで `None` を `(type None)` へ写す。`#(int None)` の
   組の中も同じく写す。`(| int None)` は Python 3.10 以降の isinstance がそのまま受ける
   (`int | None` は types.UnionType)ので触らない。"
  (cond
    (and (isinstance tp hy.models.Symbol) (= (str tp) "None"))
      `(type None)
    (isinstance tp hy.models.Tuple)
      (hy.models.Tuple (lfor item tp (_runtime-type item)))
    True tp))

;; ---------------------------------------------------------------------------
;; 型の注記 — 契約の `(: x T)` を関数の注記にも書く(静的な検査が読む)
;; ---------------------------------------------------------------------------
;;
;; 契約の型は実行時の isinstance の検査で使ってきたが、関数の注記には出していなかった
;; ので、pyright は defk / deff の引数と戻り値の型を知らなかった(2026-09-23 実測:
;; わざとの間違い 7 種を Hy 版は静的に 1 つも捕まえなかった)。同じ型を注記に写す。
;; 注記は文字列で書く(Python 3.13 以前でも定義の時に評価しない — 後で定義する名前の型を
;; 契約に書いても定義が落ちない)。文字列の型 `(: x "説明")` は注記にしない。

(import doeff-hy.static-view [static-view-enabled])

(defn _static-view? []
  "doeff-hy-check が型検査のための展開をしている間だけ真(doeff_hy/static_view.py)。"
  (static-view-enabled))

(defn _type-source [tp]
  "契約の型の式を Python の source の文字列にする。文字列の型(説明だけ)は None。
   組 `#(int None)` は isinstance の書き方なので、注記では `int | None` へ写す。"
  (import ast)
  (import hy.compiler [hy-compile])
  (cond
    (isinstance tp hy.models.String) None
    (and (isinstance tp hy.models.Symbol) (= (str tp) "None")) "None"
    (isinstance tp hy.models.Tuple)
      (let [parts (lfor item tp (_type-source item))]
        (if (in None parts) None (.join " | " parts)))
    ;; `(type None)`(None を isinstance に渡すための書き方)は注記では `None`。
    (and (isinstance tp hy.models.Expression) (= (len tp) 2)
         (= (str (get tp 0)) "type") (= (str (get tp 1)) "None"))
      "None"
    ;; root=ast.Expression は Hy が ast.Expression に type_ignores を渡して Python 3.14 で
    ;; DeprecationWarning になるので、module として compile して最後の式を取る。
    ;; 呼び出しの形(`(type x)` など)は型の注記として書けないので注記にしない。
    True
      (let [node (. (get (. (hy-compile tp "__main__" :import-stdlib False) body) -1) value)]
        (if (isinstance node ast.Call) None (ast.unparse node)))))

(defn _contract-types [checks]
  "契約の `(: name T)` から {name: T の source} を作る(説明だけの型は除く)。"
  (setv types {})
  (for [check (or checks [])]
    (when (_is-type-check check)
      (setv source (_type-source (get check 2)))
      (when (is-not source None)
        (setv (get types (str (get check 1))) source))))
  types)

(defn _annotate-params [params types]
  "引数の並びの各引数に、契約の型の注記を付ける。書き手が既に `#^ T x` と書いた引数・
   `*`・`/`・`#* args` には触らない。"
  (hy.models.List
    (lfor p params
      (cond
        (and (isinstance p hy.models.Symbol) (in (str p) types))
          `(annotate ~p ~(hy.models.String (get types (str p))))
        (and (isinstance p hy.models.List) (> (len p) 0)
             (isinstance (get p 0) hy.models.Symbol) (in (str (get p 0)) types))
          `(annotate ~p ~(hy.models.String (get types (str (get p 0)))))
        True p))))

(defn _expand-check [check fn-name phase]
  "Expand a single contract check into an assert form.
   (: x T) → isinstance assert with clear type error message.
   (: x \"desc\") → no-op (documentation-only annotation).
   Other  → generic condition assert."
  (if (_is-type-check check)
      (let [target (get check 1)
            tp (get check 2)]
        (if (isinstance tp hy.models.String)
            ;; String literal — documentation-only, no runtime check
            (do
              (when (not (.strip (str tp)))
                (raise (SyntaxError (.format "
defk {name}: :post type annotation cannot be an empty string.

  Describe what the return value represents:

    (: % \"DataFrame with OHLCV columns\")
    (: % \"list of matched handler results\")
" :name fn-name))))
              '(do))
            (let [target-label (if (= (str target) "%") "return value" (str target))]
              `(assert (isinstance ~target ~(_runtime-type tp))
                       (+ ~(+ (str fn-name) ": " phase " type error: `" target-label "` expected " (str tp) ", got ")
                          (. (type ~target) __name__))))))
      `(assert ~check ~(+ (str fn-name) ": " phase " failed: " (str check)))))

(defn _is-validation-check [form]
  "契約の 1 条件が doeff-validation の (check …) の形か。"
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "check")))

(defn _contract-code [checks fn-name phase kleisli?]
  "契約(:pre / :post)の条件の列を、関数の中に置く文の列にする。

   - 型の (: x T) と真偽の式: 今までどおり 1 つずつ assert(最初の失敗で止まる — 既存の契約の意味を
     変えない)。
   - (check 演算子 引数 … :reason 理由): doeff-validation の意味で評価する — 全部を評価して失敗を
     集め、1 つでもあれば ValidationException(関数名と事前 / 事後・各失敗の式と値と理由)を投げる。
     引数の (! …) はその場で実行する。defk / do!(生成器)だけが受ける。
   assert の後に check を置く: 型が合っていることを check の式が前提にできる。"
  (setv checks (or checks []))
  (setv code (lfor c checks :if (not (_is-validation-check c)) (_expand-check c fn-name phase)))
  (setv validation-checks (lfor c checks :if (_is-validation-check c) c))
  (when validation-checks
    (when (not kleisli?)
      (raise (SyntaxError (+ (str fn-name) ": " phase " の (check …) は defk / do! の契約にだけ書けます"
                             "(効果を使う検査を評価するので、生成器の本体が要る)— defk で定義してください"))))
    (.extend code (_validation-contract-block validation-checks fn-name phase)))
  code)

(defn _guard-performed [result label]
  "Raise if a kleisli (defk/do!) last expression evaluated to an unperformed
   effect. A bare EffectBase as the final form is RETURNED as a value, never
   performed — almost always a bug (regression: a do! whose last form was
   `(LaunchEffect ...)` returned the effect object and never launched the
   agent). The Program-return composition pattern returns a DoExpr (Pure/Expand/
   a @do call), NOT an EffectBase, so this never fires on it; plain functions
   (deff/defn) returning effect constructors are not kleisli and are not guarded.
   Returns `result` unchanged when fine, so it can wrap a return position."
  (import doeff [EffectBase])
  (when (isinstance result EffectBase)
    (raise (RuntimeError
             (+ label ": last expression is an unperformed effect `"
                (. (type result) __name__)
                "`. A defk/do! last form is RETURNED as a value, not performed — "
                "bind it instead: (<- v (" (. (type result) __name__)
                " ...)) then return v."))))
  result)


;; ADR-DOE-HY-001: statement-position bind guard --------------------------------
;;
;; defk/do!/defp/deftest の本体で、束縛されず statement 位置に置かれた式が
;; Program(DoExpr)/EffectBase に評価された場合、それは「作られたが決して走らない」
;; 計算であり、ほぼ確実にバグである(silent-passthrough)。マクロは statement 位置の
;; 式形フォームを _guard-statement-value でラップし、初回実行で決定的に fail させる。
;; auto-bind は採用しない(ADR-DOE-HY-001 R2)。エラーメッセージは書き手エージェント
;; 向けの修正プロンプトである(R3)。

(setv _STATEMENT-FORM-HEADS
  #{"setv" "setx" "import" "require" "assert" "del" "raise" "return" "global"
    "nonlocal" "yield" "await" "for" "while" "with" "with/a" "when" "unless"
    "if" "do" "cond" "try" "let" "match" "defn" "defn/a" "defclass" "defmacro"
    "quote" "quasiquote" "unquote" "annotate" "<-" "!" "When" "lazy" "lazy-val"
    "lazy-var" "set!"})

(defn _guard-statement-value [value owner line source]
  "ADR-DOE-HY-001 R1 の実行時 guard。statement 位置で評価された値が
   Program(DoExpr)/EffectBase なら RuntimeError を送出する。それ以外の値は
   そのまま返す(R4: 非 Program 文は無傷)。"
  (import doeff [DoExpr EffectBase])
  (when (isinstance value #(DoExpr EffectBase))
    (raise (RuntimeError
             (+ owner " (line " (str line) "): statement-position expression evaluated to an "
                "unperformed " (. (type value) __name__) " — a bare Program/EffectBase in "
                "statement position never runs. "
                "Fix: bind it — (<- _ " source ") — or make it the final body expression. "
                "[ADR-DOE-HY-001]"))))
  value)

(defn _doeff-check-program-return [value message mode]
  "Check defp/defpp's return-color contract. Returns False for use in assert."
  (setv is-program (inspect.isgenerator value))
  (when (and (= mode "reject") is-program)
    (raise (TypeError message)))
  (when (and (= mode "require") (not is-program))
    (raise (TypeError message)))
  False)

(defn _install-guard-globals [wrapped [runtime-globals None]]
  "Install generated-function runtime helpers in the defining module globals.

   Macro expansion can run in a class body, whose namespace is not part of a
   method's LEGB lookup. doeff.do.do uses functools.wraps, so descend through
   __wrapped__ to the generated function and seed its module globals once.
   setdefault preserves an explicit module-level binding."
  (setv target wrapped)
  (while (hasattr target "__wrapped__")
    (setv target (. target __wrapped__)))
  (when (not (hasattr target "__globals__"))
    (raise (TypeError
             "doeff-hy generated callable has no reachable __globals__")))
  (setv globals-dict (. target __globals__))
  (.setdefault globals-dict "_guard_performed" _guard-performed)
  (.setdefault globals-dict "_guard_statement_value" _guard-statement-value)
  (.setdefault globals-dict "_doeff_check_program_return" _doeff-check-program-return)
  (for [#(name value) (.items (or runtime-globals {}))]
    (.setdefault globals-dict name value))
  wrapped)

(defn _wrap-statement-guard [form owner]
  "statement 位置の式形フォーム(関数呼び出し・シンボル)を _guard-statement-value で
   ラップして返す。文形式(setv/for/with 等、_STATEMENT-FORM-HEADS)と非式フォームは
   そのまま返す。owner はエラーメッセージ用の defk/do!/defp/deftest 名。"
  (setv head (if (and (isinstance form hy.models.Expression) (> (len form) 0))
                 (str (get form 0))
                 None))
  (setv eligible
    (or (isinstance form hy.models.Symbol)
        (and (is-not head None) (not (in head _STATEMENT-FORM-HEADS)))))
  (if eligible
      (do
        (setv line (or (getattr form "start_line" None) -1))
        (setv source
          (try (.lstrip (hy.repr form) "'")
               (except [Exception] (str form))))
        ;; 型検査のための展開: 値の型を `reveal_type` でも出す。pyright は型の分からない
        ;; (Unknown)値でも deprecated の overload(macros.pyi)を選ぶので、doeff-hy-check は
        ;; 同じ位置の型が Unknown / Any なら赤を落とす(走らない Program と決めつけない)。
        (if (_static-view?)
            `(_guard-statement-value (reveal_type ~form) ~(str owner) ~line ~source)
            `(_guard-statement-value ~form ~(str owner) ~line ~source)))
      form))


(defn _helper-imports []
  "macro の展開が参照する `do` と実行時の補助の import(1 点)。

   実行時: doeff の `do`(`_doeff_do`)・`_install-guard-globals` と、本体が名前で呼ぶ補助(`_guard-performed` など)。
   補助は `_install-guard-globals` が関数の globals へも入れる(class の本体で展開した
   defk の method からも見えるように)ので、import は pyright のための宣言を兼ねる
   (型は macros.pyi)。
   型検査のための展開(`_static-view?`)では、加えて型付きの `do` と `_doeff_perform` を doeff_hy.static_types から取る(`_doeff_do` を上書きする)。"
  (if (_static-view?)
      `(do
         (import doeff-hy.static-types [do :as _doeff_do _doeff-perform])
         (import doeff-hy.macros [_install-guard-globals _guard-performed
                                  _guard-statement-value _doeff-check-program-return]))
      `(do
         (import doeff.do [do :as _doeff_do])
         (import doeff-hy.macros [_install-guard-globals _guard-performed
                                  _guard-statement-value _doeff-check-program-return]))))

(defn _result-symbol [origin]
  "本体の結果を指す `_contract_result`。位置は最後の式から取る(`return` と戻り値の型の
   赤が、defk の頭ではなく最後の式を指すように)。"
  (.replace (hy.models.Symbol "_contract_result") origin))

(defn _result-binding [last-form return-source]
  "`(setv _contract_result 最後の式)`。契約に戻り値の型が在れば変数に注記する。"
  (if (is return-source None)
      `(setv ~(_result-symbol last-form) ~last-form)
      `(setv (annotate ~(_result-symbol last-form) ~(hy.models.String return-source))
             ~last-form)))

(defn _build-fn-with-contracts [decorators name params pre-checks post-checks real-body]
  "Build a defn form with pre/post assertion wrappers.
   Works for both plain functions (deff) and generator/kleisli functions (defk).
   Kleisli functions (decorator `_doeff_do`) additionally guard against a bare
   unperformed effect as the last form (see `_guard-performed`).
   A leading docstring is emitted BEFORE the pre-condition asserts so it stays
   the first statement and becomes the function's __doc__ (a lone string body
   is left in place — it is the return value, not a docstring)."
  (setv docstring-forms [])
  (when (and (> (len real-body) 1)
             (isinstance (get real-body 0) hy.models.String))
    (setv docstring-forms [(get real-body 0)])
    (setv real-body (cut real-body 1 None)))
  (setv kleisli? (any (gfor d decorators (= (str d) "_doeff_do"))))
  (setv pre-code (_contract-code pre-checks name "pre-condition" kleisli?))
  ;; 契約の型を注記へ(引数・deff の戻り値)。defk は生成器なので戻り値そのものには
  ;; 注記せず、結果を入れる局所変数 `_contract_result` に T を注記する(`_result-binding`)
  ;; — 生成器かどうか(本体に yield が在るか)を macro が判定せずに、最後の式の型を T と
  ;; 突き合わせられる。局所変数の注記は実行時に評価されない。
  (setv params (_annotate-params params (_contract-types pre-checks))
        return-source (.get (_contract-types post-checks) "%"))
  (setv head (if (or kleisli? (is return-source None))
                 name
                 `(annotate ~name ~(hy.models.String return-source))))
  (setv guard-stmt
    (if kleisli?
        `(_guard-performed _contract_result ~(str name))
        `(do)))
  (if post-checks
      (let [post-asserts (_contract-code post-checks name "post-condition" kleisli?)
            ;; ADR-DOE-HY-001: kleisli 本体の statement 位置を guard(最終式=返り値は対象外)
            init-forms (if kleisli?
                           (lfor f (cut real-body 0 -1) (_wrap-statement-guard f name))
                           (list (cut real-body 0 -1)))
            last-form (get real-body -1)]
        `(defn ~decorators ~head ~params
           ~@docstring-forms
           ~@pre-code
           ~@init-forms
           ~(_result-binding last-form return-source)
           ~guard-stmt
           (let [% _contract_result]
             ~@post-asserts)
           ~(_result-symbol last-form)))
      `(defn ~decorators ~head ~params
         ~@docstring-forms
         ~@pre-code
         ~@real-body)))


;; ---------------------------------------------------------------------------
;; val / var / lazy val / lazy var / session val / session var と := (ADR-DOE-HY-006)
;; ---------------------------------------------------------------------------
;;
;; 本体の書き換えの定義点は doeff_hy/binding_forms.py の rewrite-body 1 つ。defk・deftest・defhandler の
;; 節(handle.hy)がここを通す。展開を止める誤りは SyntaxError、止めない所見(setv の使用・束縛し直し・
;; 旧い lazy)は doeff-hy-check が集める(static_view.report-findings)。

(import doeff-hy.binding-forms [rewrite-body BodyKind ModuleBindings ModuleNames module-declaration])
(import doeff-hy.static-view [report-findings])
(import hy.scoping [ScopeGlobal])

(defn _module-bindings [compiler]
  "この file の compile の間だけ生きる、module の直下の val / var / lazy val の名簿(Hy の compiler に付ける)。
   compiler が無い展開(hy.macroexpand など)では、その場かぎりの空の名簿。"
  (if (is compiler None)
      (ModuleBindings)
      (do
        (setv found (getattr compiler "_doeff_module_bindings" None))
        (when (is found None)
          (setv found (ModuleBindings))
          (setattr compiler "_doeff_module_bindings" found))
        found)))

(defn _module-names [compiler]
  "defk・deftest・defhandler の本体へ渡す、同じ file で前に宣言した module の lazy val / var の名前。"
  (if (is compiler None) (ModuleNames) (.names (_module-bindings compiler))))

(defn _rewrite-bindings [forms owner kind params [sessions #()] [module None]]
  "本体の宣言・:=・lazy の参照を書き換え、所見を doeff-hy-check へ渡して、書き換えた本体を返す。
   展開の時に呼ぶ helper なので defn(defk にできない)。"
  (setv rewritten (rewrite-body forms
                                :owner owner
                                :kind kind
                                :params params
                                :sessions sessions
                                :module (if (is module None) (ModuleNames) module)
                                :static (_static-view?)
                                :expand-bangs _expand-bangs))
  (report-findings (. rewritten findings))
  (list (. rewritten forms)))

(defn _module-level [compiler head args]
  "module の直下の (val …) / (var …) / (lazy val …) を展開する(lazy var / session は案内つきの誤り)。defk・deftest・defhandler の
   本体の中の宣言は本体の macro が先に書き換えるので、ここへ来るのは module の直下か、書き換えの届かない
   defn / fn / class の中(誤り)。"
  (setv form (if (is compiler None)
                 (hy.models.Expression [(hy.models.Symbol head) #* args])
                 (. compiler this)))
  (when (and (is-not compiler None) (not (isinstance (. compiler scope) ScopeGlobal)))
    (raise (SyntaxError (+ "\n(" head " …) (line " (str (getattr form "start_line" "?")) "): "
                           "defk・deftest・defhandler の節の本体か、module の直下にだけ書けます"
                           "(defn・fn・class・let の中には書けません — 関数は defk で書きます)。"
                           " [ADR-DOE-HY-006]\n"))))
  (module-declaration (_module-bindings compiler) form (_static-view?)))

(defmacro val [_hy-compiler #* args]
  "module の直下の一度だけの束縛 (val 名前 式)。defk・deftest・defhandler の節の本体の中では本体の macro が扱う。"
  (_module-level _hy-compiler "val" args))

(defmacro var [_hy-compiler #* args]
  "module の直下の書き換えられる束縛 (var 名前 式)。module の直下の書き換えは setv(Hy では := が macro を通らない)。"
  (_module-level _hy-compiler "var" args))

(defmacro lazy [_hy-compiler #* args]
  "module の直下の (lazy val 名前 式) — 効果を使わない式だけ。初めて使った時に 1 回だけ評価して覚える。"
  (_module-level _hy-compiler "lazy" args))

(defmacro session [_hy-compiler #* args]
  "(session val …) / (session var …) は defhandler の直下にだけ書ける(ここへ来たら誤りの案内)。"
  (_module-level _hy-compiler "session" args))


;; ---------------------------------------------------------------------------
;; deff — defn with :pre/:post contracts
;; ---------------------------------------------------------------------------

(defmacro deff [name params #* body]
  "Define a function with :pre/:post contracts.

   (deff my-fn [x y]
     {:pre [(: x int) (: y str)]
      :post [(: % list)]}
     (list (range x)))

   :pre and :post are REQUIRED.
   (: name Type) is shorthand for (isinstance name Type).
   Arbitrary validation expressions are also allowed in the same list."
  (_warn-defk-in-hyp "deff" name)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
  (when (is pre-checks None)
    (raise (SyntaxError (.format "
deff {name}: {{:pre [...]}} is required.

  Correct usage:

    (deff {name} [x y]
      {{:pre [(: x int) (: y str)]          ;; type checks
             (> x 0)]                        ;; arbitrary validation
       :post [(: % list)]}}                  ;; return type
      (list (range x)))

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (.format "
deff {name}: {{:post [...]}} is required.

  Correct usage:

    (deff {name} [x y]
      {{:pre [(: x int) (: y str)]
       :post [(: % list)]}}                  ;; return type
      (list (range x)))

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (_validate-pre-type-checks name params pre-checks)
  (_validate-post-type-check name post-checks)
  (locate-synthesized
    (_build-fn-with-contracts [] name params pre-checks post-checks real-body)))


;; ---------------------------------------------------------------------------
;; defk — kleisli with :pre/:post contracts + bang expansion
;; ---------------------------------------------------------------------------

(defmacro defk [_hy-compiler name params #* body]
  "Define a kleisli function (@do decorator) with :pre/:post contracts.
   Supports ! (bang) inline bind: (! expr) is rewritten IN PLACE to
   (yield expr), preserving the written evaluation position (conditionality,
   short-circuit, order, exception context). [ADR-DOE-HY-003]
   No extra imports needed — macro injects its own runtime deps.

   (defk my-fn [x y]
     {:pre [(: x Asset)]
      :post [(: % SegmentSelection)]}
     (<- result (some-effect))
     (return result))

   :pre and :post are REQUIRED. (: name Type) is shorthand for (isinstance name Type).
   Arbitrary validation expressions are also allowed in the same list.

   With bang:
   (defk my-fn [x y]
     {:pre [(: x int) (: y int)]
      :post [(: % int)]}
     (k1 (! (k2 x)) (! (k3 y))))"
  ;; Warn if defk is used in .hyp file
  (_warn-defk-in-hyp "defk" name)
  ;; Reject handler-like signatures early — these should use defhandler
  (_reject-handler-signature name params)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
  (when (is pre-checks None)
    (raise (SyntaxError (.format "
defk {name}: {{:pre [...]}} is required.

  Correct usage:

    (defk {name} [x y]
      {{:pre [(: x Asset) (: y str)]        ;; type checks
             (> (len y) 0)]                  ;; arbitrary validation
       :post [(: % Result)]}}               ;; return type
      (<- result (some-effect x))
      result)

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (.format "
defk {name}: {{:post [...]}} is required.

  Correct usage:

    (defk {name} [x y]
      {{:pre [(: x Asset) (: y str)]
       :post [(: % Result)]}}               ;; return type
      (<- result (some-effect x))
      result)

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  ;; Validate type coverage: every param needs (: param Type), post needs (: % Type)
  (_validate-pre-type-checks name params pre-checks)
  (_validate-post-type-check name post-checks)
  ;; val / var / lazy val / lazy var / := の書き換えと、旧い lazy / lazy-val / lazy-var / set! の拒否
  ;; (ADR-DOE-HY-006)。__doeff_body__ には書いたままの本体を残す。
  (setv written-body real-body)
  (setv real-body (_rewrite-bindings real-body (+ "defk " (str name)) BodyKind.DEFK
                                     (_extract-param-names params)
                                     :module (_module-names _hy-compiler)))
  ;; Expand bangs in the real body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ "defk " (str name)))))
  (setv fn-form (_build-fn-with-contracts ['_doeff_do] name params pre-checks post-checks expanded-forms))
  (locate-synthesized `(do
     ~(_helper-imports)
     ~fn-form
     (_install-guard-globals ~name)
     (setattr ~name "__doeff_body__" '~written-body)
     (setattr ~name "__doeff_args__" '~params)
     (setattr ~name "__doeff_name__" ~(str name)))))


;; ---------------------------------------------------------------------------
;; fnk — anonymous kleisli (lambda that returns DoExpr)
;; ---------------------------------------------------------------------------

(defmacro fnk [params #* body]
  "Anonymous kleisli function. Like fn but returns a DoExpr.
   Supports <- (effect bind) and ! (bang) inline bind.
   No extra imports needed — macro injects its own runtime deps.

   (fnk [x] (* x 2))

   With effects:
   (fnk [acc x]
     (<- enriched (enrich x))
     (+ acc enriched))

   With bang:
   (fnk [x y] (+ (! (k1 x)) (! (k2 y))))"
  ;; Expand bangs in the body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms (lfor form body (_expand-bangs form "fnk")))
  (locate-synthesized
    `(do ~(_helper-imports)
         (fn [~@params] ((_doeff_do (fn [] (do ~@expanded-forms))))))))


;; ---------------------------------------------------------------------------
;; do! — effectful let (returns a Program)
;; ---------------------------------------------------------------------------

(defmacro do! [#* forms]
  "Effectful let — returns a Program (generator) that sequences effect bindings.

   (do!
     (<- x (some-effect))
     (<- y (another-effect x))
     (+ x y))

   Returns a Program object. Caller decides how to use it:
     (<- result (do! ...))   ; yield it inside a defk/deff
     (setv p (do! ...))      ; store as a Program value

   Supports ! (bang) inline bind:
     (do! (f (! (g x)) (! (h y))))
   rewrites each (! expr) in place to (yield expr). [ADR-DOE-HY-003]

   Supports :pre/:post contracts with (: name Type) shorthand:

   (do!
     {:pre [(: url str)]
      :post [(: % dict)]}
     (<- resp (http-get url))
     (.json resp))"
  (setv #(pre-checks post-checks real-forms) (_extract-contracts forms))
  ;; Expand bangs in each form — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms (lfor form real-forms (_expand-bangs form "do!")))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms "do!"))
  (setv pre-code (_contract-code pre-checks "do!" "pre-condition" True)
        expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement — ADR-DOE-HY-001 guard(式形のみラップ)
                       (_wrap-statement-guard (get bind 1) "do!")
                       ;; Effect binding — yield (typed bind keeps its isinstance)
                       (let [#(name tp expr) (_bind-parts bind)]
                         (_bind-yield name tp expr)))))
  (locate-synthesized (if post-checks
      (let [post-asserts (_contract-code post-checks "do!" "post-condition" True)]
        `(do ~(_helper-imports)
             ((_install-guard-globals
                (_doeff-do (fn []
                  ~@pre-code
                  ~@expanded
                  ~(_result-binding body-expr (.get (_contract-types post-checks) "%"))
                  (_guard-performed _contract_result "do!")
                  (let [% _contract_result]
                    ~@post-asserts)
                  (return ~(_result-symbol body-expr))))))))
      `(do ~(_helper-imports)
           ((_install-guard-globals
              (_doeff-do (fn []
                ~@pre-code
                ~@expanded
                ~(_result-binding body-expr None)
                (_guard-performed _contract_result "do!")
                (return ~(_result-symbol body-expr))))))))))


;; ---------------------------------------------------------------------------
;; <- — perform effect with optional type contract
;; ---------------------------------------------------------------------------

(defmacro <- [#* args]
  "Perform an effect. Bind result if name given, optional type contract.
   (<- x (Ask \"key\"))              → (setv x (yield (Ask \"key\")))
   (<- x Type (Ask \"key\"))         → bind + isinstance assertion
   (<- (slog ...))                   → (yield (slog ...))
   The expansion is `_bind-yield` — the same definition point the body
   expanders (do! / defp / deftest / for/do / defhandler) use, so a typed bind
   carries the isinstance guarantee wherever it is written."
  (setv parts (_bind-parts #('<- #* args)))
  (when (is parts None)
    (raise (SyntaxError (+ "<-: expected (<- expr) / (<- name expr) / (<- name Type expr), got "
                           (str (len args)) " arguments"))))
  (setv #(name tp expr) parts)
  (locate-synthesized (_bind-yield name tp expr)))


;; ---------------------------------------------------------------------------
;; Internal: binding helpers
;; ---------------------------------------------------------------------------

(defn _is-bind [form]
  "Check if a form is a (<- ...) binding."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (= (str (get form 0)) "<-")))

(defn _is-iterate [expr]
  "Check if expr is (Iterate ...) or (From ...)."
  (and (isinstance expr hy.models.Expression)
       (> (len expr) 0)
       (in (str (get expr 0)) #{"Iterate" "From"})))

(defn _is-when [form]
  "Check if form is (When ...)."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (= (str (get form 0)) "When")))

(defn _iterate-arg [expr]
  "Extract the items arg from (Iterate items)."
  (get expr 1))

(defn _bind-parts [form]
  "Extract (name, Type, expr) from a (<- ...) form.
     (<- expr)           → (None, None, expr)
     (<- name expr)      → (name, None, expr)
     (<- name Type expr) → (name, Type, expr)"
  (cond
    (= (len form) 2) #(None None (get form 1))
    (= (len form) 3) #((get form 1) None (get form 2))
    (= (len form) 4) #((get form 1) (get form 2) (get form 3))))

(defn _bind-yield [name tp expr]
  "Single definition point for the yield form of an effect binding.
   Used by the <- macro AND by every body expander that pre-parses <- forms
   (do! / defp / deftest / for/do / traverse / defhandler clauses), so the type contract of a
   4-element bind is honored at runtime wherever it is written — the shared
   quality checker (dotfiles agent/quality/hy_dsl.py effect_bind) projects the
   same isinstance and relies on this guarantee existing.
     (<- expr)           → (yield expr)
     (<- name expr)      → (setv name (yield expr))
     (<- name Type expr) → (do (setv name (yield expr))
                               (assert (isinstance name Type) \"expected Type, got <actual>\"))"
  (cond
    ;; 型検査のための展開(doeff_hy/static_view.py): x の型 = expr の答えの型。
    ;; Python の `@effectful` の `x = perform(e)`(docs/24-effectful-perform.md)と同じ形で、
    ;; `_doeff_perform` の宣言は static_types.pyi。`(<- x T e)` は x に T を注記するので、
    ;; e の答えの型が T に入らなければ pyright が赤にする。e は 1 回だけ現れる。
    ;; yield は出さない(型は yield に頼らない — 2026-09-23 に agora-controllers の
    ;; controllers/worker で yield を残した形と赤が同じ 57 件であることを確かめた)。
    ;; 実行時の展開はこの枝を通らない(実行時の `(yield e)` は @effectful の書き換えの結果と同じ)。
    (_static-view?)
      ;; import を形の中に持つ(defhandler の節・利用者の defn など、`_helper-imports` を
      ;; 出さない所の `<-` でも名前が解けるように。静的な展開は実行しないので費用は無い)。
      (let [bound `(do (import doeff-hy.static-types [_doeff-perform])
                       (_doeff-perform ~expr))
            source (when (is-not tp None) (_type-source tp))]
        (cond
          (is name None) bound
          (or (is source None) (not (isinstance name hy.models.Symbol)))
            `(setv ~name ~bound)
          True `(setv (annotate ~name ~(hy.models.String source)) ~bound)))
    (is name None) `(yield ~expr)
    (is tp None) `(setv ~name (yield ~expr))
    True `(do
            (setv ~name (yield ~expr))
            (assert (isinstance ~name ~(_runtime-type tp))
                    (+ ~(+ "expected " (str tp) ", got ") (. (type ~name) __name__))))))


;; ---------------------------------------------------------------------------
;; Internal: parse do body
;; ---------------------------------------------------------------------------

(defn _parse-do-body [forms [macro-name "do"]]
  "Split forms into (bindings, body-expr).
   (<- ...) and (When ...) forms are bindings.
   Last non-binding form is body. Other non-binding forms are plain statements
   (setv, when, for, etc.) emitted as-is into the function body."
  (setv bindings []
        body-expr None)
  ;; Collect all forms; track the last non-bind form as body candidate
  (setv all-forms (list forms))
  ;; Find the last non-bind form (= body expression)
  (setv body-idx None)
  (for [#(i form) (enumerate all-forms)]
    (when (not (or (_is-bind form) (_is-when form)))
      (setv body-idx i)))
  (when (is body-idx None)
    (raise (SyntaxError (.format "
{macro}: missing body expression — only bindings found.

  The last form must be a non-binding expression (the return value):

    ({macro}
      (<- x (some-effect))     ;; ← binding
      (<- y (other-effect x))  ;; ← binding
      (+ x y))                 ;; ← body expression (required)

  Every (<- ...) and (When ...) is a binding. You need at least one
  plain expression at the end as the result.
" :macro macro-name))))
  ;; Partition: everything before body-idx goes to bindings, body-idx is body
  (for [#(i form) (enumerate all-forms)]
    (cond
      (= i body-idx) (setv body-expr form)
      (_is-bind form) (.append bindings form)
      (_is-when form) (.append bindings form)
      ;; Non-bind, non-body form → plain statement (setv, when, for, etc.)
      ;; Mark with :plain tag so expansion emits it as-is
      True (.append bindings #("__plain__" form))))
  #(bindings body-expr))


;; ---------------------------------------------------------------------------
;; traverse — applicative traverse as effect (CPS-converted Iterate)
;; ---------------------------------------------------------------------------

(defn _gen-traverse-body [bindings body-expr]
  "Generate the CPS-converted body for traverse/for-do.
   Finds Iterate/From bindings and nests Traverse effects.
   Recognizes (When pred) as a guard — emits Skip when falsy.
   Non-Iterate bindings become yield expressions inside the inner defk."
  (if (not bindings)
      body-expr
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)]
        ;; Check if this binding is a (When ...) guard
        (if (_is-when bind)
            (let [pred-expr (get bind 1)
                  ;; In-place bang expansion: (When (! (validate x))) →
                  ;; (When (yield (validate x))) — evaluated at guard position
                  ;; [ADR-DOE-HY-003]
                  rewritten-pred (_expand-bangs pred-expr "for/do")
                  inner (_gen-traverse-body rest body-expr)]
              `(do
                 (when (not ~rewritten-pred)
                   (yield (_doeff_traverse_Skip)))
                 ~inner))
            ;; Regular binding
            (let [#(name tp expr) (_bind-parts bind)]
              (if (_is-iterate expr)
                  ;; CPS: wrap rest + body into a defk, emit Traverse effect
                  ;; NOTE: does NOT yield — the outer <- / defk handles yield
                  (let [items (_iterate-arg expr)
                        ;; Extract optional :label from (Iterate items :label "name")
                        label (if (>= (len expr) 4) (get expr 3) None)
                        inner-body (_gen-traverse-body rest body-expr)
                        param (if (is name None)
                                  (hy.models.Symbol "_unused")
                                  name)]
                    (if (is-not label None)
                        `(_doeff_traverse_Traverse
                           (fn [~param] ((_doeff_do (fn [] (do ~inner-body)))))
                           ~items
                           :label ~label)
                        `(_doeff_traverse_Traverse
                           (fn [~param] ((_doeff_do (fn [] (do ~inner-body)))))
                           ~items)))
                  ;; Non-Iterate: regular bind (typed bind keeps its isinstance)
                  (let [inner (_gen-traverse-body rest body-expr)]
                    `(do ~(_bind-yield name tp expr) ~inner))))))))

(defmacro traverse [#* forms]
  "Applicative traverse — batch processing with handler-injected strategy.
   Alias for for/do. Prefer for/do with From in new code.

   (traverse
     (<- x (Iterate items :label \"extract\"))
     (<- y (some-effect x))
     [x y])

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])"
  (setv #(bindings body-expr) (_parse-do-body forms "traverse"))
  (locate-synthesized (_gen-traverse-body bindings body-expr)))


(defmacro for/do [#* forms]
  "Collection comprehension — From/When/bind with handler-injected strategy.

   From: generator bind (like SQL FROM / Haskell <- on list)
   When: guard (like SQL WHERE / Haskell guard)
   <-:  effect bind (kleisli)
   Last expression: yield value (like SQL SELECT / Haskell return)

   (for/do
     (<- item (From items :label \"extract\"))
     (<- ok (validate item))
     (When ok)
     (<- result (process item))
     result)

   Multiple generators (nested):
   (for/do
     (<- item (From items :label \"outer\"))
     (When (active? item))
     (<- sub (From (children item) :label \"inner\"))
     (When (valid? sub))
     (<- result (process item sub))
     result)

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
     (import doeff_traverse [Skip :as _doeff_traverse_Skip])"
  (setv #(bindings body-expr) (_parse-do-body forms "for/do"))
  (locate-synthesized (_gen-traverse-body bindings body-expr)))


;; ---------------------------------------------------------------------------
;; Internal: ! (bang) inline bind expansion — evaluation-position preserving
;; [ADR-DOE-HY-003]
;; ---------------------------------------------------------------------------

(defn _is-bang [form]
  "Check if form is (! expr)."
  (and (isinstance form hy.models.Expression)
       (>= (len form) 2)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "!")))

;; Forms that own their own do-context (or treat their body as data).
;; The outer expander must not cross these boundaries: the innermost
;; do-context macro expands its own bangs. [ADR-DOE-HY-003 R4]
;; `handle` is special-cased in the walk: its first argument (the wrapped
;; program) belongs to the ENCLOSING do-context, only its clauses are opaque.
(setv _BANG-OPAQUE-HEADS
  #{"for/do" "traverse" "fnk" "do!" "defhandler"
    "defk" "deff" "defp" "defpp" "deftest" "defmcp-tool"
    "defmacro" "quote" "quasiquote"
    ;; doeff-validation の validate: 各 check を別々の小さな Program に包み、check の引数の (! …) を
    ;; その項目の中で実行する(外側の本体で先に実行すると、項目の独立と失敗の収集が崩れる)。
    "validate"})

;; Python compiles comprehensions to a separate scope where yield is illegal
;; (SyntaxError since 3.8). A bang here cannot preserve its written position.
(setv _BANG-COMPREHENSION-HEADS #{"lfor" "gfor" "dfor" "sfor"})

;; A nested plain function/class is a separate scope: an inline yield there
;; would silently turn it into a generator instead of performing the effect
;; in the enclosing do-context.
(setv _BANG-FN-HEADS #{"fn" "fn/a" "defn" "defn/a" "defclass"})

(defn _bang-node-line [node]
  (or (getattr node "start_line" None) -1))

(defn _bang-node-src [node]
  (try (.lstrip (hy.repr node) "'")
       (except [Exception] (str node))))

(defn _bang-arity-msg [owner node]
  (.format "
{owner} (line {line}): (! ...) takes exactly one form, got {n}: {src}

  Correct usage:

    (! (effect-expr))
" :owner owner :line (_bang-node-line node) :n (- (len node) 1)
  :src (_bang-node-src node)))

(defn _bang-comprehension-msg [owner head node]
  (setv inner-src (_bang-node-src (get node 1)))
  (.format "
{owner} (line {line}): (! ...) inside ({head} ...) cannot preserve its written
evaluation position — Python compiles {head} to a separate comprehension scope
where yield is illegal, so the effect cannot be performed at this position.

  Fix — perform the effect before the comprehension and bind the value:

    (<- v {src})
    ({head} x xs (use v x))

  Fix — or rewrite as for/do, the effectful comprehension (per-item effects;
  sequential/parallel strategy is chosen by the handler, not the call site):

    (<- results
      (for/do
        (<- x (From xs))
        (<- r {src})
        r))

  [ADR-DOE-HY-003]
" :owner owner :line (_bang-node-line node) :head head :src inner-src))

(defn _bang-nested-fn-msg [owner head node]
  (setv inner-src (_bang-node-src (get node 1)))
  (.format "
{owner} (line {line}): (! ...) inside a nested ({head} ...) cannot preserve its
written evaluation position — the nested {head} is a separate scope, so an
inline bind there would silently turn it into a generator instead of performing
the effect in the enclosing do-context.

  Fix — perform the effect before the {head} and close over the value:

    (<- v {src})
    (fn [x] (use v x))

  Fix — or make the nested function an effectful kleisli with fnk (it returns
  a Program the caller must bind):

    (fnk [x] (use (! {src}) x))

  [ADR-DOE-HY-003]
" :owner owner :line (_bang-node-line node) :head head :src inner-src))

(defn _expand-bangs [form [owner "do-context"]]
  "Rewrite every (! expr) IN PLACE to (yield expr), preserving the written
   evaluation position: conditionality (if/when/cond), short-circuit (and/or),
   left-to-right order within a statement, exception context (try), and
   element positions inside dict/list/set/tuple/f-string literals.
   Returns the rewritten form. [ADR-DOE-HY-003 R1/R2]

   Positions where yield is syntactically impossible raise SyntaxError at
   expansion time with a fix prompt (R3): comprehensions (lfor/gfor/dfor/sfor)
   and nested fn/fn/a/defn/defn/a/defclass bodies.

   Forms that own their own do-context (for/do, traverse, fnk, do!, handle,
   nested defk/deff/defp/deftest/..., defmacro, quote/quasiquote) are opaque —
   their own macro expands their bangs (R4)."
  (defn walk [node ctx]
    (cond
      (_is-bang node)
        (do
          (when (!= (len node) 2)
            (raise (SyntaxError (_bang-arity-msg owner node))))
          (when (is-not ctx None)
            (setv #(kind head) ctx)
            (raise (SyntaxError
                     (if (= kind "comprehension")
                         (_bang-comprehension-msg owner head node)
                         (_bang-nested-fn-msg owner head node)))))
          `(yield ~(walk (get node 1) ctx)))

      (isinstance node hy.models.Expression)
        (do
          (setv head
            (when (and (> (len node) 0) (isinstance (get node 0) hy.models.Symbol))
              (str (get node 0))))
          (cond
            (and (is-not head None) (in head _BANG-OPAQUE-HEADS))
              node
            ;; handle: the wrapped program (arg 1) is evaluated in the
            ;; ENCLOSING do-context — walk it. The clauses own their own
            ;; do-context — opaque.
            (and (= head "handle") (>= (len node) 2))
              (hy.models.Expression
                [(get node 0) (walk (get node 1) ctx) #* (cut node 2 None)])
            True
              (do
                (setv new-ctx
                  (cond
                    (and (is-not head None) (in head _BANG-COMPREHENSION-HEADS))
                      #("comprehension" head)
                    (and (is-not head None) (in head _BANG-FN-HEADS))
                      #("fn" head)
                    True ctx))
                (hy.models.Expression (lfor child node (walk child new-ctx))))))

      (isinstance node hy.models.FString)
        (hy.models.FString (lfor child node (walk child ctx))
                           :brackets (. node brackets))

      (isinstance node hy.models.FComponent)
        (hy.models.FComponent (lfor child node (walk child ctx))
                              :conversion (. node conversion))

      (isinstance node #(hy.models.List hy.models.Tuple hy.models.Set hy.models.Dict))
        ((type node) (lfor child node (walk child ctx)))

      True node))

  (walk form None))


;; ---------------------------------------------------------------------------
;; defp / defpp — define a Program constant with implicit do context
;; ---------------------------------------------------------------------------

(defn _defp-post-required-msg [macro-name name]
  (.format "
{macro} {name}: {{:post [...]}} is required.

  Correct usage:

    ({macro} {name}
      {{:post [(: % ExportResult)]}}         ;; return type check
      (<- data (load-data :path \"data.csv\"))
      (<- result (process data))
      (export result))

  (: name Type) expands to (isinstance name Type).
  % binds to the program's return value in :post checks.
" :macro macro-name :name name))

(defn _defp-program-return-msg [name]
  (.format "
{name}: return value is a Program (generator), but defp defines Program[T].

  The last expression in your defp body returned a Program instead of a plain
  value. This usually means you forgot to bind it with (<- ...):

    ;; WRONG — returns Program, not the value:
    (defp {name}
      {{:post [(: % str)]}}
      (some-effect))              ;; ← this is a Program, not a str

    ;; CORRECT — bind the Program to get the value:
    (defp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))   ;; ← binds the effect result
      result)                     ;; ← returns the plain value

  If you intentionally want Program[Program[T]], use defpp instead:

    (defpp {name}
      {{:post [...]}}
      ...)
" :name name))

(defn _defpp-not-program-return-msg [name]
  (.format "
{name}: return value is NOT a Program, but defpp defines Program[Program[T]].

  defpp requires the last expression to return a Program (generator).
  If you don't need Program[Program[T]], use defp instead:

    ;; WRONG — returns a plain value from defpp:
    (defpp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))
      result)                       ;; ← plain value, not a Program

    ;; CORRECT — return a Program from defpp:
    (defpp {name}
      {{:post [(inspect.isgenerator %)]}}
      (<- config (load-config))
      (build-pipeline config))      ;; ← returns a Program

    ;; Or just use defp if you want Program[T]:
    (defp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))
      result)
" :name name))

(defn _build-defp [macro-name name body * [program-return-mode "reject"]]
  "Shared implementation for defp/defpp.
   program-return-mode: 'reject' (defp) | 'require' (defpp)"
  (_enforce-no-defp-in-hyk macro-name name)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
  (when (is-not pre-checks None)
    (raise (SyntaxError (.format "
{macro} {name}: :pre is not allowed — {macro} has no parameters.

  Remove {{:pre [...]}} and keep only {{:post [...]}}:

    ({macro} {name}
      {{:post [(: % ExportResult)]}}
      ...)
" :macro macro-name :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (_defp-post-required-msg macro-name name))))
  ;; Inject Program-return guard into :post
  (cond
    (= program-return-mode "reject")
      (do
        (setv guard-msg (_defp-program-return-msg (str name)))
        (setv post-checks (+ [
          (hy.models.Expression
            [(hy.models.Symbol "not")
             (hy.models.Expression
               [(hy.models.Symbol "_doeff_check_program_return") (hy.models.Symbol "%")
                (hy.models.String guard-msg) (hy.models.String "reject")])])]
          (list post-checks))))
    (= program-return-mode "require")
      (do
        (setv guard-msg (_defpp-not-program-return-msg (str name)))
        (setv post-checks (+ [
          (hy.models.Expression
            [(hy.models.Symbol "not")
             (hy.models.Expression
               [(hy.models.Symbol "_doeff_check_program_return") (hy.models.Symbol "%")
                (hy.models.String guard-msg) (hy.models.String "require")])])]
          (list post-checks)))))
  ;; Inline do! expansion: in-place bang rewrite [ADR-DOE-HY-003], parse,
  ;; emit generator with contracts
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ macro-name " " (str name)))))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms macro-name))
  (setv expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement — ADR-DOE-HY-001 guard(式形のみラップ)
                       (_wrap-statement-guard (get bind 1) (str name))
                       ;; Effect binding — yield (typed bind keeps its isinstance)
                       (let [#(bname tp expr) (_bind-parts bind)]
                         (_bind-yield bname tp expr)))))
  (setv post-asserts (lfor check post-checks
                       (_expand-check check name "post-condition")))
  `(do
     ~(_helper-imports)
     (setv ~name
       ((_install-guard-globals
          (_doeff_do (fn []
            ~@expanded
            ~(_result-binding body-expr (.get (_contract-types post-checks) "%"))
            (let [% _contract_result]
              ~@post-asserts)
            (return ~(_result-symbol body-expr)))))))
     ;; Preserve S-expr body directly on Program value (DoExpr has __dict__ via pyclass(dict))
     (setattr ~name "__doeff_body__" '~real-body)
     (setattr ~name "__doeff_name__" ~(str name))
     (setattr ~name "__doeff_module__" __name__)))

(defmacro defp [name #* body]
  "Define a Program[T] constant. Errors if the return value is itself a Program.
   :post is REQUIRED. Use defpp if Program[Program[T]] is intended.

   (defp my-pipeline
     {:post [(: % ExportResult)]}
     (<- data (load-data))
     (<- result (process data))
     result)"
  (locate-synthesized (_build-defp "defp" name body)))

(defmacro defpp [name #* body]
  "Define a Program[Program[T]] constant. Errors if return is NOT a Program.
   :post is REQUIRED.

   (defpp my-meta-program
     {:post [(inspect.isgenerator %)]}
     (<- config (load-config))
     (build-pipeline config))"
  (locate-synthesized (_build-defp "defpp" name body :program-return-mode "require")))


;; ---------------------------------------------------------------------------
;; deftest — effectful test that expands to pytest function
;; ---------------------------------------------------------------------------

(defn _extract-test-meta [body]
  "Parse optional test metadata dict from front of body.
   Supported keys: :interpreters, :params, :env, :marks, :skip-if, :skip-reason.
   Returns #(interpreters params-dict env-dict marks skip-if skip-reason real-body).
   Skips leading docstring if present."
  (setv interpreters None
        params-dict None
        env-dict None
        marks None
        skip-if-expr None
        skip-reason None
        real-body body)
  (setv meta-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv meta-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
  (when (is-not meta-idx None)
    (setv meta-dict (get body meta-idx)
          real-body (+ (cut body 0 meta-idx) (cut body (+ meta-idx 1) None)))
    (for [#(k v) (zip (cut meta-dict None None 2) (cut meta-dict 1 None 2))]
      (when (= (str k) ":interpreters")
        (setv interpreters (list v)))
      (when (= (str k) ":params")
        (setv params-dict v))
      (when (= (str k) ":env")
        (setv env-dict v))
      (when (= (str k) ":marks")
        (setv marks (list v)))
      (when (= (str k) ":skip-if")
        (setv skip-if-expr v))
      (when (= (str k) ":skip-reason")
        (setv skip-reason v))))
  #(interpreters params-dict env-dict marks skip-if-expr skip-reason real-body))

(defmacro deftest [_hy-compiler name #* args]
  "Define an effectful test that expands to a pytest-compatible function.
   The test body uses <- for effect binding, same as defk/defp.
   No :pre/:post contracts — use assert for validation.

   (deftest test-signal
     (<- plan (compute-signal \"2026-04-01\"))
     (assert (> (len plan.orders) 0)))

   With interpreter list (string keys resolved by conftest.py):

   (deftest test-signal-multi
     {:interpreters [\"cllm_test\" \"cllm_sim\"]}
     (<- plan (compute-signal \"2026-04-01\"))
     (assert (> (len plan.orders) 0)))

   With fixture parameters:

   (deftest test-signal-dates [trade-date]
     {:params {\"trade-date\" [\"2026-04-01\" \"2026-03-15\"]}}
     (<- plan (compute-signal trade-date))
     (assert (> (len plan.orders) 0)))

   With env overrides (merged with default env by conftest fixture):

   (deftest test-with-sim-time
     {:interpreters [\"cllm_sim\"]
      :env {\"nakagawa.sim_start_time\" \"2026-04-10T06:00:00+09:00\"}}
     (<- result (my-pipeline))
     (assert result))

   With marks and conditional skip:

   (deftest test-kabu-prices
     {:interpreters [\"cllm_paper\"]
      :marks [\"e2e\" \"slow\"]
      :skip-if (not (can-reach \"plutus\" 18082))
      :skip-reason \"kabuStation unreachable\"}
     (<- result (fetch-prices))
     (assert result))

   Expansion: generates def test_*(doeff_interpreter, ...fixtures...)
   that creates a DoExpr program and passes it to the interpreter."
  ;; Parse optional params list and body
  (setv fixture-params []
        body args)
  (when (and (> (len args) 0) (isinstance (get args 0) hy.models.List))
    (setv fixture-params (list (get args 0))
          body (cut args 1 None)))

  ;; Parse optional metadata dict
  (setv #(interpreters params-dict env-dict marks skip-if-expr skip-reason real-body)
    (_extract-test-meta body))

  ;; val / var / lazy val / lazy var / := の書き換え(ADR-DOE-HY-006)
  (setv real-body (_rewrite-bindings real-body (+ "deftest " (str name)) BodyKind.DEFTEST
                                     (sfor p fixture-params (str p))
                                     :module (_module-names _hy-compiler)))

  ;; Expand bangs in the body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ "deftest " (str name)))))

  ;; Build the generator body: convert <- to yield, plain forms as-is
  (setv gen-body [])
  (for [form expanded-forms]
    (cond
      ;; (<- name expr) → (setv name (yield expr));
      ;; (<- name Type expr) → same + isinstance assert (see _bind-yield)
      (and (_is-bind form) (is-not (_bind-parts form) None))
      (let [#(bname tp expr) (_bind-parts form)]
        (.append gen-body (_bind-yield bname tp expr)))
      ;; Everything else — ADR-DOE-HY-001 guard(式形のみラップ、文はそのまま)
      True
      (.append gen-body (_wrap-statement-guard form (str name)))))

  ;; Build the test function
  (setv fn-params (+ [(hy.models.Symbol "doeff_interpreter")] fixture-params))

  ;; Build the program creation + interpreter call
  (setv fn-body
    (if (is-not env-dict None)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body)))
         :env ~env-dict)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body))))))

  ;; Build the parametrize decorators
  (setv decorators [])

  ;; :interpreters → @pytest.mark.parametrize("doeff_interpreter_name", [...])
  (when (is-not interpreters None)
    (.append decorators
      `(.parametrize (. pytest mark) "doeff_interpreter_name"
         ~(hy.models.List interpreters))))

  ;; :params → @pytest.mark.parametrize for each key
  (when (is-not params-dict None)
    (for [#(k v) (zip (cut params-dict None None 2) (cut params-dict 1 None 2))]
      (setv param-name (if (isinstance k hy.models.String) (str k) (str k)))
      (.append decorators
        `(.parametrize (. pytest mark) ~(hy.models.String param-name)
           ~v))))

  ;; :marks → @pytest.mark.<name> for each mark
  (when (is-not marks None)
    (for [m marks]
      (setv mark-name (if (isinstance m hy.models.String) (str m) (str m)))
      (.append decorators
        `(. (. pytest mark) ~(hy.models.Symbol mark-name)))))

  ;; :skip-if → @pytest.mark.skipif(condition, reason=...)
  (when (is-not skip-if-expr None)
    (setv reason (if (is-not skip-reason None) skip-reason
                     (hy.models.String "skip condition met")))
    (.append decorators
      `(.skipif (. pytest mark) ~skip-if-expr :reason ~reason)))

  ;; Assemble the function definition with decorators
  (locate-synthesized (if decorators
    `(do
       (import pytest)
       ~(_helper-imports)
       (defn [~@decorators] ~name [~@fn-params] ~fn-body)
       (_install-guard-globals ~name {"_doeff_do" _doeff_do}))
    `(do
       ~(_helper-imports)
       (defn ~name [~@fn-params] ~fn-body)
       (_install-guard-globals ~name {"_doeff_do" _doeff_do})))))


;; ---------------------------------------------------------------------------
;; defpipeline — named-stage pipeline composition
;; ---------------------------------------------------------------------------

(defn _replace-stage-refs [expr stage-map]
  "Walk an expression and replace stage-name symbols with (! p-pipeline-stage)."
  (cond
    (and (isinstance expr hy.models.Symbol)
         (in (str expr) stage-map))
      (hy.models.Expression
        [(hy.models.Symbol "!") (get stage-map (str expr))])
    (isinstance expr hy.models.Expression)
      (hy.models.Expression (lfor child expr (_replace-stage-refs child stage-map)))
    (isinstance expr hy.models.List)
      (hy.models.List (lfor child expr (_replace-stage-refs child stage-map)))
    (isinstance expr hy.models.FComponent)
      (hy.models.FComponent (lfor child expr (_replace-stage-refs child stage-map)))
    True expr))

(defmacro defpipeline [pipeline-name #* body]
  "Define a pipeline as named stages. Each stage becomes a clickable defp.

   (defpipeline daily-cllm
     [ohlc]   (fetch-ohlc :ticker \"7203.T\" :day day)
     [news]   (fetch-news :day day)
     [data]   (merge-data ohlc news)
     [signal] (compute-signal data :model \"gpt-5\")
     [result] (execute-and-report signal))

   Expands to:
     (defp p-daily-cllm-ohlc   {:post []} (fetch-ohlc ...))
     (defp p-daily-cllm-news   {:post []} (fetch-news ...))
     (defp p-daily-cllm-data   {:post []} (merge-data (! p-daily-cllm-ohlc) (! p-daily-cllm-news)))
     (defp p-daily-cllm-signal {:post []} (compute-signal (! p-daily-cllm-data) ...))
     (defp p-daily-cllm        {:post []} (execute-and-report (! p-daily-cllm-signal)))

   Stage names in expressions are auto-replaced with (! p-...) references.
   The last stage is also aliased as p-{pipeline-name}.
   Each stage is independently runnable via IDE click."
  ;; Parse body: skip optional docstring, then [name] expr pairs
  (setv forms (list body))
  (setv docstring None)
  (when (and forms (isinstance (get forms 0) hy.models.String))
    (setv docstring (get forms 0))
    (setv forms (list (cut forms 1 None))))
  ;; Parse [name] expr pairs
  (setv stages [])
  (setv i 0)
  (while (< i (len forms))
    (setv name-form (get forms i))
    (when (not (isinstance name-form hy.models.List))
      (raise (SyntaxError (.format "
defpipeline {pipeline}: expected [stage-name], got {got}.

  Each stage must be a [name] expr pair:

    (defpipeline {pipeline}
      [fetch]  (fetch-data :day day)
      [signal] (compute-signal fetch)
      [result] (export signal))
" :pipeline pipeline-name :got (repr name-form)))))
    (when (= (len name-form) 0)
      (raise (SyntaxError (.format "
defpipeline {pipeline}: empty stage name [].
" :pipeline pipeline-name))))
    (when (>= (+ i 1) (len forms))
      (raise (SyntaxError (.format "
defpipeline {pipeline}: stage [{stage}] has no expression.
" :pipeline pipeline-name :stage (get name-form 0)))))
    (setv expr (get forms (+ i 1)))
    (.append stages #((get name-form 0) expr))
    (+= i 2))
  (when (= (len stages) 0)
    (raise (SyntaxError (.format "
defpipeline {pipeline}: no stages defined.
" :pipeline pipeline-name))))
  ;; Build stage-name → defp-name mapping
  (setv prefix (str pipeline-name))
  (setv stage-map {})
  (for [#(sname _) stages]
    (setv (get stage-map (str sname))
      (hy.models.Symbol (+ "p-" prefix "-" (str sname)))))
  ;; Generate defp for each stage
  ;; Each stage expr is wrapped as: (<- _stage expr) _stage
  ;; This ensures both effect constructors and kleisli calls are properly yielded.
  (setv result-forms [])
  (for [#(sname expr) stages]
    (setv defp-name (get stage-map (str sname)))
    (setv resolved-expr (_replace-stage-refs expr stage-map))
    (setv stage-var (hy.models.Symbol (+ "_stage_" (str sname))))
    (.append result-forms
      `(defp ~defp-name {:post []}
         (<- ~stage-var ~resolved-expr)
         ~stage-var)))
  ;; Last stage also defines p-{pipeline-name}
  (setv #(last-sname _) (get stages -1))
  (setv last-defp (get stage-map (str last-sname)))
  (setv pipeline-defp (hy.models.Symbol (+ "p-" prefix)))
  (.append result-forms `(setv ~pipeline-defp ~last-defp))
  `(do ~@result-forms))


;; ---------------------------------------------------------------------------
;; <-> — effectful first-arg threading macro
;; ---------------------------------------------------------------------------

(setv _thread-counter 0)

(defn _fresh-thread-tmp []
  "Generate a fresh temporary variable for <-> threading."
  (global _thread-counter)
  (setv _thread-counter (+ _thread-counter 1))
  (hy.models.Symbol (+ "_thread_" (str _thread-counter))))

(defmacro <-> [#* forms]
  "Effectful first-arg threading macro.

   (<-> (f :k v) (g :k2 v2) (h))

   Expands to:
     (<- _t0 (f :k v))
     (<- _t1 (g _t0 :k2 v2))
     (<- _t2 (h _t1))
     _t2

   First form: no threading (initial value).
   Subsequent forms: previous result inserted as first arg after fn name.
   Keyword args (:k v pairs) are preserved in position."
  (when (= (len forms) 0)
    (raise (SyntaxError "<-> requires at least one form")))
  (setv result-forms [])
  (setv prev-tmp None)
  (for [form forms]
    (setv tmp (_fresh-thread-tmp))
    (setv call
      (if (is prev-tmp None)
          ;; First form: no threading
          form
          ;; Subsequent: insert prev-tmp as first arg after fn name
          (hy.models.Expression
            (+ [(get form 0) prev-tmp] (list (cut form 1 None))))))
    (.append result-forms `(<- ~tmp ~call))
    (setv prev-tmp tmp))
  `(do ~@result-forms ~prev-tmp))


;; ---------------------------------------------------------------------------
;; defmcp-tool — MCP tool definition backed by doeff handler
;; ---------------------------------------------------------------------------

(defn _parse-mcp-params [param-list]
  "Parse MCP parameter list into (param-schemas, fn-param-names).
   Each element is a dict with :name, :type, :description, and optional
   :enum, :required, :default.

   Returns #(schema-exprs param-symbols) where schema-exprs are
   McpParamSchema constructor calls and param-symbols are Hy symbols
   for the @do function signature."
  (setv schemas []
        param-syms [])
  (for [p param-list]
    ;; p is a Hy Dict: {:name \"x\" :type \"string\" :description \"desc\" ...}
    (setv pdict {})
    (for [#(k v) (zip (cut p None None 2) (cut p 1 None 2))]
      (setv (get pdict (str k)) v))
    (setv pname (str (get pdict ":name")))
    (setv ptype (str (get pdict ":type")))
    (setv pdesc (str (get pdict ":description")))
    ;; Build keyword args for McpParamSchema
    (setv kw-args [])
    (when (in ":enum" pdict)
      (setv enum-vals (get pdict ":enum"))
      (.append kw-args `(tuple ~enum-vals))
      (.append kw-args None))  ; placeholder, handled below
    (setv has-enum (in ":enum" pdict))
    (setv has-required (in ":required" pdict))
    (setv has-default (in ":default" pdict))
    ;; Build the McpParamSchema(...) expression
    (setv schema-expr
      `(McpParamSchema
         :name ~pname
         :type ~ptype
         :description ~pdesc))
    (when has-enum
      (setv enum-val (get pdict ":enum"))
      (setv schema-expr
        `(McpParamSchema
           :name ~pname
           :type ~ptype
           :description ~pdesc
           :enum (tuple ~enum-val))))
    (when has-required
      ;; Rebuild with :required
      (setv req-val (get pdict ":required"))
      (if has-enum
          (setv schema-expr
            `(McpParamSchema
               :name ~pname
               :type ~ptype
               :description ~pdesc
               :enum (tuple ~(get pdict ":enum"))
               :required ~req-val))
          (setv schema-expr
            `(McpParamSchema
               :name ~pname
               :type ~ptype
               :description ~pdesc
               :required ~req-val))))
    (when has-default
      (setv def-val (get pdict ":default"))
      ;; Rebuild with :default — append to whatever we have
      ;; For simplicity, build with all optional fields
      (setv schema-expr
        `(McpParamSchema
           :name ~pname
           :type ~ptype
           :description ~pdesc
           ~@(if has-enum [`(:enum (tuple ~(get pdict ":enum")))] [])
           ~@(if has-required [`(:required ~(get pdict ":required"))] [])
           :default ~def-val)))
    (.append schemas schema-expr)
    (.append param-syms (hy.models.Symbol (.replace pname "-" "_"))))
  #(schemas param-syms))

(defmacro defmcp-tool [name description param-list #* body]
  "Define an MCP tool backed by a doeff @do handler.

   The body uses <- for effect binding, same as defk.
   Typically the body is a single line calling an existing defk.

   (defmcp-tool submit-order
     \"Submit a trading order to Kabustation\"
     [{:name \"symbol\" :type \"string\" :description \"Stock symbol code\"}
      {:name \"side\" :type \"string\" :enum [\"buy\" \"sell\"] :description \"Order side\"}
      {:name \"qty\" :type \"integer\" :description \"Quantity to trade\"}]
     (<- result (submit-order symbol side qty))
     result)

   Expands to:
   1. A @do function _<name>_mcp_handler with params from the schema
   2. An McpToolDef instance <name> with the schema + handler"
  (when (not (isinstance description hy.models.String))
    (raise (SyntaxError (.format "
defmcp-tool {name}: second argument must be a description string.

  (defmcp-tool {name}
    \"Description of what this tool does\"
    [{{:name \"param\" :type \"string\" :description \"param desc\"}}]
    body)
" :name name))))
  (when (not (isinstance param-list hy.models.List))
    (raise (SyntaxError (.format "
defmcp-tool {name}: third argument must be a parameter list [...].

  (defmcp-tool {name}
    \"Description\"
    [{{:name \"param\" :type \"string\" :description \"param desc\"}}]
    body)
" :name name))))
  ;; Parse param list
  (setv #(schema-exprs param-syms) (_parse-mcp-params param-list))
  ;; Build handler function name
  (setv handler-name (hy.models.Symbol (+ "_" (str name) "_mcp_handler")))
  ;; Expand bangs in body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form body (_expand-bangs form (+ "defmcp-tool " (str name)))))
  `(do
     (import doeff.do [do :as _doeff_do])
     (import doeff.mcp [McpToolDef McpParamSchema])
     (defn [_doeff_do] ~handler-name ~param-syms
       ~@expanded-forms)
     (setv ~name
       (McpToolDef
         :name ~(str name)
         :description ~description
         :params (tuple [~@schema-exprs])
         :handler ~handler-name))))


;; ---------------------------------------------------------------------------
;; set! — mutation macro for lazy-var
;; ---------------------------------------------------------------------------

(defmacro set! [name val]
  "Mutate a lazy-var: update local binding and write back to state.

   (set! items (+ items [new-item]))

   Expands to:
     (setv items (+ items [new-item]))
     (<- (Put _lazy_items_key (Some (+ items [new-item]))))

   Requires a lazy-var in scope (the _lazy_{name}_key variable must exist).
   Using set! on a lazy-val raises a compile-time SyntaxError in defhandler/defk."
  (import warnings)
  (warnings.warn
    (+ "(set! " (str name) " …) は (:= " (str name) " 新しい値) へ移してください(意味は同じ)"
       " [ADR-DOE-HY-006]")
    DeprecationWarning
    :stacklevel 2)
  (setv key-var (hy.models.Symbol (+ "_lazy_" (str name) "_key")))
  `(do
     (setv ~name ~val)
     (<- (Put ~key-var (Some ~name)))))


;; ---------------------------------------------------------------------------
;; validate / check — 独立した検査を全部走らせて失敗を集める(doeff-validation の Hy の面)
;; ---------------------------------------------------------------------------
;;
;; 実行時の部分(CheckSpec・validate・ValidationException)は doeff-validation が持ち、ここは
;; 展開だけを持つ(ADR-DOE-HY-005 R5 — macro は doeff-hy にだけ置く)。展開した code は
;; doeff_validation を import するので、使う側は doeff-validation を入れる。
;;
;;   (require doeff-hy.macros [defk validate check])
;;
;;   (defk ensure-placement-matches [job request]
;;     {:pre [(: job AgentJob) (: request PlacementRequest)] :post [(: % NoneType)]}
;;     (! (validate
;;          (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)
;;          (check is-not (! (LookupConversation request.conversation)) None
;;                 :reason PlacementMismatch.CONVERSATION))))
;;
;; - validate: 直下に並べるのは独立した項目 — check と Program(defk の呼び出し)の 2 種類。
;;   項目を全部走らせ(逐次・並行・fail-fast は doeff-traverse の handler で選ぶ)、落ちた項目の失敗を
;;   全部集めて、1 つでもあれば ValidationException を投げる Program になる。defk の本体では
;;   (! (validate …)) か (<- _ (validate …)) で実行する。直下の裸の (<- …) / (! …) は展開の時点で誤り。
;; - check: validate の直下と、defk / do! の :pre / :post の中にだけ書ける。
;;   (check 演算子 引数 … :reason 理由) か (check 式 :reason 理由)。各引数は左から評価し、(! …) の
;;   印の付いた引数だけを効果として実行して、結果の値で比べる。式の字面と評価した値を記録する
;;   (and / or などの短絡・制御の形は分解しない)。それ以外の所の check は展開の時点で誤り。
;; - この節の関数は defn: マクロの展開の時(compile の時)に呼ばれ、Program を実行する場が無いので
;;   defk にできない。
;;
;; 設計の記録: docs/design/doeff-validation/design.md。

;; 引数を先に評価すると意味が変わる形(短絡・制御・束縛)。括弧の形なら分解せず式と真偽だけを
;; 記録し、平たい形の演算子に置いたら展開の時点で誤りにする。
(setv _VALIDATION-NOT-DECOMPOSABLE
  #{"and" "or" "if" "when" "unless" "cond" "let" "do" "fn" "lfor" "sfor" "dfor" "gfor"
    "->" "->>" "doto" "match" "setv" "quote" "quasiquote" "!"})


(defn _validation-head-name [form]
  "形の先頭の名前を返す(validate の直下の形・契約の check を見分けるため)。"
  (when (and (isinstance form hy.models.Expression) (> (len form) 0) (isinstance (get form 0) hy.models.Symbol))
    (str (get form 0))))


(defn _validation-is-check-form [form]
  "form が (check …) か — doeff-hy の defk / do! が契約の中の check を見分けるために使う。"
  (= (_validation-head-name form) "check"))


(defn _validation-source [form]
  "失敗の記録に残す、書いたままの字面。"
  (setv text (hy.repr form))
  (if (.startswith text "'") (cut text 1 None) text))


(defn _validation-split-reason [args]
  "check の引数から :reason 理由 を取り出す。"
  (setv positional [] reason 'None i 0)
  (while (< i (len args))
    (setv arg (get args i))
    (cond
      (and (isinstance arg hy.models.Keyword) (= (str arg) ":reason"))
        (do (when (>= (+ i 1) (len args))
              (raise (SyntaxError "check: :reason の後に理由を書いてください")))
            (setv reason (get args (+ i 1)))
            (+= i 2))
      (isinstance arg hy.models.Keyword)
        (raise (SyntaxError (+ "check: 知らない keyword " (str arg) " — 使えるのは :reason だけです")))
      True (do (.append positional arg) (+= i 1))))
  #(positional reason))


(defn _validation-parse-check [args]
  "check の引数を #(式の字面 演算子か-None 引数の列 理由) に解く(validate と契約の共通の解析)。

   演算子が None の時は、引数の列の 1 つの式そのものの真偽で判定する(分解しない形)。"
  (setv #(positional reason) (_validation-split-reason args))
  (cond
    (= (len positional) 0)
      (raise (SyntaxError "check: (check 演算子 引数 …) か (check 式) の形で書いてください"))
    (= (len positional) 1)
      (let [expr (get positional 0)
            head (_validation-head-name expr)]
        (if (and head (not-in head _VALIDATION-NOT-DECOMPOSABLE) (> (len expr) 1))
            #((_validation-source expr) (get expr 0) (list (cut expr 1 None)) reason)
            #((_validation-source expr) None [expr] reason)))
    True
      (let [op (get positional 0)
            operands (list (cut positional 1 None))]
        (when (and (isinstance op hy.models.Symbol) (in (str op) _VALIDATION-NOT-DECOMPOSABLE))
          (raise (SyntaxError (+ "check: " (str op) " は引数を先に評価すると意味が変わります — "
                                 "(check (" (str op) " …)) の括弧の形で書いてください"))))
        #((_validation-source (hy.models.Expression [op #* operands])) op operands reason))))


(defn _validation-contains-bang [form]
  "引数の式の中に (! …) の印があるか(印のある引数だけを効果として実行する)。"
  (cond
    (= (_validation-head-name form) "!") True
    (in (_validation-head-name form) #{"quote" "quasiquote"}) False
    (isinstance form hy.models.Sequence) (any (gfor sub form (_validation-contains-bang sub)))
    True False))


(defn _validation-imports []
  "展開した code が参照する doeff-validation と doeff の do の import(pyright が型を追えるように)。"
  ;; 別名は validate / check の展開だけが使う名前にする: defk の本体の中で import すると関数の局所名に
  ;; なるので、defk が使う _doeff_do などと同じ名前を import すると、それより前の参照が未束縛になる。
  (if (_static-view?)
      `(do (import doeff-hy.static-types [do :as _doeff_validation_do])
           (import doeff-validation :as _doeff_validation)
           (import doeff-validation.api :as _doeff_validation_api))
      `(do (import doeff.do [do :as _doeff_validation_do])
           (import doeff-validation :as _doeff_validation)
           (import doeff-validation.api :as _doeff_validation_api))))


(defn _validation-predicate [op params]
  "判定の関数の式: 演算子があれば (fn [a b] (op a b))、無ければ 1 つの値そのもの。"
  (if (is op None)
      `(fn [~@params] ~(get params 0))
      `(fn [~@params] (~op ~@params))))


;; ---------------------------------------------------------------------------
;; validate の直下の check — 項目(CheckSpec)にする
;; ---------------------------------------------------------------------------

(defn _validation-argument [form]
  "引数 1 つを、字面と、項目の中で評価する thunk の組にする(評価の失敗をその項目の失敗にするため)。

   (! …) の印がある引数は、印を doeff-hy の規則(ADR-DOE-HY-003)で yield に書き換えた小さな Program を
   thunk が作り、項目の中で実行する。印の無い引数は普通の値として評価する(Program の値もそのまま比べる)。"
  (if (_validation-contains-bang form)
      `(_doeff_validation.CheckArgument
         ~(_validation-source form)
         (fn [] ((_doeff_validation_do (fn [] (return ~(_expand-bangs form "check"))))))
         True)
      `(_doeff_validation.CheckArgument ~(_validation-source form) (fn [] ~form) False)))


(defn _validation-check-spec [args]
  "validate の直下の check を CheckSpec の構築の式にする。"
  (setv #(expression op operands reason) (_validation-parse-check args))
  (setv params (lfor _ operands (hy.gensym "arg")))
  `(_doeff_validation.CheckSpec
     ~expression
     ~(_validation-predicate op params)
     #(~@(lfor o operands (_validation-argument o)))
     ~reason))


(defn _validation-item [form]
  "validate の直下の形を項目にする — check は検査の指定に、それ以外は Program としてそのまま。"
  (setv head (_validation-head-name form))
  (cond
    (in head #{"<-" "!"})
      (raise (SyntaxError (+ "validate: 直下に (" head " …) は書けません — " (_validation-source form) "\n\n"
                             "  validate の直下に並べるのは独立した項目(check か Program)です。\n"
                             "  Program の項目は (! …) を付けずにそのまま置きます(validate が実行する)。\n"
                             "  前の値に依る処理は validate の前で済ませるか、helper の defk の中に\n"
                             "  自分の validate を持たせてください。")))
    (= head "check") (_validation-check-spec (list (cut form 1 None)))
    True form))


;; ---------------------------------------------------------------------------
;; defk / do! の契約の中の check — その場で評価する文の列にする
;; ---------------------------------------------------------------------------

(defn _validation-contract-block [checks fn-name phase]
  "契約(:pre / :post)の check の列を、全部を評価して失敗を集め、1 つでもあれば ValidationException
   を投げる文の列にする。doeff-hy の defk / do! が契約の組み立てで呼ぶ。

   各 check の引数は左から評価し、(! …) の印の付いた引数はその場で実行する(defk / do! の本体は
   生成器なので yield が書ける)。引数か判定の評価が例外なら CheckError として集める。"
  (setv failures (hy.gensym "failures"))
  (setv owner (+ (str fn-name) " " phase))
  (setv per-check
    (lfor form checks
      (let [#(expression op operands reason) (_validation-parse-check (list (cut form 1 None)))
            params (lfor _ operands (hy.gensym "arg"))
            evaluated (hy.gensym "evaluated")
            error (hy.gensym "error")]
        `(do
           (setv ~evaluated [])
           (try
             ~@(lfor #(p o) (zip params operands)
                 `(do
                    (setv ~p ~(if (_validation-contains-bang o) (_expand-bangs o owner) o))
                    (.append ~evaluated (_doeff_validation.EvaluatedArgument ~(_validation-source o) ~p))))
             (when (not (_doeff_validation_api.judge (~(_validation-predicate op params) ~@params)))
               (.append ~failures
                        (_doeff_validation.CheckFailure ~expression (tuple ~evaluated) ~reason)))
             (except [~error Exception]
               (.append ~failures
                        (_doeff_validation.CheckError ~expression (tuple ~evaluated) ~error
                                                          ~reason))))))))
  [(_validation-imports)
   `(setv ~failures [])
   #* per-check
   `(when ~failures
      (raise (_doeff_validation.ValidationException (tuple ~failures) :context ~owner)))])


;; ---------------------------------------------------------------------------
;; マクロ
;; ---------------------------------------------------------------------------

(defmacro validate [#* forms]
  "独立した項目(check と Program)を全部走らせ、落ちた項目の失敗を全部集める Program を作る。

   defk の本体では (! (validate …)) か (<- _ (validate …)) で実行する。

   (! (validate
        (check = (! (CountSeats node)) 0 :reason Reason.SEATS-LEFT)
        (check = job.phase Phase.PENDING :reason Reason.PHASE)))"
  `(do ~(_validation-imports)
       (_doeff_validation.validate ~@(lfor form forms (_validation-item form)))))


(defmacro check [#* args]
  "check は validate の直下と defk / do! の :pre / :post にだけ書ける。それ以外の所で展開されたら誤り。"
  (raise (SyntaxError (+ "check: validate の直下と、defk / do! の :pre / :post にだけ書けます"
                         "(defk の本体・fn・内包表記の中は不可)\n\n"
                         "    (! (validate\n"
                         "         (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)\n"
                         "         (check is-not (! (LookupConversation request.conversation)) None\n"
                         "                :reason PlacementMismatch.CONVERSATION)))\n\n"
                         "  検査のまとまりを使い回すなら、helper の defk の中に自分の validate を持たせて、\n"
                         "  その defk の呼び出しを外の validate に並べてください。"))))
