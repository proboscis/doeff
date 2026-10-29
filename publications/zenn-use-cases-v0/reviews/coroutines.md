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

## 連載の9本目への仕上げ（2026-10-17）

- 担当: 記事の取り組みの依頼を受けた会話（Claude Opus 5.5）。連載の並べ方（案M）の9本目「async/awaitがあるのに、なぜyieldか」。
- 対象: `doeff-coroutines.md`、`examples/coroutine_comparison.py`、`reviews/coroutines-check.py`、図2枚。

### 直したこと

1. 悩み1つ（awaitの先は呼んだ実装で決まり、替えるにはpatchか引数が要る）→ 短いコード → 全体の図 → 本文 → まとめ → 参考の順に組み直した。
2. コードの枠を8つから5つにした（Python 4つ・bash 1つ）。比べる2つの形を1枠目に並べ、差し替えと子Programと再利用を2枠目にまとめた。
3. 自作のハンドラ`hanako`・`taro`を`@handler`・`@do`付きにし、`with_handlers`で付ける形へ揃えた。依頼の判別・`Resume`・`Pass`を記事の中で説明した。
4. 待ちを数える所を、可変のlistへ足していく自作のハンドラから、標準の`Listen`と`listen_handler`へ替えた。
5. 9月16日の本文の「doeff側のキャンセルが橋渡し先へ伝播しない」は、今のmainでは誤り。取り消しは`CancelledError`として届き、`finally`が走る（#498で直った）。取り消しは頼むだけで片付けを待たないこと、共有のループを同期の呼び出しで止めると全ての`Await`が止まることへ書き直した。
6. 実装へのリンクを、リポジトリに無いcommit d4705914から、mainのcommit 6ab1e37d6へ付け替えた。
7. coroutineオブジェクトは2回待てない（`RuntimeError`）こと、丸ごとの`async def`を包むと届く`Await`が1回になることを本文に足し、完全な例で確かめる形にした。
8. 図2枚を手で書いたSVGから作り直した（作り方は`imagegen/coroutines-svg-source.txt`）。

### 一次根拠（今のmain 6ab1e37d6で読んだ所）

| 記事の説明 | 確認した実装・一次資料 |
| --- | --- |
| `@do`の関数を呼ぶとCallの節を作り、動かすたびにVMが関数を呼んでgeneratorを作る | `doeff/do.py`の`do`と`_call`の説明（「the VM calls fn and runs the generator it returns」） |
| `with_handlers`の列は先頭が外側 | `doeff/program.py`の`with_handlers`の説明（「the first handler is outermost」） |
| `Listen`は包んだ範囲で出た決めた型の依頼を集める。見張りは記録して`Pass`する | `doeff_core_effects/effects.py`の`Listen`、`handlers.py`の`_listen_handler`の`observer_handler` |
| `await_handler()`は共有の裏のループで動かす。取り消しは`CancelledError`として届くが、終わりを待たない。同期の呼び出しでループを止めると全ての`Await`が止まる | `handlers.py`の`await_handler`の説明（#498）と`tests/test_await_cancel_propagation.py`（11件が通った） |
| 別のループに結び付いたFutureは待てない | `reviews/coroutines-check.py`で走らせ、「attached to a different loop」の`RuntimeError`を確かめた |
| coroutineは1回しか待てない | 完全な例で走らせ、「cannot reuse already awaited coroutine」の`RuntimeError`を確かめた |
| coroutineのsend・generatorの歴史・イベントループの役目 | Python公式の文書とPEP 492（9月16日の確認を引き継ぐ） |

### 実行した確かめ

```bash
uv run --no-sync python publications/zenn-use-cases-v0/reviews/coroutines-check.py  # 掲載の4枠を順に実行し、完全な例と比べ、決まりの数を測る。
uv run --no-sync python publications/zenn-use-cases-v0/examples/coroutine_comparison.py  # 完全な例を走らせる。
uv run --no-sync python publications/zenn-use-cases-v0/verify_comments.py  # 実質行の日本語コメントを確かめる。
uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/coroutine_comparison.py publications/zenn-use-cases-v0/reviews/coroutines-check.py  # 静的検査。
uv run --no-sync pytest tests/test_await_cancel_propagation.py -q  # 取り消しの説明の根拠のテスト。
```

- 2026-10-17（日本時間）、main（commit 6ab1e37d6）の上で、5つとも終了コード0。Python 3.14。
- `coroutines-check.py`の出力: Python 4枠を実行・完全な例と一致・別ループのFuture、本文の字数・枠5・図2・60字を越える文0・です・ます以外で終わる文0。
- 完全な例の出力: 挨拶 花子 / 太郎・子の奥にも届く / coroutineの2回目 cannot reuse already awaited coroutine / 本文 はじめに、doeff / Await 1断片ずつ5回・丸ごと1回。

### 未実行と制限

本物の非同期SDK、通信、LLMは呼んでいない。trioなどasyncio以外の基盤では動かしていない。
- Zennの形への書き出し（公開順の表の9本目はまだ下書きなので、表のslug `doeff-yield-vs-async-await`・公開予定として仮に書き出した）を、`zenn-check/check.mjs --online`（zenn-cli 0.5.6）で読み、崩れ0個。検査の誤り0・画像2枚の欠け0・リンク6個の切れ0・Markdownの崩れ0・秘密の形0。
- GitHubからの新しいclone（commit a8d779162）で`make sync`（約3分）と完全な例の実行を記事の手順どおりに走らせ、終了コード0。

## 2026-10-29 英語の2本目「Yield and async」の版への書き直し

`doeff-coroutines.md`を、英語の2本目「Yield and async: who answers when your Python code pauses」(`docs/articles/yield-and-async.md`)の版に書き直した。上の節までの指摘は、前の本文(花子と太郎・`collect_chunks`)への物である。

| 見る所 | 英語の版との関係 |
| --- | --- |
| 軸 | 英語の版と同じ代数的エフェクト。`await`の要求の行き先(いちばん上のTaskだけ)と、`yield`の行き先(呼ぶ側が付けたいちばん内側のハンドラ)と、継続`k`を持つハンドラが決める再開の仕方を比べる |
| 例 | Python・text・bashの枠24個は、コメントを除くと英語の版の枠と1つずつ同じ。コメントは各行の日本語にした(この連載の決まり)。例の完全なファイルは英語の版と同じ`examples/yield_and_async/`(greet.py・drive.py・stream.py)で、`examples/coroutine_comparison.py`は外した |
| 図 | 英語の版の2つのsequenceDiagramを、日本語のSVGで描いた(`coroutines-concept`・`coroutines-flow`) |
| 英語の版に無い物 | 冒頭の1本目とGitHubへの案内、`試す`の節の前後の回への案内、参考の節(Python公式の文書・PEP 492・`await_handler`の実装・前後の回) |
| 前の本文から外した主張 | 別のイベントループに結び付いたFutureは待てない事(英語の版に無いため、確かめからも外した) |
| 題 | 前の題「async/awaitがあるのに、なぜyieldで書くのか」を残した。英語の版の冒頭の問い(なぜ`@do`と`yield`で書くのか)と同じで、1本目の目次と本の書き換えの表がこの題を持つため |

### 確かめ(`reviews/coroutines-check.py`を書き直した)

- 枠が英語の版と同じ事: 数・言葉・コメントを除いた中身。
- 抜粋の枠が、コメントを除くと例のファイルの中に塊ごとにその順で在る事。実行の行の枠が、ちょうど1つの例のファイルの`__main__`の行である事。
- 枠を上から順に、例のファイルごとの名前空間で走らせ、実行の行の印字(例外は「型: 文」)が直後のtextの枠と同じ事。
- 試し方の3つのファイルを同じPythonで`-W error`で走らせ、印字の区切り9個がどれも本文のtextの枠に在る事。
- 本文の決まり: `published: false`、AIが書いたと名乗る文が無い、IOの差し替えだけに読める語(swap・fake・mock・real I/O・差し替え・モック・偽・本物のI/O)が見出しにI/Oを含む節の外に無い、60字を越える文0・です・ます以外で終わる文0、図2〜4枚、社外に出せない名前が無い。
- わざと壊した5か所(textの枠の印字・抜粋のコード・I/Oの節の外のmock・AIが書いたと名乗る行・60字を越える文)は、どれも赤になった。

### 実行した確かめ(2026-10-29)

```bash
uv run --no-project python publications/zenn-use-cases-v0/verify_pypi.py --doeff 0.8.0 --python 3.12 coroutines  # PyPIのdoeffだけの新しい環境で確かめる。3.11と3.14でも走らせた。
uv run --no-sync python publications/zenn-use-cases-v0/reviews/coroutines-check.py  # リポジトリのdoeffで確かめる。
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn.py <出力先>  # 記事をZennの形へ書き出す。
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn_book.py <出力先>  # 本を書き出す(第7章・60字を越える文0個)。
node publications/zenn-use-cases-v0/zenn-check/check.mjs <出力先> --online  # zenn-cliのプレビューで崩れを数える。
```

- `verify_pypi.py`: PyPIのdoeff 0.8.0・doeff-vm 0.8.0・doeff-core-effects 0.8.0で、Python 3.11・3.12・3.14とも OK。
- `check.mjs`: 記事16本・本1冊(章10)で、崩れは`examples/yield_and_async`へのGitHubのリンク1個(記事と本の章で2個)だけ。英語の版を置いたbranchがmainに入るまで、mainのその場所は404になる。英語の版も同じリンクを持つ。
- `verify_publication`: mainで先に切れた`package-coverage.md`の`packages/`の中へのリンク10個を外すと通った(この変更とは別)。`verify_comments`の全体は`doeff-durable.md`で止まる(この変更の前から)。`doeff-coroutines.md`の実質109行には、どれも日本語コメントがある。

### 未実行と制限

本物の非同期SDK、通信、LLMは呼んでいない。Zennの`zenn-content`へは書き出していない(公開の許可の後の仕事)。
1本目への案内の文(時間・並行・リトライ・予算・I/Oのハンドラを1つのプログラムの周りに重ねる)は、1本目の英語の訳(branch `wt/zenn-article-1-ja-on-en`)の本文を指す。このbranchの`doeff-main.md`は、まだ前の本文である。
