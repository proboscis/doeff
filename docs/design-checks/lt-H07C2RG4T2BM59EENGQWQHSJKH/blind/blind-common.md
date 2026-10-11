# 盲検の共通の材料(依頼 lt-H07C2RG4T2BM59EENGQWQHSJKH)

## 対象

- repo: doeff。読み取り用の作業木: `/home/ubuntu/.worktrees/doeff-blind-3293bc4cc`(版 `3293bc4cc357ec9f6ed02e07d4facf75de0ae882` そのもの・`.venv` を作り済み)。
- 変更の前の版: `a2254d66cfc52f9fa9c3b4829bff2d76191bccb3`。変更の差分は `git -C /home/ubuntu/.worktrees/doeff-blind-3293bc4cc show 3293bc4cc` で読める。
- 対象の file:
  - `packages/doeff-traverse/doeff_traverse/effects.py`・`effects.pyi`(`ItemOutcome`・`ItemOutcomeKind`)
  - `packages/doeff-traverse/doeff_traverse/handlers.py`・`handlers.pyi`(sequential・parallel・parallel_fail_fast・`_names_item_outcome`・`_report_boundaries`・`progress_bars`・`ProgressBar`・`BarFactory`)
  - `packages/doeff-traverse/examples/ex08_tqdm_progress.hy`
  - `packages/doeff-traverse/tests/test_item_outcomes.py`
  - `packages/doeff-effect-analyzer/tests/python/test_carried_builders.py`(`test_doeff_traverse_item_outcomes_stay_inside_the_handler`)
  - 依存先(読むだけ): `packages/doeff-traverse/doeff_traverse/collection.py`、`doeff/handler_utils.py`、`doeff/program.py`、`packages/doeff-vm/doeff_vm/_effect_types.py`(`handler_effect_types`)、`packages/doeff-effect-analyzer/python/doeff_effect_analyzer/handler_effects.py`、`packages/doeff-cluster/src/doeff_cluster/foundation/foundation_check.hy`

## 要件(固定)

- R1: doeff-traverse の sequential・parallel・parallel_fail_fast の 3 つが件ごとの結末を公開の契約で出し、その契約が doeff の main に在る。
- R2: 失敗する件のある pipeline で tqdm-progress の bar が n == total で閉じる事を確かめるテストが doeff の main に在る。
- 背景: doeff-traverse の for/do・Traverse は本番の系(agora-controllers)で広く使われ、本番の job は doeff-cluster の foundation closure(effect-analyzer で handler の節を静的に読み、答えの無い効果が残らないかを見る)で検められる。Traverse の handler の件ごとの費用は詰めてきた(agora-redesign #2871・#2958)。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 利用者の pipeline | 何を計算するか(for/do・Traverse を出す Program) | 段の並び・件の値 | 実行の順・並列・進捗・結末の知らせ | `Traverse(f, items, label)` を出す Program |
| M2 結末の消費者 | 件の結末をどう数え・見せるか(`progress_bars`・ex08 の `tqdm-progress`・利用者の handler) | bar の開閉・数え方・表示 | なし(M3 の内部の規則を持たない) | `ItemOutcome` を効果の引数の注記で名指す handler。`progress_bars(make_bar: BarFactory)` |
| M3 Traverse の handler | 件をどう走らせ、結末をどう決めて知らせるか(sequential・parallel・parallel_fail_fast) | 結末の分類(ok・failed・skipped・carried)・持ち越しの規則・Skip・例外の捕まえ方・知らせる時と場所・名指す handler が在るかの判定 | 件の Program の組み立て・内側の handler の付け直し・並列の task | Traverse の答え `Collection` と、件ごとの `ItemOutcome` |
| M4 公開の語彙 | 結末の型 | `ItemOutcome(traverse, label, total, outcome, item)`・`ItemOutcomeKind`・`ProgressBar`・`BarFactory` | なし | `effects.py`・`effects.pyi`・`handlers.pyi` |
| M5 静的な閉じの検査 | Program と handler の stack に答えの無い効果が残らないか | handler の節が出す効果の読み | なし | effect-analyzer の `analyze_handler`・foundation closure |
| M6 検 | 契約の検 | 期待する結末の列 | なし | 下の表 |

M3 の公開の約束(`effects.py` の `ItemOutcome` の docstring と README の節 Counting item outcomes):

- 件の結末が決まるたびに `ItemOutcome` を出す(parallel は終わった順・走らせる件の前の持ち越しは最初の件を走らせる時)。
- 出す所は、Traverse と handler の間の handler(件の Program に付け直す内側の handler)の下で、その外に handler 自身が居て、誰も受けなければ None で答える。handler の外へは出さない。
- 内側の handler のどれかが効果の引数の注記に `ItemOutcome`(か subclass)を名指す時だけ出す。
- 走らせる件の無い Traverse(件 0・持ち越しだけ)は出さない。parallel_fail_fast は最初の失敗で止まり、取り消した件は出さない。

## 変更シナリオと設計者の主張

- S1 concurrency: 実行の方針を sequential・parallel(N)・parallel_fail_fast の間で替える(進捗の handler は付けたまま)。主張: 変わるのは M2 の stack の 1 要素だけ。M1・M3・M4 は変わらない。数える件・total・bar の閉じ方は同じ(parallel_fail_fast が止めた Traverse を除く)。前提: 進捗の handler が Traverse の handler の内側に在る。
- S2 effects: 件の結末の新しい消費者を足す(例: 段ごとに失敗した件と理由を記録する handler、tqdm を別の表示へ替える)。主張: 変わるのは M2(handler を 1 つ足す・stack に 1 要素足す)だけ。M3 の規則(持ち越し・Skip・例外)を M2 へ写さない。M1・M3・M4 は変わらない。前提: 消費者が注記で `ItemOutcome` を名指す。
- S3 simulation: 決定的な模擬・検で、bar を記録係に替える・結末を記録して比べる。主張: 変わるのは M2 の `make_bar` の差し替え(か記録用の handler)だけ。M3 は変わらない。前提: `make_bar` が `BarFactory` の形。
- storage・hardware・distribution は対象外(保存の層が無い — 保存するなら S2 の消費者 1 つ・機器に依らない・cluster へ件を配る Traverse の handler は無い)。
- 前提のまとめ: 進捗の handler は Traverse の handler の内側に置く・消費者は注記で `ItemOutcome` を名指す・Traverse の handler は 3 つだけ。

## 検査の実体と検出範囲

| 検査 | 実体 | 検出範囲 | 実行経路 |
|---|---|---|---|
| 知らせが handler の外へ漏れない | `test_carried_builders.py::test_doeff_traverse_item_outcomes_stay_inside_the_handler`(analyzer の読みで、3 つの handler のどの節も `ItemOutcome` を出さず、`ItemOutcome` に答える節を持つ) | 3 つの handler(検の parametrize の表) | pytest(日次の全体・手の focused) |
| 知らせの中身が答えと同じ | `test_item_outcomes.py::test_each_item_reports_the_outcome_the_traverse_answers`(sequential・parallel) | 6 件 2 段の pipeline | pytest |
| bar が total で閉じる | `test_item_outcomes.py::test_progress_bars_close_at_total_when_items_fail` ほか | sequential・parallel・parallel_fail_fast(失敗の無い時) | pytest |
| 名指す handler が無い stack の動きは今のまま | `test_nothing_is_reported_unless_a_handler_inside_names_item_outcome`・`test_sequential_reads_handlers_once.py`・`test_traverse_effect_type_filter.py` | sequential・parallel | pytest |
| 型 | `effects.pyi`・`handlers.pyi` | pyright(日次の全体だけ・commit ごとには走らない) | — |
| 着地の検査 | 衝突の印の走査と uv sync・pre-commit(semgrep・doeff-linter の基点比較) | 変えた file | 着地の時・commit の hook |

## 走らせ方

- Python は uv を通す: `cd /home/ubuntu/.worktrees/doeff-blind-3293bc4cc && uv run python <file>`、Hy は `uv run python -m hy <file>.hy`。
- 検: `cd /home/ubuntu/.worktrees/doeff-blind-3293bc4cc && uv run pytest -q packages/doeff-traverse/tests/test_item_outcomes.py packages/doeff-effect-analyzer/tests/python/test_carried_builders.py`(約 5 秒)。全体の検は走らせない。
- analyzer で handler を読む: `uv run python -c "import sys; sys.path.insert(0, 'packages/doeff-effect-analyzer/python'); from doeff_effect_analyzer.handler_effects import analyze_handler; ..."`
- 作業木の file は変えない。自分の実験の file は指定の `/tmp/<dir>/` に置く。作業木の写しが要る時は `/tmp/<dir>/` の下に `git -C /home/ubuntu/.worktrees/doeff-blind-3293bc4cc archive HEAD | tar -x -C /tmp/<dir>/tree` で作る(写しの環境は `uv run --project /home/ubuntu/.worktrees/doeff-blind-3293bc4cc` で借りられる)。
