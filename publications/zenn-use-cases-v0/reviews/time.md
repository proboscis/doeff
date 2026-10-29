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

## 2026-10-16 の改稿と検証

記事の書き方の決まり9項目と、週3の5行の構成に合わせて改稿した。

| 変えた所 | 内容 |
| --- | --- |
| 冒頭 | 悩み3つ（テストが遅い・待ちの順を試せない・時刻で結果が揺れる）から始める |
| 最初のコード | `job`と、仮想時計で20秒の待ちをすぐ終えるテスト関数 |
| ハンドラの中身 | 自作の`skip_delay`で、依頼の判別・`Resume`・担当外の`Pass`を見せる |
| 見せ場 | 20秒と10秒の並行待機をテスト関数にし、開始日時を固定する |
| 指定時刻 | `ScheduleAt`の本文を時計を使わない`alarm_body`にし、`Wait`で値を受け取る |
| 外した枠 | 同期の2ミリ秒の例と時刻表の枠。同期待機と優先度は完全な例に残す |
| 図 | 2枚を手で書いたSVGから作り直した（`imagegen/time-svg-source.txt`） |

検証はdoeffのorigin/main `914ea5629`で行った。

```sh
uv run python publications/zenn-use-cases-v0/reviews/time-check.py --real-time  # 5枠を順に実行し、test関数4つを呼ぶ。
uv run pytest -q --durations=0 <実時間の枠を除く4枠を1つにしたfile>  # 4 passed。関数の実行は合わせて約0.2秒。
uv run python publications/zenn-use-cases-v0/examples/scheduling.py  # 完全な例のテスト6つを実行し、OKを表示する。
```

- 5枠とも成功した。実時間の枠は`[20.091931, 10.058906]`を表示し、約20.09秒かかった。
- pytestの関数の実行時間：0.18秒・0.01秒・0.00秒・0.00秒（最初の関数は初回の読み込みを含む）。
- 予定の本文で`GetTime`を使うと`UnhandledEffect`になることを確かめ、本文の「時間ハンドラの外側で動く」を保った。

## 2026-10-29 英語の3本目「Time as an effect」の版への書き直し

`doeff-time.md`を、英語の3本目「Time as an effect」(`docs/articles/time-as-an-effect.md`)の版に書き直した。上の節までの指摘は、前の本文(`job`・`skip_delay`・`worker`・`ScheduleAt`の例)への物である。

- 軸と例: 軸は英語の版と同じ代数的エフェクト(時間をエフェクトで頼み、`k`を受け取るハンドラが、続きをいつ動かすかを決める)。Python・bashの枠20個は、コメントを除くと英語の版の枠と1つずつ同じ。textの枠も英語の版と同じ。コメントは各行の日本語にした(この連載の決まり)。
- 英語の版との違い: 題・slug・入門への案内・前後の回のリンクは、Zennの連載の物を残した。英語の版の「前の回・次の回」(yieldとasync・記録と再生)は、Zennの9本目(`doeff-coroutines.md`)と6本目(`doeff-replay.md`)へのリンクにした。mermaidの図は、日本語のSVGの処理図(`time-flow`)にした。概念図(`time-concept`)は、同じ`two_naps`を`ticking`と`sim_time_handler`で走らせた時の待ちの並びにした。1文60字までの決まりに合わせ、長い文は2つに分けた。
- 前の本文から外した物: `SetTime`・優先度・`ScheduleAt`の予約の例。どれも完全な例`examples/scheduling.py`に残り、`verify_examples.py`が走らせる。`ScheduleAt`の本体のハンドラの範囲は、本文の限界の節に書いた。
- 確かめ(`time-check.py`を書き直した): 枠が英語の版と同じ事、clock.pyとreport.pyの抜粋を掲載の順に走らせた実行の行の印字が、直後のtextの枠と同じ事(仮想の時間の行は3回とも同じ)、test_report.pyがreport.pyの抜粋に対して通る事、実時間の行(既定は0.2秒と0.1秒の待ちに替える・`--real-time`で20秒の本物)、ハンドラ無しの`nap`が`Delay`で`UnhandledEffect`になる事、`within`が実時間の時計でも期限で打ち切る事、本文にAIが書いたと名乗る文とIOの差し替えの語が無い事。本文の印字・コード・テスト・語をわざと壊した6か所は、どれも赤になった。

```sh
uv run --no-sync python publications/zenn-use-cases-v0/reviews/time-check.py  # 手元の環境で、実時間の行は短い待ちで走らせる。
uv run --no-project python publications/zenn-use-cases-v0/verify_pypi.py --doeff 0.8.0 --python 3.12 time  # PyPIのdoeffだけの環境で、20秒の行も走らせる。
```

- `verify_pypi.py`: PyPIのdoeff 0.8.0とdoeff-time 0.1.0で、Python 3.11・3.12・3.14ともOK。実時間の行は`[20.059413, 10.056975]`(3.12)。
- `render_release.py doeff-time.md`: 60字を越える文0個・です・ます以外で終わる文0個。`export_zenn.py`と`zenn-check/check.mjs`: この回の崩れ0個。本の第3章も崩れ0個。
- `verify_examples.py`の全体は、この変更の前からの失敗(2つ目の例`operations.py`の`log_handler()`の引数)で止まる。時間の回に当たる所(`scheduling.py`・`time-check.py`・本文の構文とリンクと画像・注釈の検)は通した。
