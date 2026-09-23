# 時間スケジューリングの記事レビュー

## 担当と確認対象

- 担当: `/root/review_time`
- 日付: 2026-09-16
- 記事: `publications/zenn-use-cases-v0/doeff-time.md`
- 実行例: `publications/zenn-use-cases-v0/examples/scheduling.py`
- 画像仕様: `publications/zenn-use-cases-v0/reviews/time-visuals.json`
- 適用スキル: `doeff-patterns`、`doeff-runtime`

## 指摘と修正

主題である20秒と10秒の並行待機が専用例の検証対象に入っていなかった。`worker`と`workflow`を専用例にも入れ、仮想時刻での結果`[20.0, 10.0]`を検証した。`Gather`が返すのは指定順の日時であり、開始日時との差を秒数へ変換している点を本文と図の仕様に揃えた。

優先度の旧検証はラベルの集合だけを比較し、どちらが先に動いたかを確認できていなかった。時間取得と優先度の観測を分け、`scheduled(priorities(...))`だけで`["応答処理", "背景処理"]`を確認する例へ変更した。通常優先度の親タスクが低優先度の背景処理より先へ進め、高優先度の応答処理を登録できることを説明した。優先度は協調的なタスク選択であり、CPU処理を強制中断する機能ではない。

`SetTime`を3種類のハンドラがすべて扱うかのような説明を修正した。現行の非同期・同期の実時間ハンドラに`SetTimeEffect`の節はなく、外側へ`Pass`する。本文では仮想時計への日時設定として限定している。

同期実行のためだけに`run()`を呼ぶ通常関数を定義する旧例を取り除いた。例の入口でハンドラを取り付けて実行する形にし、同じ`job(seconds)`が仮想時間・非同期の実時間・同期の実時間で`"完了"`を返すことを専用例で確認した。長い同期待機は2ミリ秒へ短縮し、実際の同期待機も検証対象にした。

`ScheduleAt`が返す`Task`は`Wait`で最後まで待つ。予定の本文を時間ハンドラの外側で実行する現行スコープについての注意を維持し、8秒の予定が3秒時点ではまだ動いていないことも確認した。時間ハンドラが予定本文の戻り値を引き継ぐとは主張していない。

合成する処理はすべて`@do`で定義する。並行処理は`yield Spawn(...)`と`yield Gather(...)`、時間操作は`yield Delay/GetTime/...`で合成する。処理全体を`async def`へ逃がす例はない。

## 実装根拠

- `packages/doeff-time/src/doeff_time/effects/time.py`: 秒数の有限・非負チェック、タイムゾーン付き日時、各依頼の引数。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py`: 低優先度の時計ドライバ、待機予定とPromiseの組み合わせ、`SetTime`、`ScheduleAt`が返すTask。
- `packages/doeff-time/src/doeff_time/handlers/async_time.py`: `Delay`から`Await(asyncio.sleep(...))`への変換、実時計の`GetTime`、`SetTime`が未対応であること。
- `packages/doeff-time/src/doeff_time/handlers/sync_time.py`: `time.sleep`による同期待機、`ScheduleAt`がスケジューラを要すること。
- `packages/doeff-core-effects/doeff_core_effects/scheduler.py`: 高い優先度から選ぶready queue、親タスクの優先度を維持するSpawnの再開、指定順で結果を返すGather。
- `packages/doeff-time/tests/test_sim_time.py`: 仮想時刻、並行タスクごとの再開時刻、時計ドライバの優先度。
- `packages/doeff-time/tests/test_schedule_at.py`: 予定本文の外側ハンドラ、Taskの完了待ちと失敗伝播。

## 検証

```sh
.venv/bin/python publications/zenn-use-cases-v0/examples/scheduling.py # 並行待機・指定時刻・順序・3種類の時計を検証し、OKを表示する。
.venv/bin/ruff check publications/zenn-use-cases-v0/examples/scheduling.py # 専用例のlintが通ることを確認する。
.venv/bin/python -m pytest packages/doeff-time/tests/test_sim_time.py packages/doeff-time/tests/test_schedule_at.py -q # 参照した時間・予定の契約24件を確認する。
```

- 専用例: 成功。仮想時刻の20秒・10秒、3秒時点の未実行、8秒時点の予定完了、優先度の順序を確認。
- ハンドラ交換: 同じ`job(0.002)`を3種類で実行し、いずれも`"完了"`を返すことを確認。
- 本文のPython 5ブロックを上から順に同じ名前空間で実行。仮想時間は`[20.0, 10.0]`、実時間は`[20.002209, 10.002656]`を観測。実時間の20秒例も省略していない。
- 実時間の結果はテスト実行時に20秒以上25秒未満、10秒以上15秒未満に入ることも確認。本文は環境依存の起床遅延を含む近似値として説明している。
- 本文の同期2ミリ秒例も成功。
- Ruff: 成功。
- 既存テスト: 24 passed。絞ったテスト実行のため、収集されていないADRについての既存警告が1件出た。
- 各行コメント: Pythonトークンを読み、空行・説明済みの閉括弧・モジュールdocstringを除く本文78行、専用例78行の全行にコメントがあることを確認。目的と期待する値・動作を目視でも確認。
- 図のコードはconceptが4行、flowが3行。全行に短い日本語コメントを付け、図が示す時刻と戻り値を実装・本文に揃えた。

本文の検証は次の手順で再実行できる。3番目のブロックは実際に約20秒待つ。

```python
from pathlib import Path  # 記事の本文を読み込む。
import re  # Pythonのコードブロックを取り出す。

article = Path("publications/zenn-use-cases-v0/doeff-time.md")  # 検証する記事を指定する。
namespace = {}  # 前のブロックの関数を次のブロックで使えるようにする。
blocks = re.findall(r"```python\n(.*?)```", article.read_text(), re.S)  # 5ブロックを順に得る。
for index, code in enumerate(blocks, 1):  # 本文の掲載順に実行する。
    exec(compile(code, f"{article}:block-{index}", "exec"), namespace)  # 実例とassertを確認する。
    print(f"OK: block {index}")  # 成功したブロック番号を表示する。
```

## 検証の範囲と画像

外部HTTP、LLM、agent、Docker、SSH、クラウド接続は実行していない。実時間ハンドラの検証はローカルな時計と待機だけである。OSの時計は変更していない。製品コード、共有の検査スクリプト、他記事は変更していない。

画像の生成・差し替えは親担当が実施する。JSONの仕様を正本として本文のalt/captionを更新済み。概念図は同じProgramに3種類の時間解釈から1つを取り付ける構造、処理図は10秒→20秒の再開順と、指定順に結果を集めるGatherを示す。
