# boundaries 記事別レビュー

担当: `review_boundaries`。確認日: 2026-09-16。

対象: `doeff-boundaries.md`、専用の`examples/document_pipeline.py`。他記事・共有例・共通管理ファイル・製品runtimeは変更していません。

## 指摘と修正

- 元の文書例は、`async_pages(fetch)`へ非同期関数を注入する形でした。取得方法をエフェクトとハンドラで差し替える主題にそろえ、`ReadPage`を`HttpRequest`へ翻訳する`pages_over_http`へ置き換えました。
- 利用者が`client_factory`を書かなくても、固定応答・拒否・本番HTTPのハンドラを交換できる構成にしました。固定応答は公開型`HttpResponse`を返し、本文の翻訳は`raise_for_status()`後に行います。
- ドメイン側の契約を「指定した版の本文文字列を返す」と明示しました。`make_memo_rewriter(ReadPage, key_fn=...)`で保存の単位を決め、メモリ/SQLiteの担当とHTTP担当を分けました。同じ版は同じ本文という前提、版変更時の保存ミスも説明しています。
- `read_title`・`make_index`・`workflow`の処理合成は`@do`と`yield helper(...)`です。一連の処理を`async def`へ逃がす例は削除しました。非同期SDKを自作する例も不要になり、`Await`は保存・HTTP・実時計の各ハンドラ側の操作だけです。
- `assemble`やHTTPハンドラのファクトリは、Programを解決する業務処理ではなく、Programを包む構成関数です。ここで`run()`は呼びません。`run()`は本文と専用例の検証境界にのみあります。
- 現行の`Gather`は、ProgramのlistではなくTask/Futureの可変長引数を受け取ります。先に`Spawn(read_title(...))`し、得たTaskを`Gather(*tasks)`へ渡す形を、実行で確認しました。スキル内の古い`Gather(programs)`例をそのまま採用していません。
- 優先度と完了待ちは、`Spawn(..., priority=PRIORITY_HIGH)`と`Wait(task)`のコードを追加しました。高優先度タスクの正しい結果を検証し、プリエンプションや実時間の締め切り保証は主張していません。
- 状態へ翻訳する`DealDamage`も全行コメントへ改稿しました。HP 10から7、HP 2から0を本文コードで検証しました。
- asyncioの説明は一次資料を確認し、非同期I/O・タスク・同期・イベントループも扱うことを明記しました。新しいcoroutine・VM・ハンドラ合成の記事への導線も追加しました。
- 画像2枚の仕様を`boundaries-visuals.json`へ保存しました。本文のaltとcaptionは同じ仕様へそろえています。

## 実装との対応

| 説明 | 確認した実装 |
| --- | --- |
| handlerがProgramを包む | `doeff/program.py`の`handler` |
| 子Programをyieldで合成する | `doeff/do.py`と`tests/test_handler_nested_do.py` |
| HTTPの依頼・公開の応答型・エラー判定 | `packages/doeff-core-effects/doeff_core_effects/http_effects.hy`、同`.pyi` |
| 本番HTTP担当のクライアント管理・Await境界 | `packages/doeff-core-effects/doeff_core_effects/_http_handlers_impl.hy` |
| 保存判定→取得→保存の順序 | `packages/doeff-core-effects/doeff_core_effects/memo_handlers.py`の`make_memo_rewriter` |
| メモリ/SQLite保存のハンドラ | 同ファイルの`in_memory_memo_handler`、`sqlite_memo_handler`と`_memo_handlers_impl.hy` |
| Spawnの引数・優先度、GatherのTask/Future契約 | `packages/doeff-core-effects/doeff_core_effects/scheduler.py`の`Spawn`、`Gather`、`waitable_key`、`handle_scheduler_effect` |
| 時計を独立に選ぶ | `packages/doeff-time/src/doeff_time/handlers/sim_time.py`、`async_time.py` |
| 一度だけ再開する継続 | `packages/doeff-vm/src/pyvm.rs`の消費済みcontinuation拒否 |
| asyncioの対象範囲 | [asyncio公式ドキュメント](https://docs.python.org/3/library/asyncio.html)を閲覧 |

## 検証

### 専用例

`.venv/bin/python publications/zenn-use-cases-v0/examples/document_pipeline.py` — 成功。

- 索引が`("はじめに", "遊び方")`、`("遊び方", "おわりに")`になる。
- 各取得が仮想2秒でも、最初の2ページは並行取得なので全体は仮想4秒。
- 延べ4ページの依頼に対して、HTTP担当へ届くのは3件。同じ版の`rules`は再利用される。
- SQLiteへ保存後、新しいProgram・新しいSQLiteハンドラ・HTTP拒否ハンドラでも同じ結果になる。取得待機がないため仮想経過時間は0秒。
- `edition-2`へ変えた未保存の依頼は、正しいURLを含む`LookupError`になる。この期待したエラーのdoeff tracebackがstderrに表示されるが、`pytest.raises`で検証済みであり、例ファイル自体は終了コード0。
- 同じ文書処理を仮想時計とasyncio実時計で実行し、同じ索引と取得回数になる。待機10ミリ秒を2段階で行い、各時計の経過時間が合計20ミリ秒以上になる。
- `PRIORITY_HIGH`付きTaskを`Wait`して、`"遊び方"`を受け取る。

### 本文・コメント・既存テスト

- 本文のPython 11ブロックを抽出し、共有namespaceで順に実行。冒頭の抜粋は後続の定義後に評価。すべてのassertが成功。本番HTTPの構成関数は定義のみで呼び出していません。
- 本文の`urgent_title`も、固定HTTP・メモリMemo・仮想時計を付けて実行し、`"遊び方"`を確認しました。
- 本文11ブロック・専用例・画像用コード2本の計313実質行をtokenizeで照合し、全行にコメントがあることを確認。空行・閉括弧だけの行・モジュール説明の文字列は除外しました。内容も目的・期待値・待機後の動作を確認しました。
- `.venv/bin/ruff check publications/zenn-use-cases-v0/examples/document_pipeline.py` — 成功。
- `.venv/bin/python -m pytest -q packages/doeff-time/tests/test_sim_time.py packages/doeff-time/tests/test_async_handler.py tests/test_handler_nested_do.py` — 24件成功。対象外のADRファイルが収集されない旨の既存警告1件。フルスイートの通過は主張していません。

実HTTP・有料API・実エージェント・外部送信は行っていません。データは例示用URLと一般的な文書名だけで、機密リポジトリの内容は使用していません。

## 親担当への引き継ぎ

- `reviews/boundaries-visuals.json`が新しい画像・captionの正本です。画像生成自体は親担当です。
- 共通のcoverageに旧`async_pages`、非同期fetch注入、メモリ/asyncio取得先という説明があれば、HTTP担当・Memo保存・時計の独立した合成へ更新してください。
- 既存の`document_pipeline`という実行名は維持しています。共通検証スクリプトへの新規登録は不要です。
