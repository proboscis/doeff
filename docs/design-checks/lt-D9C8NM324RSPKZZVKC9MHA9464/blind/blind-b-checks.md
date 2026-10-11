# 盲検 B にだけ足す材料 — 検査の実体・設定・検出範囲

| 検査 | 実体 | 実行の命令(cwd = 読む木) | 検出範囲 |
| --- | --- | --- | --- |
| 環境変数の形の検 | `packages/doeff-hy/tests/test_macro_use_record.py` の `test_a_macro_reading_a_value_decided_by_the_environment_is_stale_when_it_changes[形]`(形 9 つ: table・listed・aliased・got・built・py-table・direct・helper・local — 中身は同じ file の `ENV_FILES`)・`test_macros_that_do_not_read_the_environment_stay_checkable_beside_ones_that_do`・`test_a_row_of_an_environment_read_is_stale_even_when_the_walk_now_compares_files`・`test_a_changed_environment_value_is_stale_in_a_second_process` | `cd packages/doeff-hy && uv run --frozen pytest -q -p no:cacheprovider tests/test_macro_use_record.py -k environment` | 子の process で使い手を読み、環境変数を替えた次の子の process が compile し直して新しい値を使うか。検の形の外の読みは縛らない |
| 照らしの他の検(focused) | doeff-hy の 9 file: `tests/test_macro_use_record.py tests/test_bound_source_store.py tests/test_bytecode_guard.py tests/test_dataclass_code_store.py tests/test_gensym_canonical.py tests/test_static_check_locate.py tests/test_static_cache.py tests/test_static_check_pyright_python.py tests/test_import_gc.py` | 同じ dir で `uv run --frozen pytest -q -p no:cacheprovider <9 file>`(約 8 分 — 走らせなくてよい) | 照らしの冷えるべき時・冷えなくてよい時・process をまたぐ覚え・置き場 |
| ruff | 根の pyproject.toml の ruff の設定 | `uv run --frozen ruff check <file>` | 書式と lint |
| 変更範囲の品質検査 | dotfiles の code-quality の fast の段(着地の前) | (走らせない — 構造の規則で、型検査を含まない) | import の向き・層の宣言(architecture.hy)など |
| pyright | 日次の全体の段だけ | (走らせない) | 型 |
