# 盲検 B に渡す検の一覧(実体・設定・検出範囲)

| 検 | 実体 | 設定・入力 | 実行の経路 | 検出する範囲 |
| --- | --- | --- | --- | --- |
| 例の検の入力が例の検を選ぶ | `tests/test_contract_table_follows_packages.py::test_changing_an_example_or_its_macros_selects_the_examples_check` | 本物の `pyproject.toml` の表・入口の `select_targets`・定数 `EXAMPLES_CHECK`・`EXAMPLES_DIR`(glob `*.hy` と `expected/*.txt`)・`EXAMPLE_MACROS`(macros.hy) | 入口の契約の組の repo 全体の行(どの差分でも走る)・日次の root の段 | 例 1 つ・期待の出力 1 つ・macros.hy のどれかを変えた差分で、例の検が選ばれない表 |
| 表の読み | `scripts/run_changed_tests.py` の `read_contract_table`・`_parse_row` | 行の鍵(`prefix`+`tests` か `prefix`+`reason`)・接頭辞の dir / file が木に在る・書いた検の file が在る・同じ接頭辞の行が 2 つ無い・同じ検を 2 度書かない | 入口の起動の時(rc 2 で止まる)・下の 2 つの検 | 形の崩れた表・無い検の名指し |
| 表と package の増減 | `tests/test_contract_table_follows_packages.py` の他の検(`test_every_package_has_a_contract_row_or_a_reasoned_exemption` ほか・模型の写しの検) | uv の workspace の member | 同上 | 組も理由つきの除外も無い package |
| 本物の表が読める | `tests/test_run_changed_tests.py::test_the_repository_table_reads_and_names_existing_tests` | 本物の表 | 日次の root の段(入口では、この file か入口を変えた時) | 読めない表・無い検 |
| 例の検そのもの | `packages/doeff-traverse/tests/test_examples_run.py` | examples/*.hy の glob・期待の出力・BROKEN・OUTPUT_NOT_COMPARED・macro の持ち主の確かめ | 入口(表の行 `packages/doeff-traverse/` と `packages/doeff-hy/src/doeff_hy/`)・日次の段 packages | 走らない例・期待と違う印字・例の中の macro の定義と doeff-hy の外からの require |
| commit の時の検査 | `.pre-commit-config.yaml` の semgrep・doeff-linter | stage した Python と Hy の file | `git commit` の hook | この変更の範囲の振る舞いは見ない(規則の所見だけ) |

マージの列(`.agents/land-queue.toml`)は検を走らせません(門は衝突の印の走査だけ)。上の検は、入口を書き手が登記の前に走らせた時と、日次の全体検証でだけ走ります。
