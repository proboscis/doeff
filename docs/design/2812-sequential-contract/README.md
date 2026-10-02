# #2812: Traverse の方針選択と旧 Sequence 案の比較資料

対象: https://github.com/proboscis/agora-redesign/issues/2812

これは設計レビュー用の訂正文書であり、公開 API や handler の実装ではない。A の一般的な推奨は撤回した。
基点は doeff `c025b722ac1508293c1b29cedc3cda4a0c78363a`。
2026-10-02 の開始時に GitHub main とローカル origin/main が一致することを確認した。
開始時のこの基点には #2760 の `read-each` がまだなかった。後の consumer 確認は下記 `2b34c3ddb` を参照する。consumer 移行は担当外とする。

## 推奨の訂正: 処理の意図は Traverse、実行方針は入口の handler

**A を汎用の蓄積処理の解決策として推奨した判断は撤回した。A の承認質問も撤回済み。**
旧案と計測結果は比較資料として残すが、公開 Sequence の実装・consumer 移行の指示ではない。
この訂正も production 実装の承認を意味しない。

論点は append や for loop を helper の中へ隠すことではなく、業務の呼び出し箇所で
traversal の順序・並行性・失敗方針まで固定してよいかである。
A の `Sequence(*programs)` は順次・最初の例外で停止という方針を callsite に埋め込む。
前回は、現行 `sequential()` の失敗蓄積を Traverse 自体の制約と捉えすぎた。

修正版の推奨は、`for/do` / `Traverse` で「各要素にこの処理を適用する」と宣言し、
アプリケーションの入口で handler の構成を一括して選ぶこと（composition root）。
各 protocol 関数の内側で sequential handler を設置する形では同じ問題を再導入する。
当初の互換性を保つなら順次・例外停止を入口で選ぶ。ただし現行 `sequential()` は
失敗を記録して続行するため、そのまま追加するだけでは足りず、適合する handler と
線形な蓄積・性能改善を検討する。並行実行への変更は reader の効果と業務契約を確認してから行う。

既存の根拠:

- [ADR-TRAVERSE-001: Handler = Interpreter / Opaque Results](https://github.com/proboscis/doeff/blob/c025b722ac1508293c1b29cedc3cda4a0c78363a/specs/features/ADR-TRAVERSE-001-applicative-traverse-via-free-monad-on-algebraic-effects.md#L128)
  は、実行順序と失敗方針をロジックの外側の handler が選ぶと規定する。
- [doeff-traverse README: Strategy is handler](https://github.com/proboscis/doeff/blob/c025b722ac1508293c1b29cedc3cda4a0c78363a/packages/doeff-traverse/README.md#L42)
  は、同じ Program を異なる handler で動かす例を示す。
- [parallel_fail_fast](https://github.com/proboscis/doeff/blob/c025b722ac1508293c1b29cedc3cda4a0c78363a/packages/doeff-traverse/doeff_traverse/handlers.py#L350)
  は、失敗方針を handler 側で選べる現存の例。ただしこれを順次版の代用品として採用したわけではない。

### 結果順と効果の実行順は別の契約

#2760 の [read-each と consumer（2b34c3ddb）](https://github.com/proboscis/doeff/blob/2b34c3ddbb9d56d83ce77e1312284dd1efdb26ce/packages/doeff-cluster/src/doeff_cluster/coordinator/protocol/state_json.hy#L148)
は、入力順の結果を zip・dict・tuple に使う。**結果を入力順に並べることと、
各 child の効果を順番に実行することは同じではない。** 並行処理でも結果順は保持できる一方、
副作用の実行順は変わりうる。確認した consumer だけでは後者が業務上必須とは証明できず、
逆に任意の reader を安全に並行化できるとも証明していない。
失敗した行を黙って落とした部分的な状態を成功として返さないという読込の正しさと、
いつ停止・収集するかという実行方針も分けて確認する。

| 選択肢 | 判断・実務上の負担 |
|---|---|
| Traverse の意図を宣言し、入口の handler で方針を選ぶ | 現在の推奨。入口の構成、互換な失敗方針、Collection の取り出し・型・性能を整える必要がある |
| A を順次実行が業務上必須の専用 API として別途提案する | 比較候補としてのみ残す。その必須性と既存抽象では足りない根拠が必要。汎用の蓄積置換としては推奨しない |

次に詰めるのは #2760 / #2871 担当との handler・入口・受入条件の整合である。
以下の旧 A の契約やテストをそのまま Traverse 全般の仕様に昇格させない。
実行可能 ADR / enforcement ledger、Traverse / VM / consumer は今回変更していない。

## 旧 A と当時の sequential handler の比較（履歴）

| 観点 | 旧 A: `Sequence(*programs)` | 基点の `Traverse` + `sequential()`（Traverse 全体の制約ではない） |
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

## 旧 A が提案していた型と動作（未採用・比較用）

以下は撤回前の比較案の記録。公開名 `doeff.Sequence` の追加や実装への合意はない。

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

### 既存の継続契約（再決定しない）

doeff の継続は one-shot で複製不可。Sequence もこの既存契約に従う。
「継続を複製する場合」を初版 API の未決定事項としていた記述は誤りであり、撤回する。
ユーザーに継続複製の方針を選んでもらう必要はない。Program を最初から再実行することは、
消費済みの継続を再開・複製することとは異なり、参照実装は実行ごとに蓄積を新しく作る。

基点 `c025b722ac1508293c1b29cedc3cda4a0c78363a` での根拠:

- [既存仕様 docs/22-capability-classes.md:40–56](https://github.com/proboscis/doeff/blob/c025b722ac1508293c1b29cedc3cda4a0c78363a/docs/22-capability-classes.md#L40):
  同じ k の複数回 Resume は禁止。操作の再試行と継続の再実行を区別する。
- [実装 packages/doeff-vm-core/src/continuation.rs:337–365](https://github.com/proboscis/doeff/blob/c025b722ac1508293c1b29cedc3cda4a0c78363a/packages/doeff-vm-core/src/continuation.rs#L337):
  Continuation は Clone を持たず、Option::take による移動で1回の消費を保証する。
- 既存の構造テスト `tests/core/test_vm_ocaml5_violations.py::test_v28_continuation_not_clone` と
  `::test_v29_no_clone_for_dispatch` が複製の実装を禁止し、実行テスト
  `tests/core/test_vm_architecture_ocaml5.py::test_continuation_is_one_shot` が二度目の再開を拒否する。

旧 A を別途再提案する場合に限り、レビューでこの既存契約を変更していないことを確認し、上記3件と
`tests/design_sequence_2812/test_sequence_contract.py::test_rerun_gets_a_fresh_accumulator`
をその候補実装に対して確認する。現在その実装計画はない。継続複製の新しい仕様・テストは追加しない。
今回の事実訂正では上記4件を実行し、4 passed。限定収集の ADR 警告に加え、
既存の二度 Resume するテストで non-tail Resume 警告と、終了時の
`generator ignored GeneratorExit` 警告を観測した（終了コード0）。
実装や共有環境は変更せず、これらの警告を解消したとはしない。
この既存契約をユーザーに再決定してもらう必要はない。A の承認質問は撤回済み。

## 比較用の検証と、修正版提案に必要な検証

`tests/design_sequence_2812/test_sequence_contract.py` はテスト用参照実装を fixture に差し込んだ実行仕様。
旧 A と基点の handler の差を記録する比較資料として維持する。9 件とも既存の実行環境で成功した。
`sequence_factory` を公開 Sequence に置き換える予定は現在ない。修正版 handler の受入テストでもない。
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
| 基点の sequential handler が失敗後にも進む差異 | `test_existing_traverse_continues_after_failure` |

`typing_contract.py` は T の推論と、E を宣言した Program 型へ代入できる正例を pyright で確認する。
E が途中で Any に劣化しても代入は通りうるため、これだけで効果型の精密な保存を証明したとはしない。
旧 A の追加公開を別途検討する場合でも、この正例だけでは十分でない。
修正版提案では、同じ traversal 宣言に異なる handler を組み合わせても業務コードを変更せずに
実行方針を選べること、結果順と効果の実行順を別々に観測すること、空入力、失敗行の欠落防止、
従来の順次・停止動作との互換性、型・実 consumer の性能を検証する必要がある。
そのテストや handler は本 PR では追加していない。今回 Hy マクロも変更していない。

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

## 旧 A の性能予備計測と未達条件（履歴）

`benchmark_sequence.py` は dict 内包表記、繰返し tuple 連結、参照 Sequence を比較する。
Program を使う 2 方式は、毎回 Program の構築・run・最後の dict 化を含める。
dict 基準は内包表記だけを測る。入力 pairs の作成は各方式とも計測外。
繰返し側は `result = (*result, value)` による tuple 全体の再構築で、issue 原文の
`result + (value,)` とは異なる。どちらも O(n²) だが定数は違うので、表の2.53秒や
参照実装への短縮倍率を、実際の旧 read-each の時間・改善倍率として使わない。
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

## Issue の完了条件との対応（いずれも未完了）

下表は issue 本文の開始時の条件との対応。後の
[c3-w48 のコメント](https://github.com/proboscis/agora-redesign/issues/2812#issuecomment-5948238434)
には「load-state が実データ量と10倍量で #2760 前の1.5倍以内・線形」と別の条件がある。
本文の10倍条件を黙って置き換えず、#2760 / #2871 の担当と対象・基準・計測方法を整合させる。
コメントは A の採用や実装開始の承認とは扱わない。

| Issue の条件 | 今回の証拠 | 残る作業 |
|---|---|---|
| 1. 3 万行の read-each が旧 dict 内包表記の 10 倍以内 | `benchmark_sequence.py` に再実行可能な比較。参照実装の proxy は約 41 倍 | #2760 の確定実装で、同じデータ・同じ環境・正しい VM build の旧/新実装を測る。10 倍を判定する named test を追加 |
| 2. #2760 と伸びうる蓄積箇所を移行 | 今回は consumer の変更なし | #2760 の完了と担当調整後に移行対象を一覧化。順序・失敗時の同値性と各 package の検証を実施 |

これは意図的な設計先行の分割であり、issue を close する PR ではない。
性能を満たさないまま条件を緩めない。A への移行を前提に作業を開始しない。
VM/Rust/do.py/macros の変更は #2816/#2817 と競合するため今回の範囲に含めない。
