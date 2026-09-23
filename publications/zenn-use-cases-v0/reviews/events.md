# events記事の精査記録

- 担当: review_events（記事単位のレビュー担当）
- 日付: 2026-09-16
- 状態: 本文・専用例の修正とオフライン実行確認を完了。画像生成は親担当へ渡した。
- 使用スキル: doeff-patterns、doeff-runtime。

## 確認・編集した対象

- `doeff-events.md`
- `examples/event_loop.py`
- `examples/scheduler_coordination.py`（親担当から専用例として割り当て）

## 指摘と修正

1. 初例の`Spawn(WaitForEvent(Ready))`を、`@do receive_ready()`を`Spawn`する例へ整理した。受信処理のProgramを合成する形を明示し、`async def`に受信処理を移していない。
2. 「Spawnしたから購読は常に完了済み」という読みを避けた。現行スケジューラの標準優先度・同順位FIFO・子タスクを親の再開より先に並べる実装を根拠に、初例で成立する順序と、優先度や購読前の処理を変えた場合の注意を説明した。
3. 反復受信の終了を`title=None`で表す代わりに、独立した`PagesFinished`型を導入した。実在する可変長引数`WaitForEvent(PageReady, PagesFinished)`の受信と`isinstance`による分岐を示した。
4. `Race`が自動で他Taskをキャンセルしないことを実装で確認し、Taskを所有する側の`try/finally`で`Cancel`する構成にした。例は外部資源を持たず、Cancelによる任意の外部資源の解放保証は主張していない。
5. 受信待機→仮想時刻1・2秒のタイトル→3秒の終了、という発行順序を明記した。期限5秒なら2件、0.5秒なら期限切れを確認した。
6. Promiseとイベントの役割を区別した。Promiseは完了を記憶する一方、メモリ上のPublish/WaitForEventは購読前の通知を保存しない。
7. `ExternalPromise`例は外部スレッドとの接続境界であると明示した。`.future`はスケジューラ側で作り、外部スレッドにはthread-safeな`.complete()`だけを渡す。Thread起動をエフェクト化したと誤読させない。
8. 本文のPython 3ブロックと専用例2ファイルについて、全実質行に目的・期待値/動作がわかる日本語コメントを付けた。空行、独立した閉括弧、説明用docstringは対象から除いた。`verify()`だけが実行境界で、合成対象は`@do`のProgramを維持した。

## 実装根拠

- `packages/doeff-events/src/doeff_events/effects/events.py`: `wait_for_event(*event_types)`、型の正規化、`Publish`/`WaitForEvent`の公開エイリアス。
- `packages/doeff-events/src/doeff_events/handlers/memory.py`: 受信時のPromise作成・購読登録・Wait、発行時に現在の一致する購読者だけを完了させる処理、`isinstance`での型照合。
- `packages/doeff-core-effects/doeff_core_effects/scheduler.py`: `Spawn`で子をenqueueしてから親を再開する順序、同優先度FIFO、`Wait(Task/Future)`、`Race(*tasks)`、`Cancel`の契約、Promise・Semaphore・ExternalPromise。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py`: 仮想時間ハンドラとスケジューラの合成。
- `packages/doeff-time/tests/test_sim_migration_readiness.py`: 多数のイベント待受・時間との合成例。既存テストの転記ではなく、本稿自体のコードを実行して確認した。

## 実行確認

リポジトリのパッケージソースを`sys.path`へ追加し、`uv run --no-sync python`で次を実行した。

- `examples/event_loop.py::verify`: 正常終了で`("はじめに", "遊び方")`、0.5秒の期限で`"期限切れ"`。両ケース成功。
- `examples/scheduler_coordination.py::verify`: Gatherの結果が`["前半", "後半"]`、許可数1で仮想経過4秒、ExternalPromiseの結果が`"入力を受信"`。成功。
- `doeff-events.md`のPythonブロック3つを抽出し、各ブロックを独立した名前空間で`__name__="__main__"`として実行。すべて成功。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/event_loop.py publications/zenn-use-cases-v0/examples/scheduler_coordination.py`: 成功。

実LLM、実エージェント、ネットワーク、Docker、SSH、外部送信は実行していない。ローカルスレッドはExternalPromiseの検証で実際に起動した。製品runtime/packagesの変更、commit/pushはない。

## 画像への引き継ぎ

`events-visuals.json`を正本とし、概念図は購読→通知→継続、処理図は複数イベント型の反復待機→終了/期限を示す。各図へ短い日本語コメント付きコードを入れる。既存の図タイトル・キャプションは意味が合っているため維持した。
