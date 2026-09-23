# ハンドラ合成記事の実装照合

対象: `doeff-handlers.md`、`examples/handler_composition.py`

確認日: 2026-09-16

担当: 記事別レビュー担当 `review_handlers`

## 作成・修正した内容

- 新記事として、異なる操作のハンドラの合成、同じ操作の解釈の追加、順序、局所的な適用、別エフェクトへの翻訳を説明した。
- `Pass(effect, k)`と、ハンドラからの`yield effect`を分けた。前者のあとへ戻って値を加工する説明はしない。
- `handler(h)(program)`の入れ子と、先頭が最外側の`with_handlers`をコードで示した。
- `@do`の補助関数`read_delayed_price`をハンドラから`yield`する例を加えた。非同期関数へ手順をまとめて`Await`で包むコードは使っていない。
- 状態、仮想時間、スケジューラを明示的に取り付けた。`Delay`と`GetTime`の提供元は`doeff_time`。スケジューラだけで仮想時間の操作を扱えるとは説明していない。
- 順序を入れ替えても結果が同じになる、状態やキャンセルの契約まで自動で同一になる、といった主張を避けた。
- 画像内に使う短いコードと、依頼・結果の矢印を`handlers-visuals.json`に定義した。タイトル・キャプションは本文と一致する。

## 実装・既存テストとの対応

| 説明 | 照合した実装・検証 |
| --- | --- |
| `handler`は計算を包む | `doeff/program.py`の`handler`。内部で`WithHandlerType(raw_handler, body)`を作る |
| `with_handlers`は先頭が最外側 | `doeff/program.py`の`with_handlers`と`tests/test_with_handlers_helper.py` |
| ハンドラ内の操作は外側へ進む | `tests/test_handler_chain_traversal.py`。3層・29層、スケジューラ付き・なし |
| ハンドラ内の`@do`補助関数からも依頼できる | `tests/test_handler_nested_do.py`。1段・2段の補助関数 |
| `Pass`後のハンドラの処理は再開しない | `tests/core/test_pass_primitive.py::test_pass_is_terminal_passthrough` |
| 状態の提供と時間の提供は別のハンドラ | `packages/doeff-core-effects/doeff_core_effects/handlers.py`の`state`、`doeff_time`の公開API、実行例 |

## 実行結果

`uv run --no-sync python publications/zenn-use-cases-v0/examples/handler_composition.py`:

```text
順序: JPY 88 / JPY 90、範囲: (100, 80, 100)、状態と時間: (80, 2.0)
```

記事のPythonコード7ブロックも抽出して実行した。冒頭の抜粋は、本文で操作・ハンドラを定義したあとに実行し、全assertが通ることを確認した。画像仕様のコード2本も同じ定義下で実行した。

以下の既存テストは12件通過。

```sh
uv run --no-sync pytest -q \
  tests/test_handler_chain_traversal.py \
  tests/test_handler_nested_do.py \
  tests/core/test_pass_primitive.py \
  tests/test_with_handlers_helper.py
```

対象を絞った実行のため、ADRファイルが収集対象外という既存のpytest警告が1件出た。フルスイート通過の主張ではない。

実行例にはRuffのimport整列とformatを適用。最終的なコードに対するRuff checkは通過。

## 残る統合作業

画像本体の生成・目視確認、メイン記事からのリンク、共通キャプションとmanifestへの登録は統合担当が行う。この記録だけを根拠に、画像の完成は主張しない。

## 追加要件：コードの各行の目的・期待動作

2026-09-16の追加指示に対応し、本文のPython 7ブロック、対応する実行例、画像仕様のコード2本の各実質行へ、日本語コメントを付けた。importや`@do`にも、何を扱うために必要なのかを書いた。依頼の発行、結果の受け取り、継続の再開、入れ子の構築、期待値の確認を具体的に説明している。複数行の引数と閉括弧も対象に含めた。

Pythonのtokenizeで実質行とコメント行を照合し、コメントのない実質行がないことを確認。コメント追加後の本文7ブロック・画像用2本・実行例を再実行し、すべてのassertが通った。Ruff checkも通過している。画像の仕様はコメント付きコードへ更新済みであり、画像本体への反映は統合担当が行う。
