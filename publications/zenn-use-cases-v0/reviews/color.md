# color 記事別レビュー

担当: review_color（記事単位の独立担当）

対象: `doeff-color.md`、新設 `examples/color_comparison.py`。他記事・共有コード・管理ファイル・製品コードは編集していません。

## 指摘と修正

- 元の例は`@do`による定義だけで、時計の差し替えが動く証拠を別記事に委ねていました。同じ`Program`を同期実時間・非同期実時間・仮想時間で実行し、全て`"処理結果: 完了"`になる例を追加しました。
- `Ask`で取得する設定と、`yield worker(seconds)`による子Program合成を早い位置に置き、何の書き方が共通になるかをコードで示しました。
- 同期・非同期の呼び出し規約の差と、asyncio固有の待機対象への依存を分けました。`await`がasyncio専用だとは説明していません。
- `run`自体がawaitableになるという誤読を防ぎ、現在の`run`が完了まで呼び出し元を待たせることを明記しました。同期`Delay`は実行スレッドを止めるため、並行実行時の性質まで等しくはなりません。
- ハンドラは普通のPythonとして直接実行された通信や`time.sleep`を自動で捕捉しないこと、差し替えには効果の契約を満たす実装が必要なことを明記しました。
- 現行の`async def`例は比較教材としてのみ残し、doeff側の処理合成は全て`@do`と`yield helper(...)`にしました。`Await`は時間ハンドラが発行する構成です。
- 旧`asyncio.coroutine`例は歴史資料と明記し、現行Pythonで実行する例から除外しました。PEP 3156とPEP 492の一次資料を閲覧して確認しました。
- 全4コードブロックと専用例の全実質行に目的・期待値の日本語コメントを付けました。画像用抜粋も各行コメント付きです。
- 新しいcoroutine・VM・ハンドラ合成の記事へのリンクを追加しました。

## 実装根拠

- `doeff/do.py:260`: 関数呼び出しを`Expand(Apply(Pure(VMCallable(thunk)), []))`へ包み、実行前のProgramを返します。
- `doeff/run.py:10`: `PyVM.run`を呼び、その完了結果を返します。
- `packages/doeff-time/src/doeff_time/effects/time.py`: `Delay`は`DelayEffect`を返します。
- `packages/doeff-time/src/doeff_time/handlers/sync_time.py`: Delayで`time.sleep`し、`Transfer(k, None)`で再開します。
- `packages/doeff-time/src/doeff_time/handlers/async_time.py`: Delayを`Await(asyncio.sleep(...))`へ変換し、`None`で再開します。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py`: 時計を持ち、予定とPromiseを使って指定時刻へ進め、`None`で再開します。
- `packages/doeff-core-effects/doeff_core_effects/handlers.py:427`: Awaitを共有のバックグラウンドasyncioループとスケジューラへ橋渡しします。
- 同ファイル`:505`: `lazy_ask`は渡されたenvからAskを解決します。
- `packages/doeff-vm/src/pyvm.rs`: 消費済みの継続を再び取り出す操作をone-shot violationとして拒否します。
- [PEP 3156](https://peps.python.org/pep-3156/)、[PEP 492](https://peps.python.org/pep-0492/): yield fromによる初期設計、Python 3.5でのnative coroutine構文の導入を確認しました。

## 検証

- `.venv/bin/python publications/zenn-use-cases-v0/examples/color_comparison.py` — 成功。同期・非同期・仮想時間が同じ結果を返すこと、および10秒の仮想待機で`GetTime`の差が正確に`timedelta(seconds=10)`になることを検証。
- `.venv/bin/ruff check publications/zenn-use-cases-v0/examples/color_comparison.py` — 成功。
- Markdown内のPythonを抽出し、共有namespaceでブロック1〜3を順次実行 — 成功。旧`asyncio.coroutine`のブロック4は歴史例として構文検証のみ。
- `tokenize`で、本文4ブロックと専用例の各実質行にコメントが存在することを検査 — 成功。文字列のみのモジュール説明と閉括弧だけの行は除外。内容も目視で目的・期待結果を確認。
- 外部API・実エージェント・有償呼び出し・外部送信は実行していません。実際の同期とasyncioのタイマーは各10ミリ秒のオフライン待機として実行しました。

## 親担当へ

- `verify_examples.py`のOFFLINEへ新設`color_comparison`を追加してください。共有検証スクリプトは担当範囲外なので未編集です。
- `reviews/color-visuals.json`を画像とcaptionの正本としてください。記事のalt/captionは同JSONへ揃えました。画像生成自体は親担当です。
