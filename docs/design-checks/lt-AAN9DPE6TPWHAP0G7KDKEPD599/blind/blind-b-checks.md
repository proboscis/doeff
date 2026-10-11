# 盲検 B に示す検査(版 1021b8946 の時点の実体)

作業木の根で `make sync` の後に撃つ。どれも触った file に絞る。

| 検査 | 命令 | 設定・対象 | 何を拒むか |
|---|---|---|---|
| scheduler の契約の検 | `uv run pytest tests/test_scheduler.py tests/test_scheduler_trace.py tests/test_scheduler_implementation.py -q` | tests/ の 3 file | 振る舞いの検(Spawn・Wait・Gather・Race・Cancel・Discard・印の shield #505・deadlock の診断・root の close-out・sweep)と、`TestTimedWaitWorkDoesNotGrowWithParkedTasks` の 2 条(1 回の WaitWithin の scheduler.py の行の数 — 待つ task 256 本で 16 本の 1.1 倍以内・NORMAL の仕事が並ぶ間の WaitWithin 1,000 回の後の走れる列が 250 未満)|
| ruff | `uv run ruff check packages/doeff-core-effects/doeff_core_effects/scheduler.py tests/test_scheduler.py` | pyproject.toml の [tool.ruff] | 書き方の規則(main の時点で 6 件の既存の指摘が在る — 新しく増やさない事を見る)|
| semgrep(commit の hook と同じ規則)| `uv run --no-project --python 3.12 python scripts/semgrep_locked.py --metrics=off --disable-version-check --config .semgrep.yaml packages/doeff-core-effects/doeff_core_effects/scheduler.py --error` | `.semgrep.yaml` | repo の設計の規則(層の境・effect の作法など)|
| pyright(日次の全体の回でだけ撃つ — 参考)| `uv run pyright packages/doeff-core-effects/doeff_core_effects/scheduler.py` | pyproject の pyright の設定 | 型 |

検出の範囲の申告(§4 の限界と同じ): `waiters`・`ready` を入口の関数の外から直に書き換える事を止める静的な規則は無い。索引・数えの不変条件(design.md §2 の I-W1・I-W2・I-R1・I-P1)を直に照らす検は無い。
