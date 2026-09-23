# observe記事の再精査

- 担当: `review_observe`
- 日付: 2026-09-16
- 対象: `doeff-observe.md`、`examples/observability.py`
- 適用: `doeff-patterns`、`doeff-runtime`

## 指摘と修正

1. TellのWriter蓄積、Slogの標準エラーへの表示、Listenの依頼収集を区別した。`slog_handler`は現在、表示専用でState不要。`writer`には外側のStateが必要。
2. Listenの戻り値を「ログ文字列のリスト」と誤解させないよう、`(result, captured)`の型と、各依頼の`.msg`・`.kwargs`をコードで検査した。SlogEffectとWriterTellEffectは別型。SlogはSlogEffectの別名であることもimportで確認した。
3. 2件という件数だけを検査していた例を拡充した。順序・属性・本体の戻り値・対象範囲・Writerへの転送・標準エラーへの表示を個別に検査する。
4. `Listen(convert())`の前後にTellを置き、収集はconvert内の2件、Writerは前後を含む3メッセージになることを確認した。
5. Slogの表示ハンドラを`slog_discard_handler`へ交換し、表示は消える一方で収集結果が残ることを示した。下位クライアントを注入する仕掛けは使わない。
6. types省略時のListenはWriterTellEffectだけを収集することをコードで示した。Listenは依頼を処理し終えるハンドラではなく、収集してPassするため、処理ハンドラ不在ではUnhandledEffectになることも専用例で確認した。
7. `doeff-flow`のimport失敗を現在の実装で再現した。TracePush等のソース上の使用形は、実行できる例として扱わないよう明示した。明示的なトレースハンドラは終了状態を自動記録しないため、`watch --exit-on-complete`の期待を生じる例も除去した。
8. trace_observerのコールバック生成だけではVMを自動観測できないことを維持した。並行タスクや内側で消費済みの依頼まで全て観測できるとは主張しない。
9. 合成する処理は全て@doで定義し、子Programをyieldする。純粋な表示変換のprogress例も@doとして使用形を記載した。
10. 全Pythonコード・シェルコード・図用コードへ、行ごとの目的と期待値・動作の日本語コメントを付けた。importや検証assertも対象とした。

## 実装根拠

- `packages/doeff-core-effects/doeff_core_effects/effects.py`: Listen、WriterTellEffect、SlogEffect、Slog別名、slog。
- `packages/doeff-core-effects/doeff_core_effects/handlers.py`: `_listen_handler`は収集後にPass、`_slog_handler`は標準エラー表示、`_slog_discard_handler`は明示的な無表示、`_writer_handler`と`writer_log`はStateによる蓄積と一覧のコピー。
- `tests/test_slog_semantics.py`: 型の分離、Listen既定、表示と収集の共存、未処理時の失敗。
- `packages/doeff-flow/src/doeff_flow/trace.py`: LiveTraceと、存在しない`doeff_vm.RunResult`へのimport。
- `packages/doeff-flow/src/doeff_flow/effects/trace.py`: TracePush／TraceAnnotate／TraceSnapshot／TraceCaptureの引数。
- `packages/doeff-flow/src/doeff_flow/handlers/production.py`: factoryの引数・Programへの適用、記録される状態はrunning、captureのjsonl文字列化。
- `packages/doeff-flow/src/doeff_flow/cli.py`: ps/watchの引数とtrace-dir指定。

## 検証

- `.venv/bin/python publications/zenn-use-cases-v0/examples/observability.py`: 成功。表示／蓄積／収集／属性／順序／scope／ハンドラ交換／types既定／進捗判定／未処理時の失敗を確認。
- `.venv/bin/ruff check publications/zenn-use-cases-v0/examples/observability.py`: 成功。
- `.venv/bin/python -m pytest tests/test_slog_semantics.py tests/test_core_effects.py -q`: 28件成功。既存ADRの収集対象外についてPytestWarningが1件ある。
- 記事内のPython6ブロックをast.parse。コアの先頭4ブロックを同一名前空間で順に実行し成功。後半2ブロックはdoeff-flowのimportが不能のため構文と実装照合のみ。
- `.venv/bin/python -c 'import doeff_flow'`: 期待した失敗を再現。`ImportError: cannot import name 'RunResult' from 'doeff_vm'`。VM/packageを修正したり偽のRunResultを注入したりしていない。
- tokenizeによって、記事と専用例の実質行に日本語コメントがあることを検査。空行・docstring・閉括弧だけの行は除く。
- 図2点のタイトル・captionが本文に一致すること、図用コード各行にコメントがあることを確認。

## 未実行の範囲

実LLM・agent・外部API・ネットワーク・監視CLIは実行していない。`doeff-flow`の記録・データ型・CLIは互換性の問題を明記した使用形であり、動作確認済みではない。コアの例はファイルや外部サービスを必要としない。

## 画像への引き継ぎ

`observe-visuals.json`を正本とし、2画像のalt・captionを更新済み。白背景の平面的な図とし、Listenで収集した後に元の依頼を外側へ渡すことを示す。図は親担当のimagegen生成待ち。
