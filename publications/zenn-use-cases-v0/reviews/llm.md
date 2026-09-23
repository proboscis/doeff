# LLM記事の精査結果

- 担当: 記事別レビューエージェント `review_llm`
- 日付: 2026-09-16
- 対象: `doeff-llm.md`、`examples/llm_pipeline.py`
- 状態: 本文・コード修正と担当範囲のオフライン検証を完了。画像は親担当が `llm-visuals.json` に従って制作する。
- 適用した指針: `doeff-patterns`。現行ソースを確認し、既存テストが使っている書き方でもユーザーの基準に合わないものは踏襲しない。

## 指摘と修正

1. `collect_openai_stream` が `async def` で収集ループ全体を隠し、その呼び出しを `Await` に渡していた。`@do` に変更し、呼び出しは `yield collect_openai_stream(stream)` とした。SDK境界の `anext(iterator, None)` だけを `Await` する。
2. 大きな関数へLLM依頼を詰め込んでいた。構成・本文・紹介文・埋め込みをそれぞれ `@do` の段階に分け、`write_article` で合成した。検証外の関数に `run()` は置かない。
3. 本文が存在しない応答を次段へ渡せていた。本文の有無と埋め込みの件数を検査し、期待するデータを返せない場合は例外にした。
4. 「プロバイダごとの差」という説明では、未実装の範囲が分からなかった。Geminiの標準ストリーミング依頼が実際には `str` を返すこと、OpenRouterのストリーミングは `NotImplementedError`、埋め込みは `Pass` であることを表に明記した。
5. ハンドラのファクトリの形を実装と照合した。OpenAI/OpenRouterは `handler(production_handlers())`、Geminiは `production_handlers()` を取り付け関数として使う。記事のコードもその形にした。
6. モックの呼び出し回数だけでは不十分だった。構成→本文、本文→紹介文、本文→埋め込みの入力を記録から検査し、さらに実SDKのSSEデコーダ・チャンク型を使う無通信検証を追加した。

## 実装根拠

| 確認内容 | ソース |
|---|---|
| 共通4エフェクトのimport・キーワード引数 | `packages/doeff-llm/src/doeff_llm/effects/{chat,structured,embedding}.py` |
| OpenAIのルーティング・Resume・生のハンドラを返すファクトリ | `packages/doeff-openai/src/doeff_openai/handlers/production.py` |
| OpenAIの非同期ストリームとチャットの戻り値 | `packages/doeff-openai/src/doeff_openai/chat.py::chat_completion` |
| OpenAIの埋め込みの戻り値 | `packages/doeff-openai/src/doeff_openai/embeddings.py::create_embedding` |
| ArticlePlanへの変換 | `packages/doeff-openai/src/doeff_openai/structured_llm.py::process_structured_response` |
| モックの返り値・呼び出し履歴・取り付け関数 | `packages/doeff-openai/src/doeff_openai/handlers/testing.py` |
| Geminiのstr応答・ストリーミング依頼の現状・取り付け関数 | `packages/doeff-gemini/src/doeff_gemini/handlers/production.py::_chat_impl`, `_streaming_chat_impl`, `_embedding_impl`, `production_handlers` |
| OpenRouterの未実装ストリーミングとdict応答 | `packages/doeff-openrouter/src/doeff_openrouter/chat.py::chat_completion` |
| OpenRouterの埋め込みPass・生のハンドラを返すファクトリ | `packages/doeff-openrouter/src/doeff_openrouter/handlers/production.py` |
| Awaitが非同期オブジェクトを待ち、スケジューラへ橋渡しする境界 | `packages/doeff-core-effects/doeff_core_effects/handlers.py::await_handler` |
| 実SDKのSSE解析・チャンク変換・応答の終了処理 | インストール済みOpenAI SDKの `openai._streaming.AsyncStream.__stream__` |

## 検証結果

すべて成功した。外部APIへは接続していない。

- `examples/llm_pipeline.py::verify`: 4つのLLM依頼の戻り値、呼び出し回数、前段から後段への入力を確認。
- `examples/llm_pipeline.py::verify_sdk_stream`: ローカルのSSEデータをSDKの `AsyncStream` で解析。roleのみ、本文、choicesが空、終了の5チャンクから本文を収集。5チャンク＋反復終了確認＋クライアント終了の7回の `Await` を観測し、収集全体が1回の `Await` に戻っていないことを確認。SDKの `APIError` が `Try` の失敗まで伝わることも確認。
- `doeff-llm.md`: Python 6ブロックを掲載順に同一名前空間でコンパイルして実行。テスト用ハンドラを使う `run()` まで実行し、本番ハンドラはProgramへの取り付けだけを確認。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/llm_pipeline.py`: 成功。
- `uv run --no-sync ruff format --check publications/zenn-use-cases-v0/examples/llm_pipeline.py`: 成功。

実行時は `uv run --no-sync python` から子プロセスを起動し、`PYTHONPATH` にリポジトリルート、記事用examples、各workspace packageの `src`（srcがないpackageはそのディレクトリ）を設定した。子プロセスで `publications/zenn-use-cases-v0/examples/llm_pipeline.py` を実行した。記事コードは同じ環境でPythonフェンスを順に抽出し、`exec(compile(block, filename, "exec"), namespace)` で実行した。

## 各行コメント

- 記事6コードブロックの各実質行へ、その行の目的と期待する値・動作を日本語で記載した。
- 対応例ファイルにも、通常の段階、テスト用設定、SDKの異常系の検証までコメントを付けた。formatterが分割した引数は、その式に付けたコメントで説明している。
- `llm-visuals.json` の図内コードも各行コメント付き。概念図と処理図のそれぞれに、コードとノード・矢印の対応を指定した。

## 残る実行範囲と画像

本番モデルの品質・費用、実APIの認証や通信は未実行で、検証成功とは扱わない。SDKクライアントの構築はローカルデータの解析用のみで、要求は送信していない。製品runtime・provider実装の変更、commit、pushは行っていない。

図の正本は `llm-visuals.json`。本文の画像altと直後のキャプションをこのJSONへ合わせた。処理図は従来の4段階全体図から、ユーザーが指摘した `@do` の収集ループを示す図へ変更する。共通キャプション・manifestの更新は親担当へ引き継ぐ。

## 最終検査での各行コメント補修（2026-09-16）

- 担当: `review_llm_comments`。
- `examples/llm_pipeline.py` の分割された呼び出し・引数・条件式・importなど92行に、その行自身の目的と期待する値・動作の日本語コメントを追加した。括弧だけの行にあった「組み立てを閉じる」という説明は削除した。
- 以前の「formatterが分割した引数は、その式に付けたコメントで説明する」という基準を訂正し、すべての実質行に直接コメントを付けた。Pythonのtokenizeでコメントと文字列を区別して検査し、実質201行のコメント未記載が0行であることを確認した。docstringと括弧・区切りだけの行は実質行へ数えない。
- 変更前後のAST（行位置情報を除く）が一致しており、処理内容や`@do`・`Await`の境界は変えていない。`ruff format`は実行していない。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/llm_pipeline.py` は成功。
- workspace packageのパスを設定し、`uv run --no-sync python` から専用例を実行した。4段階の受け渡し、および実SDKでのSSE解析・チャンクごとのAwait・APIErrorの伝播の検証がともに成功した。外部への通信はない。
