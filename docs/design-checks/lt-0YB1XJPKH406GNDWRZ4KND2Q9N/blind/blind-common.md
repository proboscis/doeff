# 盲検 A・B に共通して渡す材料

## 読む物

- 固定した要件・責務・公開契約・将来の変更についての主張・強制方法: あなたの作業木の
  `docs/design-checks/lt-0YB1XJPKH406GNDWRZ4KND2Q9N/design-before-blind.md`(この文書と同じ dir の 1 つ上)。
  主張と前提はこの文書のとおりで、ここに書いていない主張はしていない。
- 実コード(あなたの作業木の中・版は作業木の HEAD):
  - `packages/doeff-hy/src/doeff_hy/macros.hy` — `_in-function-body?`・`_scoped-definition`・`_helper-imports`・定義の macro
    (`defeffect`・`deff`・`defk`・`defp`・`defpp`・`deftest`・`defsystem`・`defpipeline`・`defmcp-tool`)
  - `packages/doeff-hy/src/doeff_hy/handle.hy` — `defhandler`・`handle`(macros.hy が require して出し直す)
  - `packages/doeff-hy/src/doeff_hy/declarations.hy` — `defeffect-form`・`declaration-setters`
  - `packages/doeff-hy/src/doeff_hy/system_form.hy` — `defsystem-form`
  - `packages/doeff-hy/src/doeff_hy/pytest_items.py` — pytest の item の記録を書く側
  - `packages/doeff-adr/src/doeff_adr/lazy_collection.py` — 記録を読む側(`verify_records`・`check_no_unrecorded_items`)
- 検(強制方法の実体):
  - `tests/test_nested_definition_scope.py` — `_DEFINITION_SAMPLES`・`test_every_definition_macro_has_a_nested_sample`・
    `test_function_with_only_a_definition_binds_only_its_names`・`test_nested_definition_runs`・
    `test_outer_function_binds_no_helper_name`・`test_nested_definition_leaves_module_globals_unchanged`・
    `test_class_body_definition_runs_without_module_definitions`・`test_module_and_class_level_definitions_are_not_wrapped`
  - `tests/test_handle_scope.py` — handle・defhandler の同じ類の検
  - 検の対象の範囲: pytest の testpaths の `tests`(日次の全体の回と、手で名指した時に走る)。

## 作業木と実行

- あなた専用の作業木(他の会話と共有しない・`.venv` は作り済み)を下の dir に用意した。この木の file は実験のために
  書き換えてよい。commit・push・branch の作成はしない。返答の後で、設計者が木の差分を読む。
- Python は必ず uv を通す: 作業木を cwd にして `uv run --frozen python …`・`uv run --frozen pytest <file> -q -W ignore`。
  `.venv/bin/python` を直に呼ばない。機体の `python3` を使わない。
- 検は上に挙げた file だけを名指して走らせる(全数の検・dir 単位の検はしない)。1 回の命令は 5 分以内に収める。
- 実験の script・出力は `/tmp/<下の dir 名>/` に置き、最後の返答にその path を書く。
- 他の会話への連絡・GitHub への書き込み・作業木の外の共有の file の変更はしない。
