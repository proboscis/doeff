# 材料(盲検 A・B に同じ物を渡す)

対象 repo: doeff。読む作業木: /home/ubuntu/.worktrees/doeff-wt-dc-9r40(版 3198e5548 — 対象の変更 77e0eb3f9 を含む)。
この作業木は**読むだけ**にしてください(file を書かない・git を動かさない)。試す file が要るなら /tmp/blind-9r40-<A か B>/ の下にだけ作ってください。
pytest の全体・日次の全体の検は走らせないでください。

## 変更(77e0eb3f9)

tests/test_daily_test_population.py の除外の表 EXCLUDED に 1 行を足した:

    "docs/design-checks/": (
        "設計検証の記録の証拠(盲検の再現・直した版の検の写し。その時点の記録で、日次の母集団ではない)"
    ),

背景: 設計検証の記録(docs/design-checks/<依頼 id>/)の証拠 evidence/scripts/test_handle_scope.revision.py(直した版の検の写し)が
test_*.py の名で置かれ、test_every_test_file_belongs_to_a_daily_population が赤になっていた。この検は root の pyproject.toml の
[[tool.doeff.contract-tests]] の接頭辞 ""(repo 全体)の行に載り、make test-changed はどの差分でもこの検を走らせる。

## 要件

- R1: doeff main で test_every_test_file_belongs_to_a_daily_population が緑。
- R2: 設計検証の記録に test_*.py・test_*.hy の名の file を置いても、この検が赤にならない形にする。
- R3: 日次の母集団の検の本来の役目(検のつもりの file が日次のどの母集団にも属さず黙って走らない形を赤にする — ADR-DOE-ENFORCE-001 R8)を弱めない。

## 責務

- M-pop = tests/test_daily_test_population.py: 「検の file」の定義(_test_file_patterns)・母集団の根の読み(root の testpaths・packages/<p>/tests・Makefile の PACKAGE_EXTRA_TEST_ROOTS)・除外の表 EXCLUDED(path か `/` で終わる dir の接頭辞 → 理由)。契約: 検の file は根の下か除外の行の下。除外の行は今の木の検の file を 1 本以上覆う。
- M-records = docs/design-checks/<依頼 id>/(設計検証の記録): その時点の証拠(design.md・報告 JSON・盲検の入出力・再現の script と log)。着地の後は書き換えない。日次の母集団ではない。
- M-roots = root の pyproject の testpaths・Makefile の PACKAGE_EXTRA_TEST_ROOTS: 日次に走る根。
- M-contract = root の pyproject の [[tool.doeff.contract-tests]]: どの差分でどの検を必ず走らせるか。
- M-seal = tests/test_sealed_records_are_named.py の RECORD_KINDS: 封の記録の dir の種類(design・design-checks)。

## 将来の変更と主張

- S1(保存の置き場): 新しい設計検証の記録が evidence/ や blind/ の下に test_*.py・test_*.hy の証拠を置く → 変えるのは記録の新しい dir だけ。M-pop・M-roots・M-contract・M-seal は変えない。前提: 記録の中の検の名の file は写しで、日次で走らせる物ではない。
- S2(保存の置き場): 新しい種類の記録の dir(例 docs/design-reviews/)を作り検の名の file を置く → M-pop の EXCLUDED に理由つきの 1 行を足す。足すまでは赤。
- S3(並行): 複数の会話が同時に別々の設計検証の記録を足しマージの列へ並べる → どの会話も M-pop を触らないので同じ行で衝突しない。
- S4(外の作用): 記録の外の dir(例 docs/notes/・scripts/)に検の名の file を置く → 今までどおり赤。除外は docs/design-checks/ の接頭辞ちょうど。

## 検査の実体

- tests/test_daily_test_population.py の test_every_test_file_belongs_to_a_daily_population・test_exclusion_table_has_no_stale_rows・test_every_test_named_file_is_collected_by_some_collector。
- 実行経路: make test-changed(scripts/run_changed_tests.py が [[tool.doeff.contract-tests]] の接頭辞 "" の行でどの差分でも走らせる)と、日次の全体検証の root の段(.agents/land-queue.toml の gate.full)。
- 単独で走らせる命令(読む作業木で): `uv run --no-sync pytest tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population -q`
