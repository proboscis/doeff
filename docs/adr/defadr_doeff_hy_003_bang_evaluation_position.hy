;;; Executable ADR: bang(!) は効果を記述位置で評価し、制御境界や先行する
;;; 部分式を越えて hoist しない。Python generator に変換できない内包表記と
;;; 通常のネスト関数は、修正プロンプト付きの展開時エラーにする。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defhandler defk <-])
(import doeff-hy.macros [_expand-bangs])
(import doeff [EffectBase])
(import dataclasses [dataclass])
(import hy)


(defclass [(dataclass :frozen True)] _BangProbe [EffectBase]
  #^ str label
  #^ object value)


(defhandler _bang-probe-handler [events]
  (_BangProbe [label value]
    (.append events label)
    (resume value)))


(defn _pair [left right]
  [left right])


(defk _branch-position-probe [urgent?]
  {:pre [(: urgent? bool)]
   :post [(: % str)]}
  (if urgent?
      (+ "sent:" (! (_BangProbe :label "alert" :value "ok")))
      "skipped"))


(defk _short-circuit-probe []
  {:pre []
   :post [(: % bool)]}
  (and False (! (_BangProbe :label "short-circuit" :value True))))


(defk _dict-literal-probe []
  {:pre []
   :post [(: % dict)]}
  {"k" (! (_BangProbe :label "dict" :value 42))})


(defk _argument-order-probe [events]
  {:pre [(: events list)]
   :post [(: % list)]}
  (_pair
    (do
      (.append events "setup")
      "ready")
    (! (_BangProbe :label "fetch" :value "fetched"))))


(defk _try-position-probe []
  {:pre []
   :post [(: % str)]}
  (try
    (do
      (raise (ValueError "stop before bang"))
      (! (_BangProbe :label "escaped-try" :value "unreachable")))
    (except [ValueError]
      "caught")))


(defk _simple-bang-probe []
  {:pre []
   :post [(: % int)]}
  (setv value (! (_BangProbe :label "simple" :value 41)))
  (+ value 1))


(defn _bang-expansion-error [source]
  (try
    (do
      (_expand-bangs (hy.read source))
      None)
    (except [error SyntaxError]
      (str error))))


(defadr ADR-DOE-HY-003
  :title "bang(!) は一時 bind の文頭 hoist ではなく、その場の yield 式へ変換して評価位置を保存する。if/and/or/try/引数/dict literal の制御・順序を越えない。Python generator に安全に変換できない lfor/gfor/sfor/dfor と通常のネスト fn は、修正プロンプト付きの展開時エラーにする"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy"]
  :problem
    [(fact
       "従来の `_expand_bangs` は `(! expr)` を一時 `<-` bind として収集し、包含する文の先頭へ一律に挿入する。この変換は if の未選択分岐、and/or の短絡、try の例外境界、関数引数の左から右という評価位置を越える。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:_expand-bangs (2026-07-14以前)")
     (fact
       "従来 walker は Expression と List しか辿らないため Dict 内の bang は未展開の `!` 呼び出しとして残り、実行時 NameError になる。"
       :evidence "ACP ADR-0056 移行 probe idiom (l); 本 ADR test-adr-doe-hy-003-dict-literal-bang-runs")
     (fact
       "lfor/gfor/sfor/dfor と通常のネスト fn の本体は外側 generator の yield 式を安全に受け取れない。暗号的なコンパイル／実行時エラーではなく、代替構文を示す展開時エラーが必要である。"
       :evidence "ACP ADR-0056 移行 probe idiom (m); 本 ADR の unsupported-position probes")]
  :context
    [(interpretation
       "Python の yield は generator 内の式であり、if 式、短絡演算子、呼び出し引数、dict literal、try suite にそのまま置ける。したがって bang の所有レイヤである Hy マクロは、通常位置では `(yield expr)` へ局所変換するのが最小かつ意味論保存的である。")
     (interpretation
       "効果的な反復には doeff の for/do と fnk が既にある。通常の内包表記や fn を無理に generator 化するより、展開時に for/do / fnk を示して書き手エージェントを正しい抽象へ誘導する。")]
  :decision
    [(rule R1 "bang `(! expr)` は、その bang 自身の評価位置にある `(yield expr)` へ変換する。周囲の文頭や suite 先頭へ bind を hoist しない。")
     (rule R2 "walker は Hy の全 Sequence model を辿り、Dict/List/Tuple/Set/FComponent 内でも同じ in-place 変換を行う。")
     (rule R3 "lfor/gfor/sfor/dfor 内の bang は `[ADR-DOE-HY-003]` と for/do への書き換え例を含む SyntaxError にする。")
     (rule R4 "通常のネスト fn/fn-a/defn/defn-a 内の bang は `[ADR-DOE-HY-003]` と fnk への書き換え例を含む SyntaxError にする。fnk と for/do/traverse は自身のマクロ展開に委譲する。")
     (rule R5 "単純な statement 内を含む既存の合法 bang は、値と効果回数を変えず後方互換に保つ。")]
  :laws
    [(law bang-preserves-evaluation-position
       :statement "for_all legal_bang: execution_time(effect) == evaluation_time(source_position); effect does not cross branch, short_circuit, try, or left_to_right_argument boundaries"
       :counterexamples
         [(counterexample "False 側の if 分岐に書いた bang が文頭へ hoist され、未選択でも handler を実行する")
          (counterexample "第2引数の bang が第1引数の setup 副作用より先に実行される")])
     (law unsupported-bang-position-fails-during-expansion
       :statement "bang inside python_comprehension or plain_nested_function => SyntaxError(actionable_rewrite, ADR-DOE-HY-003)"
       :counterexamples
         [(counterexample "lfor 内の bang が未展開のまま実行時 NameError になる")
          (counterexample "通常 fn 内の bang が意図せず別 generator を作る")])]
  :enforcement
    [(deftest test-adr-doe-hy-003-false-branch-does-not-run-bang
       (setv events [])
       (<- result ((_bang-probe-handler events) (_branch-position-probe False)))
       (assert (= result "skipped"))
       (assert (= events [])))

     (deftest test-adr-doe-hy-003-short-circuit-does-not-run-bang
       (setv events [])
       (<- result ((_bang-probe-handler events) (_short-circuit-probe)))
       (assert (is result False))
       (assert (= events [])))

     (deftest test-adr-doe-hy-003-dict-literal-bang-runs
       (setv events [])
       (<- result ((_bang-probe-handler events) (_dict-literal-probe)))
       (assert (= result {"k" 42}))
       (assert (= events ["dict"])))

     (deftest test-adr-doe-hy-003-argument-order-is-preserved
       (setv events [])
       (<- result ((_bang-probe-handler events) (_argument-order-probe events)))
       (assert (= result ["ready" "fetched"]))
       (assert (= events ["setup" "fetch"])))

     (deftest test-adr-doe-hy-003-bang-stays-inside-try
       (setv events [])
       (<- result ((_bang-probe-handler events) (_try-position-probe)))
       (assert (= result "caught"))
       (assert (= events [])))

     (deftest test-adr-doe-hy-003-comprehension-bang-is-expansion-error
       (setv message
         (_bang-expansion-error "(lfor x [1 2] (! (_BangProbe :label \"bad\" :value x)))"))
       (assert (isinstance message str))
       (assert (in "[ADR-DOE-HY-003]" message))
       (assert (in "for/do" message)))

     (deftest test-adr-doe-hy-003-nested-fn-bang-is-expansion-error
       (setv message
         (_bang-expansion-error "(fn [x] (! (_BangProbe :label \"bad\" :value x)))"))
       (assert (isinstance message str))
       (assert (in "[ADR-DOE-HY-003]" message))
       (assert (in "fnk" message)))

     (deftest test-adr-doe-hy-003-simple-bang-remains-compatible
       (setv events [])
       (<- result ((_bang-probe-handler events) (_simple-bang-probe)))
       (assert (= result 42))
       (assert (= events ["simple"])))]
  :plans [])
