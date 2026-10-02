# #2812: 順次 Sequence の API 比較と実行仕様（未決定）

対象: https://github.com/proboscis/agora-redesign/issues/2812

これは設計レビュー用の提案であり、A の採用を記録する ADR でも、公開 API の実装でもない。
基点は doeff `c025b722ac1508293c1b29cedc3cda4a0c78363a`。
2026-10-02 の開始時に GitHub main とローカル origin/main が一致することを確認した。
#2760 は open で、この基点には対象の `read-each` がまだない。consumer 移行は担当外とする。

## 推奨と判断が必要な点

**A の「公開ヘルパーによる、順次実行・最初の例外で停止」を推奨する。**
新しい VM 命令はこの結論の前提にしない。まず既存の Program 合成で契約を表し、性能を別途判定する。
B は既存 handler の追加だけでは元の失敗動作を保存できない。

maintainer に残る判断は、(1) 追加公開 API と下記の契約を採るか、
(2) Python 合成で性能条件に届かなければ core/VM 側の最適化を別途許可するか、である。
issue の「推奨」は「決めること」に置かれており、開始時に承認コメントはなかった。
自動的に A を承認済みと扱う根拠はない。一方、この比較・実行仕様・Draft PR を作るための追加承認は不要。
本提案では不変条件の追加をまだ発効させないため、実行可能 ADR / enforcement ledger は変更しない。
採用後の実装では ADR・必要な検査・ledger を同時に整え、AGENTS の TDD 手順に従う。

## 既存コードとの比較

| 観点 | A: `Sequence(*programs)` の提案 | B: 現行 `Traverse` + `sequential()` |
|---|---|---|
| 入力 | `Program[T, E]` の可変長引数 | 値の列と `T -> Program[U, Any]` の関数 |
| 結果 | `Program[tuple[T, ...], E]` | effect の答えは `Collection[U]` |
| 順序 | 前の child が完了してから次を開始 | 順次 handler なら入力順 |
| 例外 | 同じ例外を外へ伝播し、後続 child は開始しない | handler が child を `Try` で包み、失敗を記録して後続へ進む |
| 空入力 | `()`、専用 handler 不要 | 空 Collection、Traverse handler は必要 |
| handler | 子が必要とする既存 handler を継承。Sequence 専用 handler 不要 | 入口に collection handler が必要。順序・失敗方式は handler が決める |
| 取り出し | tuple をそのまま利用 | 公開 effect の `Inspect` / `Reduce` で取得。失敗の扱いも決める必要がある |
| 型の効果集合 | 子の E をそのまま外側へ保存 | 現行 `.pyi` の f は `Program[U, Any]`。子の E を精密には保存しない |

根拠となる既存定義:

- `doeff/__init__.py`: `Program[T, E]` は `__iter__ -> Generator[E, Any, T]`。
  `merge_dicts` は既存の公開 `@do` 合成ヘルパーの先例。
- `docs/23-static-typing.md`: `yield from` が値型 T と効果型 E を伝える。
  `EffectGenerator[T]` では E が Any になるため、精密な宣言には使わない。
- `packages/doeff-traverse/doeff_traverse/handlers.py::sequential`: child ごとに Try。
  `Reduce` は失敗した要素を除外する。従って事後に最初のエラーを投げても、実行済みの後続処理は取り消せない。
- `packages/doeff-traverse/doeff_traverse/effects.pyi`: Traverse の値型と callback の Any 効果型。
- `packages/doeff-traverse/doeff_traverse/collection.py`: Python では反復・valid_values・errors も公開されている。
  「技術的に中身を読めない」とは言わない。ただし設計上は opaque とされ、通常の取り出しは effect 経由。
  `tuple(collection)` は失敗を落とすため元の読込の置き換えにはできない。
- `packages/doeff-core-effects/doeff_core_effects/scheduler.py::Gather`: 現行 Gather は
  `Task[T] | Future[T]` を受けて list を返す。Program 列の直列評価ではない。
  スキルの古い `Gather(p_items)` 例は、この基点の API の根拠として使っていない。

## 提案する型と動作

公開する場合の候補名は issue に合わせて `doeff.Sequence`。実装方法は未決定。

```python
# 公開口の意図を表す signature（コードはまだ export しない）
def Sequence(*programs: Program[T, E]) -> Program[tuple[T, ...], E]: ...
```

- 同じ型の列を中心に扱う。異種入力は値型の union を持つ可変長 tuple。
  位置ごとの異種 tuple 推論（TypeVarTuple）は初版の必須条件にしない。
- `Sequence()` の値は必ず `()`。無効果を明示する空引数 overload は採用時に
  `Program[tuple[()], Never]` として検証する（本稿の型検査は非空の T/E 保存が対象）。
- Program protocol に適合する child を入力とする。`Pure`、`@do` の結果、型上同じ protocol を満たす effect を含む。
  生の値を暗黙に Pure に変換する API にはしない。
- 呼び出しは child を実行しない。引数を作る Python/Hy 式自体は通常どおり先に評価される。
  `Sequence(*generator)` も呼出時に generator を消費するため、入力の遅延走査 API とは区別する。
- 入力位置ごとに 1 回評価し、重複・None・入れ子 tuple を保存する。結果を flatten しない。
- 1 つの child の継続が完了するまで次を開始しない。helper 自身は Spawn / Gather / Try を加えない。
  child 自身が並行タスクを起動することまで禁止しない。
- child の通常の例外・未処理 effect は外へ伝播する。成功済み child の効果は rollback しない。
  外側が復旧して Sequence 自体を再実行する場合は、新しい実行として扱う。
- child の effect は呼出側の handler に届き、child 内のローカル handler はその child の範囲に留まる。
  Sequence 専用 handler が不要という意味であり、子の effect の handler まで不要という意味ではない。
- 蓄積の追加計算量を O(n)、追加メモリを O(n) にする。child 自体の時間・メモリは別。
  参照実装はテスト内の list + 最後の tuple 化。production の可変蓄積の例外許可を既成事実にしない。
  handler の継続を複製する場合の可変状態共有も、実装方式の決定後に別途検証する。

## 再利用できる検証

`tests/design_sequence_2812/test_sequence_contract.py` はテスト用参照実装を fixture に差し込んだ実行仕様。
採用後は `sequence_factory` を公開 Sequence に置き換える。9 件とも現行 VM で成功した。
これは新 API の存在・性能・移行完了を証明するものではない。

| 契約 | テスト |
|---|---|
| 空入力・専用 handler 不要 | `test_empty_requires_no_handlers` |
| 入力順・重複・None | `test_order_duplicates_and_none` |
| 遅延開始・前の child 完了後に次を開始 | `test_children_are_lazy_and_finish_before_next_starts` |
| 同じ例外の伝播・後続非実行 | `test_first_error_propagates_and_later_child_never_runs` |
| 外側 handler と child 内の scope | `test_children_use_callers_reader_and_nested_reader` |
| 未処理 effect を飲み込まない | `test_unhandled_child_effect_is_not_swallowed` |
| 入れ子の形 | `test_nested_sequences_preserve_tuple_shape` |
| 再実行時に蓄積を共有しない | `test_rerun_gets_a_fresh_accumulator` |
| B が失敗後にも進む差異 | `test_existing_traverse_continues_after_failure` |

`typing_contract.py` は T の推論と E の保存の正例を pyright で確認する。
実装時は空入力、異種入力、非 Program 入力の拒否、呼出側の E 漏れの負例、
Hy の `<-` による型認識も追加する。今回 Hy マクロは変更していない。

実行例（既存環境のみ利用、install/sync は行わない）:

```sh
PYTHONPATH="$PWD:$PWD/packages/doeff-core-effects:$PWD/packages/doeff-traverse" \
  /Users/kento/repos/doeff/.venv/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/design_sequence_2812/test_sequence_contract.py
/Users/kento/repos/doeff/.venv/bin/ruff check --no-cache tests/design_sequence_2812
/Users/kento/repos/doeff/.venv/bin/pyright --pythonpath /Users/kento/repos/doeff/.venv/bin/python \
  tests/design_sequence_2812/test_sequence_contract.py \
  tests/design_sequence_2812/typing_contract.py
```

pytest: 9 passed。ruff: 成功。pyright: 0 errors。
root conftest の VM invariant checks を有効にしたまま実施。
限定収集のため実行可能 ADR が未収集という既存プラグインの警告が 1 件あり、全 ADR の検証済みとはしない。
最初の pyright は Python 環境未指定で pytest import が解決できず、上の `--pythonpath` 指定で成功した。

## 性能の予備計測と未達条件

`benchmark_sequence.py` は dict 内包表記、繰返し tuple 連結、参照 Sequence を比較する。
毎回 Program の構築・run・最後の dict 化を含める。入力 pairs の作成は各方式とも計測外。
各方式 warm-up 1 回 + 5 回の中央値。CPU は process_time、wall は perf_counter。
VM invariant checks は明示的に有効。macOS / CPython 3.14.3 free-threaded の既存環境。

| 件数 | dict CPU ms | tuple 連結 CPU ms | 参照 Sequence CPU ms | 参照 wall ms | 参照 / dict |
|---:|---:|---:|---:|---:|---:|
| 300 | 0.009 | 0.644 | 0.487 | 0.487 | 54.1 |
| 3,000 | 0.132 | 28.911 | 5.200 | 5.215 | 39.4 |
| 10,000 | 0.364 | 283.806 | 18.132 | 18.169 | 49.8 |
| 30,000 | 1.298 | 2527.926 | 53.228 | 53.331 | 41.0 |

線形化は有望だが、この proxy では 10 倍以内ではない。
issue の Zeus での旧実装 5.6 ms と Mac の 53 ms を割って合格扱いしてはならない。
今回の row は Python `@do` の恒等処理であり、#2760 の実際の defk/read-each ではない。
したがって本番対象の性能条件の合否はまだ未検証である。

また、既存の VM バイナリは共有 checkout の
`doeff_vm.cpython-314t-darwin.so`（mtime 2026-10-02 08:12 JST）を読み込んだ。
main の Rust はその後も変化しており、バイナリとこの基点の対応は確認できていない。
共有環境の再構築は禁止範囲なので実施していない。
これは現行インストール環境での設計比較の証拠に限り、最新 main の受入検証の代用にはならない。
性能を受け入れる前に専用の正しいビルド環境で再測定する必要がある。

## Issue の完了条件との対応（どちらも未完了）

| Issue の条件 | 今回の証拠 | 残る作業 |
|---|---|---|
| 1. 3 万行の read-each が旧 dict 内包表記の 10 倍以内 | `benchmark_sequence.py` に再実行可能な比較。参照実装の proxy は約 41 倍 | #2760 の確定実装で、同じデータ・同じ環境・正しい VM build の旧/新実装を測る。10 倍を判定する named test を追加 |
| 2. #2760 と伸びうる蓄積箇所を移行 | 今回は consumer の変更なし | #2760 の完了と担当調整後に移行対象を一覧化。順序・失敗時の同値性と各 package の検証を実施 |

これは意図的な設計先行の分割であり、issue を close する PR ではない。
性能を満たさないまま条件を緩めたり、A の採用を宣言したりしない。
VM/Rust/do.py/macros の変更は #2816/#2817 と競合するため今回の範囲に含めない。
