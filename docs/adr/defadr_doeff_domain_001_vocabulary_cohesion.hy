;;; Executable ADR: FP の「純粋だが所属不明の小関数の森」問題への doeff の答えは、
;;; 凝集を「語彙 + 法」の宣言レベルで結合する defdomain と、エージェントが照会できる
;;; SEDA(CLI/MCP)、そして適合検査である。エージェントにとってのドキュメントとは
;;; 失敗する検査のことである。
;;; R1 は 2026-07-17 に E1 として packages/doeff-domain に実装済み — 本ファイルの
;;; enforcement は designed-red 番兵から緑の挙動ピンに置換済み。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(require doeff-domain.macros [defdomain])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-DOMAIN-001
  :title "vocabulary cohesion: doeff-domain サブパッケージは凝集単位の宣言 defdomain(effect 型の束 + laws + 期待 handler 面)とプロセス内 registry・適合検査を提供し、SEDA は CLI/MCP としてエージェントから照会可能にし、適合検査(handler の domain 被覆・Program 使用 effect 集合 ⊆ interpreter 処理集合・domain 外の同義語彙定義の禁止)を defsemgrep/deftest で提供する。IDE 面は非優先 — 書き手はエージェントである"
  :status "proposed"
  :scope ["packages/doeff-domain"
          "packages/doeff-effect-analyzer"
          ".semgrep.yaml"
          "docs/adr/defadr_doeff_domain_001_vocabulary_cohesion.hy"]
  :problem
    [(fact
       "実害の実例(姉妹リポジトリ ACP、核#8): エージェント swarm が `_issue-terminal?` 述語を 7 定義、terminal-set を 6 定義(値も発散)、failure classifier を 3 系統まで増殖させた — 既存の 1 本が見つからないまま書き足される FP 凝集性欠如の典型。何も fail しなかった。ADR 0031 R2/R4(2026-07-03)で単一正典 predicates.hy に統合して是正。"
       :evidence "agent-control-plane docs/erosion-audit-2026-07-02.md 核#8; ACP ADR 0031; apps/hypha/shared/predicates.hy")
     (fact
       "SEDA(doeff-effect-analyzer)は『静的 effect 依存解析・incremental tree-structured reports・PyO3』を掲げる WIP。凝集の照会面(『この Program はどの effect を実行しうるか』『この effect の handler はどこか』)の種は既に社内にある。"
       :evidence "packages/doeff-effect-analyzer/README.md; specs/effect-analyzer/")
     (fact
       "書き手エージェントは IDE 補完を使わない。grep・ファイル読取・スキル・失敗する検査に反応する。よって凝集の担保は探索性の改善(IDE のドット体験)ではなく、照会可能なインデックスと fail する適合検査に置く。"
       :evidence "2026-07-14 maintainer 前提(agent-first); ACP 核#8 の増殖主体が swarm だった事実")]
  :context
    [(interpretation
       "OOP の凝集の正体は実行時機構(クラス)ではなく発見可能性である。doeff には effect 型族=インターフェース、handler=実装、handler stack=オブジェクトグラフという暗黙の凝集単位が既にあるが、宣言も強制もされていない。defdomain はこれを宣言に昇格する — 結合するのは語彙と法だけで、実装も状態も結合しない(OOP の密結合の原因を除いた凝集の輸入)。")
     (interpretation
       "エージェントにとってのドキュメントとは失敗する検査のことである。domain 宣言は (1) エージェントに最初に読ませる 1 ファイルであり、(2) 適合検査の照合対象である。docs は読み飛ばされるが red は必ず読まれる。")
     (interpretation
       "裁定(2026-07-17, ii): law vocabulary-has-single-home の declaration は「導入」と読む — includes による参照は宣言数に数えない。導入の一意性はクラス同一性(名前でなく)をキーに登録時に強制され、同名別クラスの併存は D3 の対象外(それは検査 (c) と :terms・命名 semgrep の領分)。")
     (interpretation
       "裁定(2026-07-17, iv): 検査 (c) の正直な限界 — 述語の真の同義検出は不可能である。effect の孤児禁止(EffectBase 子孫の domain 所属強制)+ :terms の正典宣言 + プロジェクト側の命名パターン semgrep が上限であり、それ以上を約束しない。")
     (interpretation
       "実装状況(2026-07-17): R1 は E1 として packages/doeff-domain に実装済み(Domain 純データ + プロセス内 registry + defdomain マクロ + handles 注釈 + 二層処理集合導出 + 検査 (a)(c) + doeff-core-effects dogfood)。R2(SEDA CLI/MCP)は E2 待ち、R3 の (b)(Program 使用集合 ⊆ interpreter 処理集合)は E3(SEDA 依存)待ちのため status は proposed を維持する。dogfood は実ドリフトを検出した: MemoDeleteEffect / CacheDeleteEffect は語彙として定義・export されているがどの handler も処理していない — known_uncovered として申告済み(被覆されたら stale 検査が申告除去を強制)、処置は maintainer 裁定待ち。")]
  :decision
    [(rule R1 "doeff-domain サブパッケージ(packages/doeff-domain)は凝集単位の宣言を提供する: 純データ Domain(name/title/effects/includes/terms/handlers/laws/adrs/docs)、プロセス内 registry(import 時登録・同名再登録は即例外)、宣言マクロ defdomain(Hy モジュール doeff_domain.macros)、生 Python handler 用の opt-in 注釈 handles()、処理集合の二層導出、適合検査 (a)(c)。doeff-domain は doeff-hy / doeff-adr に依存してはならない(doeff_vm.EffectBase と hy への依存は可、doeff-core-effects へは dogfood 宣言モジュールからのみ一方向)。")
     (rule R2 "SEDA は CLI および MCP tool としてエージェントから照会可能にする: 『この Program が実行しうる effect 集合』『この effect 型の handler 所在』『この interpreter の処理集合』。IDE プラグイン面は非優先とする。(E2 待ち)")
     (rule R3 "適合検査を defsemgrep / deftest で提供する: (a) handler が宣言 domain の導入 effect を被覆する(includes は導入元 domain の責務)、(b) Program の使用 effect 集合 ⊆ 実行 interpreter の処理集合(E3、SEDA 依存)、(c) domain 宣言の外での同義語彙(同一意味論の述語・effect)の定義を禁止する — 実装形は『名指しパッケージの全 EffectBase 子孫がいずれかの domain に導入されている』検査。")
     (rule R4 "本 ADR の実装は計画 Stage E であり、Track B(pytest 正典ゲート)の完了を前提とする — 適合検査は走るゲートがあって初めて意味を持つ。")
     (rule R5 "裁定(2026-07-17, i): defdomain は完全 opt-in である — doeff 利用の必須要素ではなく組織化戦略であり、既定ゲート(run()/CLI/default interpreter)への強制はしない。適合検査は採用プロジェクトが自分のテストとして配線する。doeff 自身は dogfood として自パッケージ範囲(doeff-core-effects)に採用する。")
     (rule R6 "裁定(2026-07-17, ii): 導入 1 / 包含 ∞ — 1 つの effect クラスは全体でちょうど 1 つの domain の effects(導入)に現れてよい。2 つ目の domain が同じクラスを導入しようとしたら登録時に両 domain 名を明示して即例外。includes による参照はいくつでも可。キーはクラス同一性。")
     (rule R7 "裁定(2026-07-17, iii): 処理集合の導出は二層 — (1) 注釈層: handles() が付与する __doeff_handles__(生 Python handler の opt-in 宣言。他パッケージの関数へ後付け可。全域性の保証ではなく、実態照合は E2/E3 の SEDA が担う)。(2) 構造層: defhandler 産物の __doeff_body__(quoted 節リスト。属性ダックタイピングで判定し doeff-hy を import しない。ドリフト不能)。lazy 初期化節(defhandler が認める 3 head: lazy / lazy-val / lazy-var)はスキップし、:when ガードや条件付き reperform を含む節も『処理に参加する宣言』として数える。名前解決は sys.modules[handler.__module__] 属性 → 照合先 domain の effect クラス名との文字列一致の順で試み、どちらも失敗したら検査エラー(fail loud)。どちらの属性も無い handler も検査エラー。")]
  :laws
    [(law vocabulary-has-single-home
       :statement "for_all semantic_predicate_or_effect v: canonical_declaration_count(v) == 1 AND declared_in_some_domain(v); duplicate_definitions_detected_by_conformance_check"
       :counterexamples
         [(counterexample "ACP 核#8: terminal 述語 7 定義・terminal-set 6 定義が並存し、判定が呼び出しサイトごとに発散(2026-07-02 監査で検出、何も fail していなかった)")
          (counterexample "新規エージェントが既存 domain 語彙を発見できず、同義の effect 型を別パッケージに追加する — 照会面(SEDA)不在時の既定挙動")])]
  :enforcement
    [(deftest test-adr-doe-domain-001-defdomain-provided-by-doeff-domain
       ;; R1 緑ピン: doeff-domain が宣言 API 一式を提供する(旧 designed-red 番兵の置換)。
       ;; defdomain マクロ自体は本ファイル冒頭の require と下の includer ピンで実証される。
       (import doeff_domain [Domain DomainLaw DomainTerm register-domain
                             handles handled-effects assert-domain-covered
                             assert-registered-domains-covered
                             assert-no-orphan-effects isolated-registry])
       (import doeff_domain.macros)
       (assert (callable register-domain))
       (assert (callable handles))
       (assert (callable handled-effects))
       (assert (callable assert-domain-covered))
       (assert (callable assert-registered-domains-covered))
       (assert (callable assert-no-orphan-effects)))
     (deftest test-adr-doe-domain-001-introduce-once-include-freely
       ;; R6/D3 緑ピン: 2 つ目の導入は両 domain 名入りで登録時に即例外、includes は自由。
       (import doeff_domain [Domain register-domain isolated-registry
                             DuplicateEffectIntroductionError])
       (import doeff_vm [EffectBase])
       (import pytest)
       (defclass Adr001PinEffect [EffectBase])
       (with [_ (isolated-registry)]
         (setv home (register-domain
                      (Domain :name "adr-001-pin-home" :title "pin: 導入元"
                              :effects [Adr001PinEffect])))
         (with [excinfo (pytest.raises DuplicateEffectIntroductionError)]
           (register-domain
             (Domain :name "adr-001-pin-second" :title "pin: 二重導入"
                     :effects [Adr001PinEffect])))
         (assert (in "adr-001-pin-home" (str excinfo.value)))
         (assert (in "adr-001-pin-second" (str excinfo.value)))
         ;; includes による参照はいくつでも可 — defdomain マクロの実使用ピンを兼ねる
         (defdomain adr-001-pin-includer
           :title "pin: 包含は自由"
           :includes [home])
         (defdomain adr-001-pin-includer-2
           :title "pin: 包含は自由(2)"
           :includes [home])
         (assert (= adr-001-pin-includer.includes #(home)))))
     (deftest test-adr-doe-domain-001-coverage-check-red-green
       ;; R3(a) 緑ピン: 被覆欠落 domain で fail する fixture と、被覆で pass する fixture。
       (import doeff_domain [Domain register-domain isolated-registry handles
                             assert-domain-covered DomainCoverageError])
       (import doeff_vm [EffectBase])
       (import pytest)
       (defclass Adr001CoveredEffect [EffectBase])
       (defclass Adr001UncoveredEffect [EffectBase])
       (defn adr-001-pin-handler [body] body)
       ((handles Adr001CoveredEffect) adr-001-pin-handler)
       (with [_ (isolated-registry)]
         (setv red (register-domain
                     (Domain :name "adr-001-pin-red" :title "pin: 被覆欠落"
                             :effects [Adr001CoveredEffect Adr001UncoveredEffect]
                             :handlers [adr-001-pin-handler])))
         (with [excinfo (pytest.raises DomainCoverageError)]
           (assert-domain-covered red))
         (assert (in "Adr001UncoveredEffect" (str excinfo.value))))
       (with [_ (isolated-registry)]
         (setv green (register-domain
                       (Domain :name "adr-001-pin-green" :title "pin: 被覆済み"
                               :effects [Adr001CoveredEffect]
                               :handlers [adr-001-pin-handler])))
         (assert-domain-covered green)))
     (deftest test-adr-doe-domain-001-orphan-check-red-green
       ;; R3(c) 緑ピン: 未所属 EffectBase 子孫はクラス名+定義モジュールを挙げて fail し、
       ;; 導入すれば pass する。
       (import doeff_domain [Domain register-domain isolated-registry
                             assert-no-orphan-effects OrphanEffectError])
       (import doeff_vm [EffectBase])
       (import pytest sys types)
       (setv mod-name "adr_doe_domain_001_orphan_pin")
       (setv mod (types.ModuleType mod-name))
       (setv OrphanPin (type "Adr001OrphanPinEffect" #(EffectBase)
                             {"__module__" mod-name}))
       (setv (. mod Adr001OrphanPinEffect) OrphanPin)
       (setv (get sys.modules mod-name) mod)
       (try
         (with [_ (isolated-registry)]
           (with [excinfo (pytest.raises OrphanEffectError)]
             (assert-no-orphan-effects [mod-name]))
           (assert (in "Adr001OrphanPinEffect" (str excinfo.value)))
           (assert (in mod-name (str excinfo.value)))
           (register-domain
             (Domain :name "adr-001-pin-orphan-home" :title "pin: 孤児の導入"
                     :effects [OrphanPin]))
           (assert-no-orphan-effects [mod-name]))
         (finally
           (del (get sys.modules mod-name)))))
     (deftest test-adr-doe-domain-001-dogfood-core-effects-green
       ;; R5/D8 緑ピン: dogfood 宣言が登録され、(c) が doeff-core-effects 全域で green。
       (import doeff_domain.core-effects-domains)
       (import doeff_domain [get-domain assert-no-orphan-effects])
       (for [name ["doeff-reader" "doeff-state" "doeff-writer" "doeff-slog"
                   "doeff-error" "doeff-scope" "doeff-listen" "doeff-await"
                   "doeff-scheduler" "doeff-http" "doeff-memo" "doeff-cache"]]
         (assert (get-domain name)))
       (assert-no-orphan-effects ["doeff_core_effects"]))
     (defsemgrep domain-001-no-hy-adr-dependency
       "doeff-domain-no-hy-adr-dependency"
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/introspect.py"
         "source" "import doeff_hy\n"}
        {"relative-path" "packages/doeff-domain/src/doeff_domain/macros.hy"
         "source" "(require doeff-hy.handle [defhandler])\n"}]
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/introspect.py"
         "source" "import hy\n"}
        {"relative-path" "packages/doeff-domain/tests/test_domain_introspect.py"
         "source" "import doeff_hy\n"}])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
