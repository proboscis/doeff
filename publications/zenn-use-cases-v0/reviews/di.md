# DI記事の再精査

- 担当: `review_di`（記事専任エージェント）
- 日時: 2026-09-16
- 確認対象: `doeff-di.md`、`examples/dependencies.py`
- 確認版: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 適用した指針: `doeff-patterns`、`doeff-runtime`、記事別共通レビュー基準

## 指摘と修正

1. 元の整形実装は普通の関数で、利用側も `formatter(text)` と直接呼んでいた。`FormatTitleFn` というProtocolを依存キーにし、両実装を `@do`、利用側を `yield formatter(text)` へ変更した。取得する段階と子Programを実行する段階を区別した。
2. 遅延解決を観測するための `built.append(prefix)` を、`yield Tell(prefix)` に変更した。`writer`・`state`・`writer_log`を実際に合成して、取得4回に対して作成2回という動作を検証した。
3. `reader`は辞書の値をそのまま渡すこと、`lazy_ask`が `Ask` と `Local`を処理すること、Programの遅延評価にschedulerが必要なことを明記した。`Local`内の変更は依存する結果だけ別に作ることを確認した。
4. 環境変数の例を `env_var_ask(...)(lazy_ask(env={})(...))` に変更した。内部の未登録Askが外側へ渡ることまで確認した。架空の変数を `patch.dict` で設定し、終了時に元へ戻す。利用側Programは環境変数を直接読まない。
5. `doeff-pinjected`は現行workspaceから削除済み。削除契約テストを根拠に短い注記を置き、存在しない連携APIは紹介しない。
6. 依存の取得と、通信操作をエフェクトで切ることを区別した。HTTPの具体例は、HTTPハンドラとMemoハンドラを合成するreplay記事へリンクした。
7. 全コード行に目的・期待する値や動作の日本語コメントを追加した。画像の正本仕様を `di-visuals.json` に保存し、本文altとキャプションを一致させた。

## 実装根拠

| 確認内容 | 根拠 |
|---|---|
| Askは任意のキーを保持し、LocalはenvとProgramを受け取る | `packages/doeff-core-effects/doeff_core_effects/effects.py` の `Ask` / `Local` |
| readerは辞書の値をそのまま返す | 同 `handlers.py` の `reader` |
| 遅延評価・依存追跡・Localごとのキャッシュ・外側への委譲 | 同 `handlers.py` の `lazy_ask` |
| セマフォで遅延解決を調整する | `lazy_ask` の `CreateSemaphore` / `AcquireSemaphore` / `ReleaseSemaphore` |
| writerは状態へログを保存し、writer_logはコピーを返す | 同 `handlers.py` の `_writer_handler` / `writer_log` |
| 環境変数の通常値は文字列、未登録はPass | 同 `handlers.py` の `env_var_ask` |
| ハッシュ可能な非文字列キーを使える | `tests/misc/test_reader_hashable_keys.py` |
| pinjected連携が削除された状態を契約とする | `tests/core/test_pinjected_removal.py` |

## 検証

- `.venv/bin/python publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。整形2種、通常/Local/復帰の結果、作成ログ2件、環境変数への委譲をassertで確認。
- 本文のPythonコードを正規表現 `r'```python\n(.*?)```'` で抽出し、同じ名前空間に順番に `exec(compile(...))`: **3ブロック成功**。第3ブロックは本文に明記したとおり第2ブロックの `read_greeting`を利用する。
- `.venv/bin/ruff check publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。
- `.venv/bin/python -m pytest tests/effects/test_lazy_ask.py tests/effects/test_env_var_ask.py tests/misc/test_reader_hashable_keys.py tests/core/test_pinjected_removal.py -q`: **33 passed**。既存のADR未収集警告1件。今回は指定したDI関連テストの検証であり、ADR全件の検証とはしていない。
- `tokenize`でコメント位置と実質行を照合: 専用例63行、本文20行・31行・6行にコメントあり。空行、閉じ括弧のみの行、冒頭docstringは除外。画像用抜粋8行も各行にコメントあり。

## 検証範囲と残り

実HTTP・LLM・agent・外部の環境設定・pinjected連携は実行していない。使用した環境変数は記事専用の架空値のみ。製品runtime・共通資料・他記事は変更していない。画像の生成とmanifest/captionsの更新はroot担当へ引き渡す。
