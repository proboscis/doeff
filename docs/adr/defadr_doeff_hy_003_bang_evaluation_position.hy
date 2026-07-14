;;; Executable ADR: bang(!)インライン bind は「書いた位置に (yield expr) を手書きした」
;;; のと観測等価でなければならない(evaluation-position preservation)。
;;; 従来の「文の直前への一律 hoist」は分岐脱出・短絡無視・評価順逆転・literal 内未展開の
;;; 4 病理を作るため廃止し、in-place (yield expr) 置換に切り替える。
;;; yield が構文上置けない位置(内包表記・ネスト fn)は修正プロンプト形式の展開時
;;; SyntaxError とする(ADR-DOE-HY-001 の guard-error と同じ思想)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk deff do! <- defhandler])
(import doeff [run EffectBase])
(import dataclasses [dataclass])
(import types)
(import hy)
(import pytest)


;; ---------------------------------------------------------------------------
;; 生きた probe 群 — enforcement の deftest が実行する再現体。
;; ProbeEff の実行は probe-handler が log(Python list)へ tag を append して観測する。
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ProbeEff [EffectBase]
  #^ str tag)

(defclass [(dataclass :frozen True)] BoomEff [EffectBase])

(defhandler probe-handler [log]
  (ProbeEff [tag]
    (.append log tag)
    (resume tag))
  (BoomEff []
    (raise (ValueError "boom"))))

(deff record-step [log tag]
  {:pre [(: log list) (: tag str)]
   :post [(: % str)]}
  (.append log tag)
  tag)

;; probe 1: 分岐条件性 — False 分岐内の ! は実行されてはならない(罠: hoist が if の外で無条件実行)
(defk probe-branch [urgent]
  {:pre [(: urgent bool)] :post [(: % str)]}
  (if urgent
      (setv r (! (ProbeEff :tag "alert")))
      (setv r "skipped"))
  r)

;; probe 2: 短絡 — (and False (! ...)) は効果を実行してはならない
(defk probe-short-circuit [flag]
  {:pre [(: flag bool)] :post [(: % "False または tag 文字列")]}
  (setv r (and flag (! (ProbeEff :tag "sc-eff"))))
  r)

;; probe 3: dict literal 内の ! が動くこと(罠: 展開器が literal を歩かず実行時 NameError)
(defk probe-dict-literal []
  {:pre [] :post [(: % dict)]}
  {"k" (! (ProbeEff :tag "dict-eff"))})

;; probe 4: 同一文内の評価順 — 純粋引数が先、! が後(罠: hoist が ! を文の先頭へ)
(defk probe-eval-order [log]
  {:pre [(: log list)] :post [(: % list)]}
  [(record-step log "pure-first") (! (ProbeEff :tag "eff-second"))])

;; probe 5: try 文脈 — try 内の ! は try の中で評価され、handler の例外は except に届く
(defk probe-try-catch []
  {:pre [] :post [(: % str)]}
  (try
    (setv r (! (BoomEff)))
    (except [e ValueError]
      (setv r "caught")))
  r)

;; probe 8: 後方互換 — 単純な statement / 引数位置の従来合法 bang は同一意味論のまま
(defk probe-legacy-simple []
  {:pre [] :post [(: % str)]}
  (setv first-val (! (ProbeEff :tag "legacy-1")))
  (+ first-val "+" (! (ProbeEff :tag "legacy-2"))))


(defadr ADR-DOE-HY-003
  :title "bang(!) evaluation-position preservation: (! expr) は展開時にその出現位置で (yield expr) に in-place 置換され、書いた位置の評価意味論(条件性・短絡性・評価順・例外文脈・literal 内評価)を保存する。文の直前への hoist は廃止する。yield が構文上置けない位置(内包表記・ネスト fn)の ! は修正プロンプト形式の展開時 SyntaxError とする — エージェント書き手には静かな意味論移動ではなく決定的で行動可能な赤を"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy"]
  :problem
    [(fact
       "現行 _expand-bangs は全ての (! expr) を文の直前の (<- _bang_N expr) へ一律 hoist する。if/and/or/try の内側の ! も制御構造の外へ出るため、(if urgent? (setv r (! (send-alert))) ...) は urgent? が False でも send-alert を実行する。エラーも警告も出ない。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:979-1026(2026-07-14 時点); ACP ADR-0056 移行実測 idiom (g)(h)(l)(m)")
     (fact
       "同一文内でも hoist は評価順を逆転させる: (process (setup-step) (! (fetch))) は fetch が文頭に出て setup-step より先に走る。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:992-1000 walk が引数位置の ! を bindings 先頭へ抽出")
     (fact
       "walk は hy.models.Expression と List にしか潜らないため、dict/set/tuple/f-string literal 内の ! は未展開のまま残り、実行時に NameError: hyx_Xexclamation_markX という暗号的エラーになる。let の束縛値位置も同様。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:1017-1023(Dict/Set/Tuple/FString 分岐なし)")
     (fact
       "内包表記(lfor/gfor)内の効果は Python の構文上 yield 不能だが、現行は明確なエラーも案内も無く hoist が静かに意味論を変える。ネストした fn 内の ! も同様に外へ hoist される。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:971-977 は for/do と traverse のみ opaque 扱い")
     (fact
       "下流 agent-control-plane は委譲 worker に bang 全面禁止を課して運用回避しており、道具の縁が下流の規律に転嫁されている。"
       :evidence "ACP ADR-0056")
     (fact
       "Python の yield は式であり、Hy 1.3.0 は if 分岐・三項・and/or・dict/list literal・関数引数・try 本体・let 束縛値・while 条件・f-string の全 probe 位置で yield を持ち上げずその場の yield 式にコンパイルする(hy2py 出力と generator 実行の両方で確認)。内包表記内の yield は Python 3.8+ の SyntaxError、ネスト fn 内の yield はネスト fn を静かに generator 化する。"
       :evidence "2026-07-15 Hy 1.3.0 実測: scratchpad/yield_probe.hy の hy2py 出力 + send 駆動の実行確認(PR 記載)")]
  :context
    [(interpretation
       "! の合成可能な契約は『その位置に (yield expr) を手書きしたのと観測等価』ただ一つである。hoist は『文の直前に書いたのと等価』という別の暗黙意味論であり、構文位置と実行位置の乖離がエージェント書き手に予測不能な罠を作る。defk 本体は generator であり <- 自体が (setv x (yield expr)) の糖衣である以上、式位置の ! は yield 式へ in-place 置換すれば評価位置が正確に保存される。")
     (interpretation
       "保存不能な位置(内包表記=別スコープで yield 不能、ネスト fn=yield すると別 generator 化)は、静かな hoist でも静かな NameError でもなく、展開時の決定的な赤にする。ADR-DOE-HY-001 と同じ判断: 書き手はエージェントであり、修正プロンプト形式のエラーメッセージがそのまま修正指示になる。")
     (interpretation
       "独自の do-context を持つマクロフォーム(for/do・traverse・fnk・do!・handle 等)の内部の ! の所有権は最内の do-context マクロにある。外側の展開器が境界を越えて潜ると、遅延されるべき効果(do! の中身など)が外側の文脈で即時実行される — hoist と同族の評価位置バグ。")]
  :decision
    [(rule R1 "(! expr) は展開時にその出現位置で (yield expr) に置換される(in-place)。文の直前への hoist による評価位置の移動は行わない。")
     (rule R2 "この置換により、分岐(if/when/cond)・短絡(and/or)・try/except・dict/list/set/tuple/f-string literal・関数引数・let 束縛値・while 条件を含む全ての式位置で、条件性・短絡性・左から右の評価順・例外文脈が保存される。")
     (rule R3 "yield が構文上置けない位置 — 内包表記(lfor/gfor/dfor/sfor)およびネストした fn/fn/a/defn/defn/a/defclass 本体 — の ! は展開時 SyntaxError とする。エラーメッセージは修正プロンプト形式: 所有マクロ名・行番号・元式・修正テンプレ(事前 <- bind、および for/do または fnk への書き換え)を必ず含む。")
     (rule R4 "独自の do-context を持つフォーム(for/do traverse fnk do! handle defhandler defk deff defp defpp defmcp-tool およびそれらの内部)と quote/quasiquote/defmacro には外側の展開器は立ち入らない。bang の展開はそのフォーム自身のマクロが行う。")
     (rule R5 "(! ...) はちょうど 1 つのフォームを取る。それ以外の arity は展開時 SyntaxError とする(余剰引数の黙殺禁止)。")
     (rule R6 "<- の意味論・ADR-DOE-HY-001 の statement guard の意味論は変えない。")]
  :laws
    [(law bang-position-transparency
       :statement "for_all expression_position p in do_context: observable(program_with_bang_at(p)) == observable(program_with_inline_yield_at(p)) — 効果列・束縛値・例外文脈が一致する"
       :counterexamples
         [(counterexample "(if urgent? (setv r (! (send-alert))) (setv r \"skipped\")) — 旧 hoist では urgent?=False でも send-alert が実行された(分岐脱出)")
          (counterexample "(and (guard) (! (effect))) — 旧 hoist では guard が False でも effect が実行された(短絡無視)")
          (counterexample "(process (setup-step) (! (fetch))) — 旧 hoist では fetch が setup-step より先に走った(評価順逆転)")
          (counterexample "(try (setv r (! (risky))) (except [e E] ...)) — 旧 hoist では risky が try の外で評価され except に届かなかった(例外文脈喪失)")
          (counterexample "{\"k\" (! (eff))} — 旧実装は literal を歩かず、実行時 NameError: hyx_Xexclamation_markX(暗号的)")
          (counterexample "(setv p (do! (! (eff)))) — 旧 hoist は do! 境界を越えて外側で即時実行し、遅延 Program の意味論を壊した")])
     (law bang-impossible-position-loud
       :statement "for_all position p where yield_is_syntactically_impossible(p): bang_at(p) => expansion_time_SyntaxError with fix_prompt; silent_hoist_count == 0 AND silent_nameerror_count == 0"
       :counterexamples
         [(counterexample "(lfor x xs (! (eff x))) — 旧実装は x をスコープ外へ hoist して静かに壊れた(エラーも案内も無し)")
          (counterexample "(fn [x] (+ x (! (eff)))) — 旧実装は効果を fn の外で 1 回だけ実行し、クロージャの意味論を静かに変えた")])]
  :enforcement
    [(deftest test-adr-doe-hy-003-branch-conditionality
       ;; probe 1: False 分岐内の ! の効果は実行されない(handler 側で実行回数を観測)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-branch False))))
       (assert (= result "skipped"))
       (assert (= log []) (+ "False 分岐の効果が実行された(hoist 分岐脱出): " (str log)))
       (setv log2 [])
       (setv result2 (run ((probe-handler log2) (probe-branch True))))
       (assert (= result2 "alert"))
       (assert (= log2 ["alert"])))
     (deftest test-adr-doe-hy-003-short-circuit
       ;; probe 2: (and False (! ...)) は効果を実行しない
       (setv log [])
       (setv result (run ((probe-handler log) (probe-short-circuit False))))
       (assert (= result False))
       (assert (= log []) (+ "短絡で止まるべき効果が実行された: " (str log)))
       (setv log2 [])
       (setv result2 (run ((probe-handler log2) (probe-short-circuit True))))
       (assert (= result2 "sc-eff"))
       (assert (= log2 ["sc-eff"])))
     (deftest test-adr-doe-hy-003-dict-literal
       ;; probe 3: dict literal 内の ! が動く(in-place 置換を選択 — エラー化ではない)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-dict-literal))))
       (assert (= result {"k" "dict-eff"}))
       (assert (= log ["dict-eff"])))
     (deftest test-adr-doe-hy-003-same-statement-eval-order
       ;; probe 4: 同一文内で純粋引数が ! より先に評価される(左から右)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-eval-order log))))
       (assert (= result ["pure-first" "eff-second"]))
       (assert (= log ["pure-first" "eff-second"])
               (+ "同一文内の評価順が逆転した: " (str log))))
     (deftest test-adr-doe-hy-003-try-context
       ;; probe 5: try 内の ! は try の中で評価され、handler の例外が except に届く
       (setv result (run ((probe-handler []) (probe-try-catch))))
       (assert (= result "caught")
               "try 内の ! の例外が except に届かない(hoist が try 文脈を壊している)"))
     (deftest test-adr-doe-hy-003-comprehension-expansion-error
       ;; probe 6: 内包表記内の ! → 修正プロンプト付き展開時 SyntaxError
       (setv mod (types.ModuleType "_adr_doe_hy_003_lfor_probe"))
       (setv code (+ "(require doeff-hy.macros [defk <-])\n"
                     "(defk bad-lfor [xs]\n"
                     "  {:pre [(: xs list)] :post [(: % list)]}\n"
                     "  (lfor x xs (! (probe-eff x))))"))
       (with [excinfo (pytest.raises SyntaxError)]
         (for [form (hy.read-many code)]
           (hy.eval form :module mod)))
       ;; 修正プロンプト: for/do への書き換え案内を含むこと
       (assert (in "for/do" (str excinfo.value))
               (+ "展開時エラーに for/do の修正案内が無い: " (str excinfo.value))))
     (deftest test-adr-doe-hy-003-nested-fn-expansion-error
       ;; probe 7: ネスト fn 内の ! → 修正プロンプト付き展開時 SyntaxError
       (setv mod (types.ModuleType "_adr_doe_hy_003_fn_probe"))
       (setv code (+ "(require doeff-hy.macros [defk <-])\n"
                     "(defk bad-fn [xs]\n"
                     "  {:pre [(: xs list)] :post [(: % list)]}\n"
                     "  (list (map (fn [x] (+ x (! (probe-eff x)))) xs)))"))
       (with [excinfo (pytest.raises SyntaxError)]
         (for [form (hy.read-many code)]
           (hy.eval form :module mod)))
       ;; 修正プロンプト: fnk への書き換え案内を含むこと
       (assert (in "fnk" (str excinfo.value))
               (+ "展開時エラーに fnk の修正案内が無い: " (str excinfo.value))))
     (deftest test-adr-doe-hy-003-legacy-simple-backcompat
       ;; probe 8: 既存の合法な bang 使用(単純 statement / 引数位置)の後方互換
       (setv log [])
       (setv result (run ((probe-handler log) (probe-legacy-simple))))
       (assert (= result "legacy-1+legacy-2"))
       (assert (= log ["legacy-1" "legacy-2"])))
     (deftest test-adr-doe-hy-003-do-bang-scope
       ;; probe 9(R4): do! 内の ! は do! の遅延 Program 文脈で実行される — 外へ hoist されない
       (setv log [])
       (setv p (do! (! (ProbeEff :tag "inner"))))
       (assert (= log []) "do! は遅延 Program — 構築時に効果が飛んではならない")
       (setv result (run ((probe-handler log) p)))
       (assert (= result "inner"))
       (assert (= log ["inner"])))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
