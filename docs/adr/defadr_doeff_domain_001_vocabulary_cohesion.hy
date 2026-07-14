;;; Executable ADR: FP の「純粋だが所属不明の小関数の森」問題への doeff の答えは、
;;; 凝集を「語彙 + 法」の宣言レベルで結合する defdomain と、エージェントが照会できる
;;; SEDA(CLI/MCP)、そして適合検査である。エージェントにとってのドキュメントとは
;;; 失敗する検査のことである。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-DOMAIN-001
  :title "vocabulary cohesion: doeff-hy は凝集単位の宣言 defdomain(effect 型の束 + laws + 期待 handler 面)を提供し、SEDA は CLI/MCP としてエージェントから照会可能にし、適合検査(handler の domain 被覆・Program 使用 effect 集合 ⊆ interpreter 処理集合・domain 外の同義語彙定義の禁止)を defsemgrep/deftest で提供する。IDE 面は非優先 — 書き手はエージェントである"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-effect-analyzer"
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
       "エージェントにとってのドキュメントとは失敗する検査のことである。domain 宣言は (1) エージェントに最初に読ませる 1 ファイルであり、(2) 適合検査の照合対象である。docs は読み飛ばされるが red は必ず読まれる。")]
  :decision
    [(rule R1 "doeff-hy は凝集単位の宣言マクロ defdomain を提供する: effect 型の束、domain laws、期待される handler 面(どの handler 群がこの語彙を被覆すべきか)を 1 宣言にまとめる。")
     (rule R2 "SEDA は CLI および MCP tool としてエージェントから照会可能にする: 『この Program が実行しうる effect 集合』『この effect 型の handler 所在』『この interpreter の処理集合』。IDE プラグイン面は非優先とする。")
     (rule R3 "適合検査を defsemgrep / deftest で提供する: (a) handler が宣言 domain の全 effect を被覆する、(b) Program の使用 effect 集合 ⊆ 実行 interpreter の処理集合、(c) domain 宣言の外での同義語彙(同一意味論の述語・effect)の定義を禁止する。")
     (rule R4 "本 ADR の実装は計画 Stage E であり、Track B(pytest 正典ゲート)の完了を前提とする — 適合検査は走るゲートがあって初めて意味を持つ。")]
  :laws
    [(law vocabulary-has-single-home
       :statement "for_all semantic_predicate_or_effect v: canonical_declaration_count(v) == 1 AND declared_in_some_domain(v); duplicate_definitions_detected_by_conformance_check"
       :counterexamples
         [(counterexample "ACP 核#8: terminal 述語 7 定義・terminal-set 6 定義が並存し、判定が呼び出しサイトごとに発散(2026-07-02 監査で検出、何も fail していなかった)")
          (counterexample "新規エージェントが既存 domain 語彙を発見できず、同義の effect 型を別パッケージに追加する — 照会面(SEDA)不在時の既定挙動")])]
  :enforcement
    [(deftest test-adr-doe-domain-001-defdomain-exists
       ;; RED(2026-07-14): defdomain マクロは未実装。R1 実装で green。
       ;; ソーステキストのピン(マクロは module 属性にならないため)。実装後は挙動ピンに昇格する。
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv macros-src (.read-text (/ root "packages" "doeff-hy" "src" "doeff_hy" "macros.hy")))
       (assert (in "defmacro defdomain" macros-src)
               "defdomain マクロが doeff-hy に存在しない — ADR-DOE-DOMAIN-001 R1"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
