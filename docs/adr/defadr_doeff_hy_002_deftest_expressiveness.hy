;;; Executable ADR: deftest の :interpreters / :env 語彙は「部分スタック合成 +
;;; handler 差し替え + env 注入」を消費側シムなしで表現できなければならない。
;;; エージェントが検証を deftest で忠実に表現できないことは、レビュアー(人間)への
;;; 検証報告が歪むことと同義である。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest <-])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [Ask])


(defadr ADR-DOE-HY-002
  :title "deftest expressiveness contract: doeff-hy の deftest は :interpreters / :env / :marks を fixture へ忠実に受け渡し、その語彙だけで『部分スタック合成 + 単一 handler 差し替え + env 注入』が表現できることを契約とする。消費リポジトリが deftest を回避して自前 run_test シムを再発明する状態は本契約の違反シグナルである"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy"
          "packages/doeff-adr/src/doeff_adr/pytest_plugin.py"
          "docs/adr/conftest.py"
          "docs/adr/defadr_doeff_hy_002_deftest_expressiveness.hy"]
  :problem
    [(fact
       "最大級の Python 消費者 mediagen は conftest で doeff_interpreter fixture を配線済みにもかかわらず、260 のテスト関数中 deftest 使用は 0。代わりに tests/support/run_test.py が interpreter スタックの一部を再実装し、手動 with_handlers 差し替えを行う。fixture の :env 経路は NotImplementedError(『deftest :env override is not wired to mediagen_interpreter yet』)のまま。"
       :evidence "mediagen conftest.py / tests/support/run_test.py / mediagen ADR-008 §5 residual(2026-07-14 調査)")
     (fact
       "書き手エージェントに対する検証契約の観点: エージェントが issue の Verification 項目を deftest で忠実に表現できないと、検証の沈黙弱体化(2026-06-12 の PR #472 / #474 事案 — mock 置換・表示のみ検査が green のまま出荷)の再発面になる。deftest の表現力は品質改善ではなく、エージェント主導開発の成立条件である。"
       :evidence "doeff CLAUDE.md『Verification Contract (CRITICAL)』; 2026-07-14 投資計画議論")]
  :context
    [(interpretation
       "消費者がフレームワークの看板テスト機構を配線までして使わないのは、需要の不在ではなく表現力の不足のシグナルである(mediagen はシムを書いてまで同じことをしている)。シムが表現している要求 — 部分スタック + 差し替え + env — を deftest の語彙に吸収するのが所有レイヤの仕事。")
     (interpretation
       "責務境界: doeff-hy は params の忠実な受け渡しを所有し、doeff-adr は収集を所有し、消費リポジトリは doeff_interpreter fixture(env の反映義務を含む)を所有する。doeff 自身の docs/adr conftest fixture はこの fixture 契約の参照実装を兼ねる。")]
  :decision
    [(rule R1 "deftest の :interpreters / :env 語彙で『部分スタック合成 + 単一 handler 差し替え + env 注入』を、消費側の自前シムなしで表現できる。受け入れ基準は mediagen の tests/support/run_test.py が deftest 語彙で置換可能になること。")
     (rule R2 "doeff-hy は deftest params(:env / :interpreters / :marks)を fixture に忠実に受け渡す。fixture 側契約(:env の反映義務、未対応 params の hard fail)を文書化する。")
     (rule R3 "doeff 自身の docs/adr conftest の doeff_interpreter fixture は :env を反映する参照実装とする。fixture が :env を黙って無視することを禁止する(NotImplementedError による fail は許容 — 沈黙よりよい)。")]
  :laws
    [(law deftest-params-are-honored
       :statement "for_all deftest t: env_declared(t, k, v) => Ask(k) == v under_fixture; params_silently_dropped == 0"
       :counterexamples
         [(counterexample "fixture が :env を受け取りながら反映せず、テストが本番 env で走って偶然 green になる")
          (counterexample "deftest を回避した自前シムが interpreter スタックの一部を再実装し、スタック合成の変更(handler 順序等)への追従が漏れる")])]
  :enforcement
    [(deftest test-adr-doe-hy-002-env-roundtrip
       {:env {"adr.doe.hy.002.probe" 42}}
       ;; R2/R3 のピン: deftest の :env が fixture 経由で Ask に届くこと。
       ;; red の場合は params 受け渡しか fixture 反映のどちらかが未配線(どちらも本 ADR の対象)。
       (<- v (Ask "adr.doe.hy.002.probe"))
       (assert (= v 42) f"deftest :env が Ask に届いていない: {v} — ADR-DOE-HY-002 R2/R3"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
