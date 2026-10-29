;;; Executable ADR: FP の「純粋だが所属不明の小関数の森」問題への doeff の答えは、
;;; 凝集を「語彙 + 法」の宣言レベルで結合する defdomain と、エージェントが照会できる
;;; SEDA(CLI/MCP)、そして適合検査である。エージェントにとってのドキュメントとは
;;; 失敗する検査のことである。
;;; R1 は 2026-07-17 に E1 として packages/doeff-domain に実装済み — 本ファイルの
;;; enforcement は designed-red 番兵から緑の挙動ピンに置換済み。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [DoExpr])
(import doeff_domain [DomainError GetDomain])


(defk failure-of [program]
  {:pre [(: program DoExpr)] :post [(: % (| DomainError KeyError ImportError))]
   :tags {:context "doeff-domain-adr" :role "program"}}
  "red の側の確かめ: program が domain の表の handler から投げ返された失敗で終わる事を確かめ、その失敗を返す(答えれば赤)。"
  (try
    (<- answer program)
    (raise (AssertionError (+ "the program answered instead of failing: " (repr answer))))
    (except [failure #(DomainError KeyError ImportError)]
      failure)))


(defk read-domains [names]
  {:pre [(: names list)] :post [(: % list)]
   :tags {:context "doeff-domain-adr" :role "program"}}
  "dogfood の確かめ: 名の並びを GetDomain で 1 つずつ引いた domain の並び(表は呼び手が据えた domain の表の handler)。"
  (var found [])
  (for [name names]
    (<- domain (GetDomain name))
    (:= found [#* found domain]))
  found)


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
       "実装状況(2026-07-17): R1 は E1 として packages/doeff-domain に実装済み(Domain 純データ + プロセス内 registry + defdomain マクロ + handles 注釈 + 二層処理集合導出 + 検査 (a)(c) + doeff-core-effects dogfood)。R2(SEDA CLI/MCP)は E2 待ち、R3 の (b)(Program 使用集合 ⊆ interpreter 処理集合)は E3(SEDA 依存)待ちのため status は proposed を維持する。dogfood は実ドリフトを検出した: MemoDeleteEffect / CacheDeleteEffect は語彙として定義・export されているがどの handler も処理していない — known_uncovered として申告済み(被覆されたら stale 検査が申告除去を強制)、処置は maintainer 裁定待ち。")
     (interpretation
       "裁定(2026-07-18, i–ii): E2(SEDA/registry 照会の CLI/MCP 面)は見送る。核#8 型増殖への実 enforcement は E1 の孤児禁止・被覆・stale-ratchet が既に担い、MemoDeleteEffect / CacheDeleteEffect の実ドリフト検出から裁定 A、PR #546 での解消まで実証済みである。照会対象の宣言は packages/doeff-domain/src/doeff_domain/core_effects_domains.hy の 1 ファイルに集約され grep 可能であり、Evidence Rule(docs/22-capability-classes.md)に従って証拠なき CLI/MCP コストは払わない。再開条件は『エージェントが語彙探索に実際に失敗した事例』の観測とし、その時点で再起票する。E3 の Program 使用 effect 集合 ⊆ interpreter 処理集合は価値を堅持するが、前提の interpreter→handler 列の列挙契約は frontier+人間の深い設計議論に予約し、worker へ委譲してはならず、E3 はその設計セッション後に着手する。2026-07-18 の frontier read-only 調査(原本 2026-07-18-seda-survey-doeff-e2.md)では、Python 解析系は cargo 統合テスト 8 件中 6 件が検出 effect 空集合で失敗し、ask( 等の文字列 prefix マッチだけで現 API の EffectBase サブクラス + dataclass を認識できず、specs/features/effect-analyzer.md も旧 API 前提と確認した。README / spec が参照する specs/effect-analyzer/ は存在しない幻参照である。一方、spec 外の Hy 解析系(hy_reader / hy_analyzer)は単体 14/14 green で、PascalCase 認識が現 API の粗い近似となり E3 左辺推論の土台候補である。handler 所在と interpreter 処理集合は doeff-domain registry の introducing_domain / handled_effects から静的解析なしで照会できるが、後者には上記の列挙契約が残る。なお 2026-07-17 の実装状況で maintainer 裁定待ちだった MemoDeleteEffect / CacheDeleteEffect は、裁定 A(PR #546、2026-07-18)で解消済みである。")]
  :decision
    [(rule R1 "doeff-domain サブパッケージ(packages/doeff-domain)は凝集単位の宣言を提供する: 純データ Domain(name/title/effects/includes/terms/handlers/laws/adrs/docs)、プロセス内 registry(import 時登録・同名再登録は即例外)、宣言マクロ defdomain(Hy モジュール doeff_domain.macros)、生 Python handler 用の opt-in 宣言の型 Handles(2026-10-29 に関数 handles() から改めた — R8)、処理集合の二層導出、適合検査 (a)(c)。表の読み書きと処理集合の導出と適合検査は effect と handler を返す工場 domain_registry で見せる(R8)。doeff-domain は doeff-hy / doeff-adr に依存してはならない(doeff_vm.EffectBase と hy への依存は可、doeff-core-effects へは dogfood 宣言モジュールからのみ一方向)。")
     (rule R2 "SEDA の CLI および MCP tool としての照会面『この Program が実行しうる effect 集合』『この effect 型の handler 所在』『この interpreter の処理集合』は見送る。IDE プラグイン面は非優先とする。(E2 見送り、2026-07-18 裁定 — 再開条件 = エージェントが語彙探索に実際に失敗した事例の観測)")
     (rule R3 "適合検査を defsemgrep / deftest で提供する: (a) handler が宣言 domain の導入 effect を被覆する(includes は導入元 domain の責務)、(b) Program の使用 effect 集合 ⊆ 実行 interpreter の処理集合(E3 — interpreter→handler 列の列挙契約の設計セッション(frontier+人間予約、worker への委譲禁止)後に着手)、(c) domain 宣言の外での同義語彙(同一意味論の述語・effect)の定義を禁止する — 実装形は『名指しパッケージの全 EffectBase 子孫がいずれかの domain に導入されている』検査。")
     (rule R4 "本 ADR の実装は計画 Stage E であり、Track B(pytest 正典ゲート)の完了を前提とする — 適合検査は走るゲートがあって初めて意味を持つ。")
     (rule R5 "裁定(2026-07-17, i): defdomain は完全 opt-in である — doeff 利用の必須要素ではなく組織化戦略であり、既定ゲート(run()/CLI/default interpreter)への強制はしない。適合検査は採用プロジェクトが自分のテストとして配線する。doeff 自身は dogfood として自パッケージ範囲(doeff-core-effects)に採用する。")
     (rule R6 "裁定(2026-07-17, ii): 導入 1 / 包含 ∞ — 1 つの effect クラスは全体でちょうど 1 つの domain の effects(導入)に現れてよい。2 つ目の domain が同じクラスを導入しようとしたら登録時に両 domain 名を明示して即例外。includes による参照はいくつでも可。キーはクラス同一性。")
     (rule R7 "裁定(2026-07-17, iii): 処理集合の導出は二層 — (1) 注釈層: domain の handlers に置く宣言の値 Handles(handler, effect …)と、handler が自分で持つ __doeff_handles__ の印(core の installer が付ける)の順に読む(生 Python handler の opt-in 宣言。他パッケージの handler も、その handler に何も付けずに宣言できる。全域性の保証ではなく、実態照合は E2/E3 の SEDA が担う。改めた前の形: 関数 handles() が handler に __doeff_handles__ を後付けした — 2026-10-29 に R8 で型 Handles に改めた)。(2) 構造層: defhandler 産物の __doeff_body__(quoted 節リスト。属性ダックタイピングで判定し doeff-hy を import しない。ドリフト不能)。lazy 初期化節(defhandler が認める 3 head: lazy / lazy-val / lazy-var)はスキップし、:when ガードや条件付き reperform を含む節も『処理に参加する宣言』として数える。名前解決は sys.modules[handler.__module__] 属性 → 照合先 domain の effect クラス名との文字列一致の順で試み、どちらも失敗したら検査エラー(fail loud)。宣言も、どちらの属性も無い handler も検査エラー。")
     (rule R8 "表に置く・引く・check する関数 8 個(register_domain・isolated_registry・get_domain・handles・handled_effects・assert_domain_covered・assert_registered_domains_covered・assert_no_orphan_effects)は、effect RegisterDomain・GetDomain・HandledEffects・AssertDomainCovered・AssertRegisteredDomainsCovered・AssertNoOrphanEffects と、表から答える handler を返す工場 domain_registry にし、関数は内側(doeff_domain._registry・_checks・_introspect)へ移した(2026-10-29・依頼書 lt-71XZ9Z3RP0W3V2WFN3R7TW6SSV の問い domain の答え lt-FP17P0A4CPBC1RJCF9BFBVF4WV — effect にする・card ki-03a7f0af2b64・ADR-DOE-API-001 R23)。domain_registry() は process の表(defdomain が import の時に載せる所)を答える時に読み、domain_registry(domains=[Domain …]) は渡した domain だけの別の表から答える — isolated_registry の代わりで、RegisterDomain もその表へ載せ、名の無い時は process の表へ落ちずに KeyError。内側に据えた方が先に答えるので、test は program の一部だけを別の表で走らせられる。check の外れ(DomainError の子)・表に無い名(KeyError)・走査で読めない module(ImportError)は effect を出した所へ投げ返す。handles は呼ぶと印の付いた handler を返す工場(ADR-DOE-API-001 R3)でも、program を返す helper(R18)でもない import の時の宣言なので、effect にせず宣言の型 Handles にした(R19 の check と同じ道 — 型は R2)。defdomain・宣言の型・例外は今のまま入口と doeff_domain.macros から見せる。")]
  :laws
    [(law vocabulary-has-single-home
       :statement "for_all semantic_predicate_or_effect v: canonical_declaration_count(v) == 1 AND declared_in_some_domain(v); duplicate_definitions_detected_by_conformance_check"
       :counterexamples
         [(counterexample "ACP 核#8: terminal 述語 7 定義・terminal-set 6 定義が並存し、判定が呼び出しサイトごとに発散(2026-07-02 監査で検出、何も fail していなかった)")
          (counterexample "新規エージェントが既存 domain 語彙を発見できず、同義の effect 型を別パッケージに追加する — 照会面(SEDA)不在時の既定挙動")])]
  :enforcement
    [(deftest test-adr-doe-domain-001-defdomain-provided-by-doeff-domain
       ;; R1・R8 緑ピン: doeff-domain の入口は宣言の型・例外と、表の effect・handler を返す工場を出し、前の関数 8 個を出さない。
       ;; defdomain マクロ自体は dogfood の宣言(下の dogfood ピン)が import の時に使い、GetDomain がその表から答える事で実証される。
       (import doeff_domain)
       (import doeff_domain [Domain DomainLaw DomainTerm Handles
                             RegisterDomain GetDomain HandledEffects AssertDomainCovered
                             AssertRegisteredDomainsCovered AssertNoOrphanEffects domain-registry])
       (import doeff_domain.macros)
       (import doeff [EffectBase])
       (for [effect [RegisterDomain GetDomain HandledEffects AssertDomainCovered
                     AssertRegisteredDomainsCovered AssertNoOrphanEffects]]
         (assert (issubclass effect EffectBase)))
       (assert (. (domain-registry) _doeff-is-handler-fn))
       (for [gone ["register_domain" "isolated_registry" "get_domain" "handles" "handled_effects"
                   "assert_domain_covered" "assert_registered_domains_covered" "assert_no_orphan_effects"]]
         (assert (not (hasattr doeff_domain gone)) gone)))
     (deftest test-adr-doe-domain-001-introduce-once-include-freely
       ;; R6/D3 緑ピン: 2 つ目の導入は両 domain 名入りで登録時に即例外(RegisterDomain を出した所へ投げ返す)、includes は自由。
       ;; 表は domain-registry の空の別の表 — process の表(defdomain の載せる所)に触れない(R8)。
       (import doeff_domain [Domain RegisterDomain domain-registry DuplicateEffectIntroductionError])
       (import doeff [EffectBase])
       (defclass Adr001PinEffect [EffectBase])
       (val table (domain-registry :domains []))
       (<- home (table (RegisterDomain (Domain :name "adr-001-pin-home" :title "pin: 導入元"
                                               :effects [Adr001PinEffect]))))
       (<- failure (failure-of (table (RegisterDomain (Domain :name "adr-001-pin-second" :title "pin: 二重導入"
                                                             :effects [Adr001PinEffect])))))
       (assert (isinstance failure DuplicateEffectIntroductionError))
       (assert (in "adr-001-pin-home" (str failure)))
       (assert (in "adr-001-pin-second" (str failure)))
       ;; includes による参照はいくつでも可
       (<- includer (table (RegisterDomain (Domain :name "adr-001-pin-includer" :title "pin: 包含は自由"
                                                   :includes [home]))))
       (<- includer-2 (table (RegisterDomain (Domain :name "adr-001-pin-includer-2" :title "pin: 包含は自由(2)"
                                                     :includes [home]))))
       (assert (= includer.includes #(home)))
       (assert (= includer-2.includes #(home))))
     (deftest test-adr-doe-domain-001-coverage-check-red-green
       ;; R3(a) 緑ピン: 被覆欠落 domain で fail する fixture と、被覆で pass する fixture。生 handler は Handles で宣言する(R7・R8)。
       (import doeff_domain [Domain Handles AssertDomainCovered domain-registry DomainCoverageError])
       (import doeff [EffectBase])
       (defclass Adr001CoveredEffect [EffectBase])
       (defclass Adr001UncoveredEffect [EffectBase])
       (defn adr-001-pin-handler [body] body)
       (val declared (Handles adr-001-pin-handler Adr001CoveredEffect))
       (val red (Domain :name "adr-001-pin-red" :title "pin: 被覆欠落"
                         :effects [Adr001CoveredEffect Adr001UncoveredEffect]
                         :handlers [declared]))
       (<- failure (failure-of ((domain-registry :domains [red]) (AssertDomainCovered red))))
       (assert (isinstance failure DomainCoverageError))
       (assert (in "Adr001UncoveredEffect" (str failure)))
       (val green (Domain :name "adr-001-pin-green" :title "pin: 被覆済み"
                           :effects [Adr001CoveredEffect]
                           :handlers [declared]))
       (<- _ ((domain-registry :domains [green]) (AssertDomainCovered "adr-001-pin-green")))
       (assert (not (hasattr adr-001-pin-handler "__doeff_handles__"))))
     (deftest test-adr-doe-domain-001-orphan-check-red-green
       ;; R3(c) 緑ピン: 未所属 EffectBase 子孫はクラス名+定義モジュールを挙げて fail し、
       ;; 導入すれば pass する(どちらも domain-registry の別の表で)。
       (import doeff_domain [Domain AssertNoOrphanEffects domain-registry OrphanEffectError])
       (import doeff [EffectBase])
       (import sys types)
       (val mod-name "adr_doe_domain_001_orphan_pin")
       (val mod (types.ModuleType mod-name))
       (val OrphanPin (type "Adr001OrphanPinEffect" #(EffectBase)
                             {"__module__" mod-name}))
       (setv (. mod Adr001OrphanPinEffect) OrphanPin)
       (setv (get sys.modules mod-name) mod)
       (try
         (<- failure (failure-of ((domain-registry :domains []) (AssertNoOrphanEffects [mod-name]))))
         (assert (isinstance failure OrphanEffectError))
         (assert (in "Adr001OrphanPinEffect" (str failure)))
         (assert (in mod-name (str failure)))
         (val home (Domain :name "adr-001-pin-orphan-home" :title "pin: 孤児の導入"
                            :effects [OrphanPin]))
         (<- _ ((domain-registry :domains [home]) (AssertNoOrphanEffects [mod-name])))
         (finally
           (del (get sys.modules mod-name)))))
     (deftest test-adr-doe-domain-001-dogfood-core-effects-green
       ;; R5/D8 緑ピン: dogfood 宣言が defdomain で process の表へ登録され、domain-registry() が GetDomain に答え、
       ;; (c) が doeff-core-effects 全域で green。
       (import doeff_domain._core_effects_domains)
       (import doeff_domain [AssertNoOrphanEffects domain-registry])
       (val table (domain-registry))
       (<- found (table (read-domains ["doeff-reader" "doeff-state" "doeff-writer" "doeff-slog"
                                       "doeff-error" "doeff-scope" "doeff-listen" "doeff-await"
                                       "doeff-scheduler" "doeff-http" "doeff-memo" "doeff-cache"
                                       "doeff-outcome" "doeff-file" "doeff-http-server" "doeff-sql"
                                       "doeff-process" "doeff-warm-process" "doeff-process-exit" "doeff-channel"
                                       "doeff-compute" "doeff-stop-signal" "doeff-vm-probe"])))
       (assert (all (gfor domain found domain.title)))
       (<- _ (table (AssertNoOrphanEffects ["doeff_core_effects"]))))
     (defsemgrep domain-001-no-hy-adr-dependency
       "doeff-domain-no-hy-adr-dependency"
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/_introspect.py"
         "source" "import doeff_hy\n"}
        {"relative-path" "packages/doeff-domain/src/doeff_domain/macros.hy"
         "source" "(require doeff-hy.handle [defhandler])\n"}]
       [{"relative-path" "packages/doeff-domain/src/doeff_domain/_introspect.py"
         "source" "import hy\n"}
        {"relative-path" "packages/doeff-domain/tests/test_domain_introspect.py"
         "source" "import doeff_hy\n"}])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
