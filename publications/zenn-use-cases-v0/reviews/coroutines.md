# coroutine比較記事の実装照合

対象: `doeff-coroutines.md`、`examples/coroutine_comparison.py`

確認日: 2026-09-16

担当: 記事別レビュー担当 `review_coroutines`

## 作成した内容

- 同じ「名前を取得して挨拶を返す」を、比較用の`async def`とdoeffの`@do`で示した。
- coroutineオブジェクト、`asyncio.Task`、awaitable、Program、ハンドラ境界の役割を分けた。`await`自体がasyncio専用、必ず停止する、asyncioが時間だけを扱う、という説明はしない。
- `ReadName`はこの例で明示的に定義した依頼型。架空の組み込みAPIとして紹介していない。
- `hanako`/`taro`の差し替えは同じProgramで実行し、継続を複製・再利用しているのではないと明記した。
- 子の挨拶も`yield greet()`で合成した。収集関数は`@do collect_chunks`とし、`Await`は非同期イテレータの次の1要素を読む`anext(iterator, None)`にだけ使用した。
- テスト用の非同期イテレータは、SDK側の反復プロトコルを再現する目的を明記した。アプリケーションの収集ループを`async def`へ逃がしていない。
- 内側の待機依頼を観測するハンドラで、4断片と終了確認の合計5回の`Await`を確認した。本文だけが合うという検証にとどめていない。
- `await_handler`の共有asyncioループ、実行環境の互換性、キャンセル伝播の現行制約を実装に合わせて記した。
- 単純な名前供給はDIでも可能とし、Programの深い位置から届く操作を範囲ごとに解釈・観測できる点を、実例の確認範囲に即して説明した。
- メインへの戻りリンク、VM・ハンドラ合成・関数の色・時間・LLMへの関連リンクを置いた。

## 一次根拠

| 記事の説明 | 確認した実装・一次資料 |
| --- | --- |
| `@do`を呼ぶと遅延したProgramを作る | `doeff/do.py:259`の`do`。`wrapper`が`Expand(Apply(...))`を構築し、thunk内でgeneratorを作る |
| handlerが適用範囲を作る | `doeff/program.py:28`の`handler`。`WithHandlerType(raw_handler, body)`を返す |
| generatorからの依頼と再開 | `packages/doeff-vm/src/python_generator_stream.rs:259`の`send_to_generator`、同ファイル686行付近のEffectBaseからPerformへの分類 |
| ハンドラ境界までの継続 | `packages/doeff-vm-core/src/vm/dispatch.rs:57`の`perform_effect`、同ファイル122行付近の`reattach_chain` |
| Awaitは非同期実装への橋渡し | `packages/doeff-core-effects/doeff_core_effects/effects.py:82`の`Await`と、`handlers.py:427`の`await_handler`。`CreateExternalPromise`、`Wait`、`Transfer`を使う |
| 標準Awaitハンドラの制約 | `handlers.py:427`以降のdocstring。共有バックグラウンドasyncioループ、doeff側キャンセルが橋渡し先へ伝播しない既知の制約 |
| coroutineとTaskの区別、sleep(0) | [Python公式 Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)を2026-09-16に確認 |
| coroutineのsend/throw、awaitable | [Python公式 Coroutine objects](https://docs.python.org/3/reference/datamodel.html#coroutine-objects)を同日に確認 |
| I/O、単調時計、独自のイベントループ | [Python公式 Event loop](https://docs.python.org/3/library/asyncio-eventloop.html)を同日に確認 |
| generatorベースとnative coroutineの歴史 | [PEP 492](https://peps.python.org/pep-0492/)を同日に確認 |

適用スキル: `doeff-patterns`、`doeff-runtime`、`doeff-capability-review`。スキル内の例だけをAPIの根拠にせず、上記の現行実装と実行結果に照合した。

## 実行結果

```sh
uv run --no-sync python publications/zenn-use-cases-v0/examples/coroutine_comparison.py  # 比較・差し替え・合成・収集の検証を行う。
uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/coroutine_comparison.py  # 専用の実行例の静的検査を行う。
```

実行結果:

```text
挨拶: 花子 / 太郎、合成: 2回、本文: はじめに、doeff、Await: 5回
All checks passed!
```

記事のPython 7ブロックも、Markdownから抽出して掲載順に同じ名前空間で実行した。全assertが通過した。紹介した`ReadName`とハンドラは記事内で定義しており、実行例からの暗黙のimportで補っていない。画像用コード2本も実行した。概念図は本文のReadNameとhanakoの定義を使い、処理図は明示しているcollect_chunksのwhile True内へ抜粋を戻して、同じストリームが全文になることを確認した。

すべてオフラインの確認。実LLM、エージェント、通信、Docker、SSH、外部APIを実行していない。フルスイートの通過は主張しない。

## 各行コメント

本文のPython 7ブロックとshell 1行、専用例、画像仕様のコードの全実質行へ、日本語の目的・期待値・期待動作を付けた。API名の読み替えだけでなく、名前の供給、元の継続の保持、空文字列と終了の違い、4断片と終了確認で5回になる理由を説明した。

Pythonのtokenizeで、専用例1本・本文7ブロック・画像用コード2本の実質行とコメント行を照合した。モジュール説明のdocstringを除き、コメントのない実質行はなかった。

## 残る統合作業

画像仕様を`coroutines-visuals.json`へ記録した。本文の画像タイトル・キャプションはJSONと一致している。imagegenによる2枚の画像本体の生成、メインからの導線と共通manifestへの登録は親担当が行う。本レビューだけを根拠に画像の完成は主張しない。
