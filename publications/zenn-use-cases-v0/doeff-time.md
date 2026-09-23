---
title: "20秒待つ処理を、20秒待たずにテストする — doeffの仮想時間"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

待機を含む処理を、そのままシミュレーションで動かしたい。この記事では、同じ処理本体を仮想時間と実時間で動かします。doeffでは処理を`@do`で定義し、操作を`yield`で依頼します。その依頼の実行方法を選ぶのがハンドラです。


![待機の手順はそのまま、時計を選ぶ](/images/zenn-use-cases-v0/generated/time-concept.png)

`Delay`と`GetTime`を書く処理本体は共通です。外側に取り付けるハンドラで、実時間の待機か仮想時計の進行かを選びます。


## 「待つ」を外側への依頼にする

```python
from doeff import do  # 関数を、実行前の計算を表すProgramにする。
from doeff_time import Delay  # 時計の実装に依存しない待機の依頼を使う。


@do  # 秒数だけを受け取る処理を、時計から独立したProgramにする。
def job(seconds: float):  # 呼び出しただけでは待機を始めない。
    yield Delay(seconds)  # 選んだ時間ハンドラが待機を終えるまで中断する。
    return "完了"  # 待機が終わると、この文字列を呼び出し元へ返す。
```

`job(10)`を実行すると、`Delay(10)`で「10秒待ってほしい」という依頼を渡します。このような操作をエフェクト（effect）と呼びます。

`yield`で依頼を外側へ渡すと、処理はいったん止まります。その依頼を扱うハンドラ（handler）が、続きをどう動かすかを決めます。

`job`自身は、`asyncio.sleep`を呼んでいません。現実の時計で待つのか、仮想時計を進めるのかを、外側で選べます。

`@do`の関数を呼ぶと、すぐに結果が返る代わりに、計算を表す`Program`ができます。ハンドラを取り付け、最後に`run`で実行します。

| 処理の内側が書くこと | 実行側が決めること |
| --- | --- |
| 10秒待ちたい | 実時間で待つか、仮想時間を進めるか |
| 別の処理を始め、結果を待ちたい | タスクをどう管理し、いつ再開するか |
| 外部サービスの結果がほしい | 実際に呼ぶか、テスト用の値を返すか |
| 完了済みの計算結果がほしい | 保存先から取得するか、計算して保存するか |

処理の手順と、その実行方法を分離するわけです。

## 見せ場：20秒と10秒の待機を、仮想時間で並行実行する

待機を単に省略するだけなら、テスト用の関数でもできます。

もう少し面白い例として、20秒待つ処理と10秒待つ処理を並行に走らせ、それぞれが何時に完了したかを調べてみます。

```python
from datetime import datetime, timezone  # 開始日時をUTCで固定する。

from doeff import do, run  # Programの定義と、この例の実行入口を用意する。
from doeff_core_effects.scheduler import Gather, Spawn, scheduled  # タスクを開始・収集する。
from doeff_time import Delay, GetTime, sim_time_handler  # 仮想時計で待機と時刻取得を扱う。


@do  # 待機と時刻取得を、同じ時間ハンドラで解釈できるようにする。
def worker(seconds: float):  # 20または10を渡すと、その秒数の待機を依頼する。
    yield Delay(seconds)  # 仮想時間なら時計が指定秒数進むまで待つ。
    return (yield GetTime())  # 再開した時点の日時を返す。


@do  # 2つの待機を、通常のyieldで組み立てる。
def workflow():  # 実時間でも仮想時間でも同じ処理を使う。
    start = yield GetTime()  # 経過秒数の基準となる開始日時を受け取る。
    slow = yield Spawn(worker(20))  # 20秒待つ処理を開始し、Taskを受け取る。
    fast = yield Spawn(worker(10))  # 10秒待つ処理も開始し、Taskを受け取る。
    finished = yield Gather(slow, fast)  # 完了順によらず、slow、fastの順で日時を得る。
    return [(time - start).total_seconds() for time in finished]  # 仮想時間なら[20.0, 10.0]。


start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 仮想時計の開始を00:00:00に固定する。
program = sim_time_handler(start_time=start)(workflow())  # 処理に仮想時間の解釈を取り付ける。
result = run(scheduled(program))  # 2タスクと仮想時計の進行を管理し、完了まで実行する。
assert result == [20.0, 10.0]  # 完了順ではなく、slow、fastの指定順で値が返る。
print(result)  # [20.0, 10.0]と表示する。
```

実際に20秒待つ必要はありません。仮想時間の上で、10秒後と20秒後にそれぞれの処理が完了します。`Gather`は引数の順で結果を返すので、表示は`[20.0, 10.0]`です。

```text
仮想時刻       0秒          10秒          20秒
20秒の処理    開始 ──────────────────── 完了
10秒の処理    開始 ──────── 完了
時計          実行可能な処理が待機したら、次の予定へ進む
```

ここでは、単に`Delay`へ固定値を返しているのではありません。仮想時間ハンドラは待機の予定を管理し、スケジューラと協調して、適切な時刻に処理を再開します。時計を進めるタスクは低い優先度で動くので、通常優先度の処理が実行できる間に時計だけが先走ることはありません。

**「何を返すか」に加えて、「いつ続きを動かすか」を扱える。** これが、単なる実装の差し替えから一歩進むところです。

### 実時間で動かすなら、外側を変える

先ほどの`worker`と`workflow`はそのまま使います。

```python
from doeff_core_effects.handlers import await_handler  # 非同期待機をスケジューラへ接続する。
from doeff_time import async_time_handler  # asyncioを使う実時間ハンドラを選ぶ。

program = async_time_handler()(workflow())  # 同じworkflowに実時間の解釈を取り付ける。
result = run(scheduled(await_handler()(program)))  # 実際に約20秒待って、両タスクの結果を得る。
print(result)  # おおむね[20.0, 10.0]。実時間なので起床の遅れなどによる誤差がある。
```

`async_time_handler`は、内部で`asyncio`を使って待ちます。`await_handler`は、その非同期の待機をdoeff側へ接続します。`scheduled`はタスクの実行を管理します。

処理本体には`async def`も`asyncio.sleep`もありません。実行基盤に必要な接続を、外側へまとめています。

これは、`asyncio`を使わなくなるという話ではありません。**`asyncio`への依存をハンドラ側に置き、処理本体では共通の`yield`を使う**という話です。

:::message
このコードは2026年9月16日時点の開発checkoutで確認しています。仮想時間の例と実時間の例は、前者の定義に続けて実行できます。`doeff_time`と`doeff_core_effects`を含む対応する環境が必要です。導入方法と検証版へのリンクは末尾にまとめています。
:::

## 指定時刻の処理と、優先度もコードで書く

3秒後まで待ち、8秒後に別の処理を動かします。優先度の例は別の計算として示します。

```python
from datetime import datetime, timedelta, timezone  # UTC日時と、そこからの秒数差を作る。

from doeff import do, run  # 予定処理をProgramにし、検証の入口で実行する。
from doeff_core_effects.scheduler import (  # タスクの優先度と終了待ちを扱う。
    PRIORITY_HIGH,  # 通常タスクより高い優先度。
    PRIORITY_IDLE,  # 通常タスクより低い優先度。
    Spawn,  # 別タスクを開始し、Taskを受け取る。
    Wait,  # Taskの完了を待つ。
    scheduled,  # タスクの実行順と再開を管理する。
)
from doeff_time import GetTime, ScheduleAt, SetTime, WaitUntil, sim_time_handler  # 仮想日時を扱う。


@do  # 予定処理と優先度の例で、実行された順序を記録する。
def record_label(events: list[str], label: str):  # 記録先と表示名を受け取る。
    events.append(label)  # 検証用リストへ、実行した時点でラベルを追加する。
    return label  # 通常のSpawnならWaitでこのラベルを受け取れる。


@do  # 絶対日時で予定を組み立てる。
def timeline(events: list[str]):  # 予定が実行された証拠をリストへ残す。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # タイムゾーン付きの開始日時を作る。
    yield SetTime(start)  # 仮想時計を00:00:00へ設定する。
    alarm = yield ScheduleAt(  # 00:00:08に実行する予定を登録し、Taskを受け取る。
        start + timedelta(seconds=8),  # 予定時刻を8秒後に指定する。
        record_label(events, "予定"),  # その時刻にラベルを記録するProgramを渡す。
    )
    yield WaitUntil(start + timedelta(seconds=3))  # 00:00:03になるまで待つ。
    now = yield GetTime()  # 待機後の仮想日時を取得する。
    assert now == start + timedelta(seconds=3)  # 8秒の予定より先に、3秒で一度再開したことを確認。
    assert events == []  # 8秒の予定はまだ実行されていない。
    yield Wait(alarm)  # 予定のTaskが終わるまで待ち、失敗した場合は例外を受け取る。
    return (yield GetTime())  # 予定が終わった00:00:08を返す。


@do  # 優先度は時間の設定と別に、スケジューラへの依頼として指定する。
def priorities(events: list[str]):  # 実行可能な2タスクがどちらから選ばれるかを記録する。
    background = yield Spawn(  # 背景処理を登録するが、通常優先度の親処理は先へ進める。
        record_label(events, "背景処理"), priority=PRIORITY_IDLE  # 実行を後回しにする。
    )
    urgent = yield Spawn(  # 高優先度の応答処理を追加する。
        record_label(events, "応答処理"), priority=PRIORITY_HIGH  # 背景処理より先に選ばれる。
    )
    yield Wait(urgent)  # 応答処理の完了を確認する。
    yield Wait(background)  # 背景処理も完了させ、未完了タスクを残さない。


def verify_timeline_and_priority() -> None:  # この例の実行結果を、順序も含めて検証する。
    events: list[str] = []  # 予定の実行回数を調べる記録先。
    end = run(scheduled(sim_time_handler()(timeline(events))))  # 指定時刻まで仮想時計を進める。
    assert end == datetime(2026, 1, 1, 0, 0, 8, tzinfo=timezone.utc)  # 最終時刻は8秒後。
    assert events == ["予定"]  # 予定が1回だけ動いたことを確認する。
    priority_events: list[str] = []  # 優先度の検証は別の記録先を使う。
    run(scheduled(priorities(priority_events)))  # 時間ハンドラなしで優先度を解釈する。
    assert priority_events == ["応答処理", "背景処理"]  # 高優先度が先に動いたことを確認する。


verify_timeline_and_priority()  # 外部通信も実時間の待機もなく、両方の例を確認する。
```

`ScheduleAt`から動く本文は、現行実装では時間ハンドラの外側へ委譲されます。この例はそこで追加の時間操作をせず、ラベルだけを記録します。予定の本文でも時計が必要なら、そのハンドラのスコープを明示する必要があります。この例の優先度は、実行可能なタスクを選ぶ順序に効いています。背景処理を登録した後も通常優先度の親処理が先へ進み、高優先度の応答処理を登録できるため、記録順は`["応答処理", "背景処理"]`になります。実行中のCPU処理を強制的に中断する仕組みではありません。

[完全な例](examples/scheduling.py)もリポジトリに保存しています。

## 同期待機を選ぶ場合

単独の待機なら、同じ`job`を同期ハンドラへ渡すこともできます。この例は2ミリ秒だけ実際に待ちます。`Delay`だけならスケジューラは不要ですが、`Spawn`や`ScheduleAt`を使うときは`scheduled`も必要です。

```python
from doeff import run  # 単独の待機を、この例の入口で実行する。
from doeff_time import sync_time_handler  # 実行スレッドを止めて待つ解釈を選ぶ。

program = sync_time_handler()(job(0.002))  # 同じjobに、2ミリ秒の同期待機を取り付ける。
assert run(program) == "完了"  # スレッドを実際に待機させた後、同じ戻り値を得る。
```

## 処理の流れ

![10秒で再開し、次に20秒で再開する](/images/zenn-use-cases-v0/generated/time-flow.png)

20秒のタスクと10秒のタスクが同じ仮想時刻に待機を始めます。10秒のタスクが先に再開し、最後にGatherが指定順で日時を返します。開始からの秒数へ変換すると`[20.0, 10.0]`です。


---

[doeffでできること：メイン記事へ戻る](doeff-main.md)

## 待機以外の時間操作

`WaitUntil`で特定時刻を待ち、`ScheduleAt`で時刻を指定して処理を開始できます。`GetTime`は選んだ時計の現在時刻です。`SetTime`を解釈するのは、この3種類では仮想時間ハンドラだけです。実時間ハンドラへ切り替えてOSの時計まで変更できる、という意味ではありません。日時はタイムゾーン付きの値を使います。同期的に実時計を待つ`sync_time_handler`もあり、非同期・同期・仮想時間という実行方法を選べます。実際にスレッドを止める同期待機は、非同期の待機と同じ並行性を保証しません。

ゲームの進行と組み合わせる例は[カードゲームの記事](doeff-games.md)を参照してください。時間以外の依頼との組み合わせは[ハンドラの合成](doeff-handlers.md)、`await`との関係は[coroutineとの違い](doeff-coroutines.md)で説明します。

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。以下はこの記事で確認した開発版へのリンクです。公開パッケージの最新版との一致は別途確認が必要です。

- [doeff本体と導入方法](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc)
- [実時間・仮想時間のハンドラ](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-time)
- [ハンドラの能力と関数の色の整理](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/docs/22-capability-classes.md)
- [結果を保存・再利用するハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
