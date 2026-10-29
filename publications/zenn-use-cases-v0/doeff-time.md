---
title: "テストで20秒待つのをやめる"
emoji: "⏱️"
type: "tech"
topics: ["python", "doeff", "algebraiceffects", "テスト", "asyncio"]
published: false
---

*待つコードは、ハンドラに時間を頼みます。ハンドラはコードの残りを持ち、それをいつ動かすかを決めます。だからテストでは何日分もの予定を数ミリ秒で走らせ、本番では同じコードを実時間で動かせます。*

Pythonの代数的エフェクト(algebraic effects)を、[doeff](https://github.com/proboscis/doeff)で紹介します。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。入門では、エフェクトとハンドラの仕組みを説明しました。この記事では、時間をエフェクトにします。

`await asyncio.sleep(1800)`は、本番でもテストでも30分待ちます。待つコードは確かめにくく、その訳は次の3つです。3つは、たいてい一緒に来ます。

- 待ちは、テストでも本番と同じだけ時間がかかります。
- 同時に走る2つの待ちは、正しい順に終わらなければなりません。だから、待ちのたびにすぐ返すだけでは足りません。
- 「次の平日の9時」のように時計を読むコードは、テストを走らせた時刻によって答えが変わります。

`asyncio`では、イベントループが時計を持っています。`asyncio.sleep`はループにタイマーを置き、coroutineをいつ続けるかはループが決めます。この記事では、その決めごとをハンドラへ移します。コードはエフェクトで時間を頼み、それに答えるハンドラは、コードの残りを受け取ります。だから、どの時計を使うかはハンドラが選びます。先へ飛ぶ仮想の時計でも、`asyncio`を通した実時間の時計でもよく、その間のコードは変わりません。

入門を読んでいなくても、この記事に要る語は次の3つです。

- **エフェクト**は、`@do`の関数が何かを頼むために`yield`する、ただの値です。関数は、その`yield`で止まります。
- **ハンドラ**は、`@handler`を付けた`(effect, k)`の関数です。`k`は止まった関数の残り(継続)で、ハンドラはそれをどう進めるかを決めます。`yield Resume(k, value)`は、`value`を`yield`の結果にして、残りを走らせます。
- ハンドラは**呼ぶ側**が付けます。`handler(program)`は、`program`の周りに`handler`を付けた物です。

## 時間を頼む

[doeff-time](https://github.com/proboscis/doeff/tree/main/packages/doeff-time)は、doeffの時間のエフェクトと、それに答えるハンドラのパッケージです。次の`nap`は時間を2回頼み、どの時計で待つかは一度も言いません。

```python
# clock.py(抜粋)
from datetime import datetime, timedelta, timezone  # 時刻・時刻の差・UTCを使う。

from doeff import Gather, Resume, Spawn, do, handler, run  # 集める・再開・始める・定義・ハンドラ・実行を使う。
from doeff_core_effects.handlers import await_handler  # asyncioの待ちを、スケジューラの下で走らせるハンドラ。
from doeff_core_effects.scheduler import scheduled  # SpawnとGatherに答える、doeffの参考のハンドラ。
from doeff_time import (  # doeff-timeの、時間のエフェクトと時計のハンドラ。
    Delay,  # 待ちを頼むエフェクトを作る。
    DelayEffect,  # Delay(...)が作るエフェクトのクラス。
    GetTime,  # 今の時刻を問うエフェクトを作る。
    GetTimeEffect,  # GetTime()が作るエフェクトのクラス。
    async_time_handler,  # asyncioで実際に待つ時計のハンドラ。
    sim_time_handler,  # doeff-timeの仮想の時計のハンドラ。
)

START = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)  # 時計の始まり。2026年11月30日(月)9:00 UTC。


@do  # napを、yieldのたびに止まるプログラムにする。
def nap(seconds: float):  # 時間を2回頼み、どの時計かは一度も言わない。
    yield Delay(seconds)  # seconds秒待ちたいと頼む。
    return (yield GetTime())  # 起きた時の時刻を問い、それを返す。
```

- `yield Delay(seconds)`は、`seconds`秒待ちたいと頼みます。
- `yield GetTime()`は、今の時刻を問います。答えは、タイムゾーン付きの`datetime`です。
- `Delay(...)`は`DelayEffect`を、`GetTime()`は`GetTimeEffect`を作ります。ハンドラは、受け取るエフェクトのクラスを引数の型の注釈に書き、どのエフェクトを受け取るかを示します。

`nap(20)`をハンドラ無しで走らせると、`Delay`の所で`UnhandledEffect`になって止まります。エフェクトは、ハンドラが意味を与えるまで、何の意味も持ちません。

## 時計は、`k`をいつ動かすかを決めるハンドラ

時計を自分で書いてみます。この時計は、時刻を変数に持ちます。`Delay`が来たら時刻を進め、すぐに`k`を再開します。`GetTime`が来たら、持っている時刻で`k`を再開します。

```python
# clock.py(抜粋)
def ticking(start: datetime):  # 自作の時計。すぐにkを再開し、針を進める。
    now = start  # この時計が持つ今の時刻。

    @handler  # clockを、プログラムに付けられるハンドラにする。
    def clock(effect: DelayEffect | GetTimeEffect, k):  # DelayとGetTimeだけを受け取る。
        nonlocal now  # 外の関数のnowを書き換える。
        if isinstance(effect, DelayEffect):  # 待ちの依頼なら、
            now += timedelta(seconds=effect.seconds)  # 頼まれた秒数だけ針を進め、
            return (yield Resume(k, None))  # 待たずに、すぐ続きを再開する。
        return (yield Resume(k, now))  # 時刻の問いには、持っている時刻で答える。

    return clock  # startから始まる時計のハンドラを返す。
```

```python
print(run(ticking(START)(nap(20))))  # 自作の時計で、20秒のnapを1つ走らせる。
```

```text
2026-11-30 09:00:20+00:00
```

この時計の上では20秒が過ぎ、あなたの時計では1秒も過ぎていません。タスクが1つなら、仮想の時計の仕事はこれだけです。次は、2つのタスクが同時に待ちます。1つは20秒、もう1つは10秒です。

```python
# clock.py(抜粋)
@do  # two_napsを、yieldのたびに止まるプログラムにする。
def two_naps(slow: float = 20, fast: float = 10):  # 2つのタスクが同時に待つ。
    start = yield GetTime()  # 始めの時刻を問う。
    slow_task = yield Spawn(nap(slow))  # 20秒のnapを、タスクとして始める。
    fast_task = yield Spawn(nap(fast))  # 10秒のnapも始める。
    woke = yield Gather(slow_task, fast_task)  # 2つのタスクが起きた時刻を、引数の順に受け取る。
    return [(at - start).total_seconds() for at in woke]  # 始めから何秒後に起きたかに直す。
```

`Spawn`は、プログラムをタスクとして始めます。`Gather`はタスクを待ち、引数の順に結果を返します。どちらもエフェクトで、`scheduled`はそれに答えるdoeffの参考のハンドラです。結果の数は、それぞれのタスクが始めから何秒後に起きたかです。

```python
print(run(scheduled(ticking(START)(two_naps()))))  # 自作の時計で、2つのタスクを走らせる。
```

```text
[20.0, 30.0]
```

10秒の`nap`が、始めから30秒後に起きました。`ticking`は遅いタスクの`k`をすぐに再開したので、速いタスクが待ち始める前に、遅いタスクの20秒が時計に載りました。そこへ、速いタスクが10秒を足しました。いくつものタスクを受け持つ時計は、待ちが来るたびにその場で答えることはできません。待っている全部のタスクの`k`を、どのタスクも動けなくなるまで持ちます。そして時刻をいちばん早い起床まで進め、そのタスクから再開します。

doeff-timeの`sim_time_handler`は、これを行う時計です。

```python
print(run(scheduled(sim_time_handler(start_time=START)(two_naps()))))  # doeff-timeの仮想の時計で、同じ2つのタスクを走らせる。
```

```text
[20.0, 10.0]
```

遅いタスクは始めから20秒後に、速いタスクは10秒後に起き、実行に時間はかかりませんでした。この実行を、やりとり1つずつで並べると次のとおりです。

![sim_time_handlerが、待つkを持って時計を進める](/images/zenn-use-cases-v0/generated/time-flow.png)
*2つのnapが待ちで止まると、時計はそれぞれのkを持ちます。どのタスクも動けない時にだけ、時刻を次の起床まで進めて、そのkを再開します。*

`sim_time_handler`は、待っている`k`を、それぞれの起床の時刻まで持ちます。図は、再開を`Resume`と書いています。コードの`sim_time_handler`は、入門で見た`Transfer(k, value)`で再開します。タスクは続きを進め、ハンドラに制御は戻りません。時計を進めるのは、優先度がいちばん低い(idleの)タスクです。スケジューラはこのタスクを、ほかの全部のタスクが待っている時にだけ走らせます。だから時刻は、どのタスクも動けない時にだけ進みます。このハンドラは、どのプログラムでも`yield`できるエフェクトだけを通して、スケジューラとやりとりします。docsの章[Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant)では、これに似た時計を約30行で作っています。

これが、時間を、呼び出す関数でなくエフェクトにする訳です。ハンドラは、`yield`が何を返すかを決めます。そして`k`を持つので、プログラムの残りをいつ走らせるかも決めます。

![同じtwo_napsでも、kをいつ動かすかで答えが変わる](/images/zenn-use-cases-v0/generated/time-concept.png)
*tickingは待ちのたびにすぐkを再開するので、2つの待ちが足し算になります。sim_time_handlerは、どのタスクも動けなくなるまでkを持つので、2つの待ちが重なります。*

## 何日分もの予定を、数ミリ秒で

次は、もっと長い時間を含むプログラムです。平日の毎朝9時にレポートを作り、1回の作成には10分の期限を付けます。

```python
# report.py(抜粋)
from doeff import Cancel, Pass, Spawn, TaskCancelledError, Wait, do, handler, run  # 取り消し・委譲・始める・待つ・定義・実行を使う。
from doeff_core_effects.scheduler import scheduled  # タスクのエフェクトに答える、doeffの参考のハンドラ。
from doeff_time import (  # doeff-timeの、時間のエフェクトと仮想の時計。
    Delay,  # 待ちを頼むエフェクトを作る。
    DelayEffect,  # Delay(...)が作るエフェクトのクラス。
    GetTime,  # 今の時刻を問うエフェクトを作る。
    RaceWithin,  # タスクを、期限までだけ待つエフェクトを作る。
    WaitUntil,  # 決めた時刻まで待つエフェクトを作る。
    WaitUntilEffect,  # WaitUntil(...)が作るエフェクトのクラス。
    sim_time_handler,  # doeff-timeの仮想の時計のハンドラ。
)


# プログラム:平日の毎朝9時にレポートを作る。1回の作成には10分を与える。
def next_weekday_at(now: datetime, hour: int) -> datetime:  # nowより後で、いちばん近い平日のhour時を返す。
    at = now.replace(hour=hour, minute=0, second=0, microsecond=0)  # 同じ日のhour時ちょうど。
    if at <= now:  # その時刻をもう過ぎていれば、
        at += timedelta(days=1)  # 次の日にする。
    while at.weekday() >= 5:  # 土曜と日曜なら、
        at += timedelta(days=1)  # 平日になるまで1日ずつ送る。
    return at  # 次の平日のhour時。


@do  # buildを、yieldのたびに止まるプログラムにする。
def build(minutes: int):  # 言われたとおりの時間がかかる一歩。
    yield Delay(minutes * 60)  # minutes分待つ。
    return f"built in {minutes} min"  # 何分で作れたかを返す。


@do  # withinを、yieldのたびに止まるプログラムにする。
def within(seconds: float, program):  # programの値か、"timed out"を返す。
    task = yield Spawn(program)  # programを、タスクとして始める。
    outcome = yield RaceWithin((task,), seconds)  # タスクを、seconds秒までだけ待つ。
    if not outcome.timed_out:  # 期限の前に終われば、
        return outcome.value  # その値を返す。
    yield Cancel(task)  # 期限が先に来たら、タスクを取り消す。
    try:  # 取り消したタスクの後始末が終わるのを待つ。
        return (yield Wait(task))  # 後始末が終わると、このyieldでTaskCancelledErrorが起きる。
    except TaskCancelledError:  # 取り消しが届いたら、
        return "timed out"  # 期限切れと返す。


@do  # report_dayを、yieldのたびに止まるプログラムにする。
def report_day(minutes: int):  # 次の平日の9時まで待ち、レポートを作る。
    now = yield GetTime()  # 今の時刻を問う。
    yield WaitUntil(next_weekday_at(now, 9))  # 次の平日の9時まで待つ。
    started = yield GetTime()  # 作り始めた時刻を問う。
    result = yield within(10 * 60, build(minutes))  # 10分の期限を付けて作る。
    finished = yield GetTime()  # 終わった時刻を問う。
    return f"{started:%a %d %H:%M}-{finished:%H:%M} {result}"  # 曜日・日・始めと終わりの時刻・結果を1行にする。


@do  # daily_reportを、yieldのたびに止まるプログラムにする。
def daily_report(build_minutes: list[int]):  # 1日に1行を返す。
    lines: tuple[str, ...] = ()  # 作った行をためる。
    for minutes in build_minutes:  # 1日分ずつ、
        lines = (*lines, (yield report_day(minutes)))  # その日のレポートを作り、行を足す。
    return lines  # 全部の日の行を返す。
```

- `next_weekday_at`は、ふつうのPythonの関数です。`now`を引数で受け取るので、時計を読みません。
- `build`は、言われたとおりの時間がかかります。だから、1回の作成だけを遅くできます。
- `within`はタイムアウトです。`program`をタスクとして始め、`RaceWithin((task,), seconds)`を`yield`します。これは「タスクを、長くても`seconds`秒まで待つ」という依頼です。期限が先に来たら、タスクを取り消して、その後始末が終わるのを待ちます。後始末が終わると、`Wait`の`yield`で`TaskCancelledError`が起きます。
- `report_day`は、時刻を`GetTime`で問い、`WaitUntil`で待ちます。自分では時計を読まないので、何をするかは、どの時計が答えるかで決まります。いつ走らせたかでは決まりません。
- `daily_report`は、作成1回ごとに`report_day`を1回走らせます。`@do`の関数からプログラムを`yield`すると、それを走らせて、その結果を受け取れます。

仮想の時計を、金曜の夕方から始めます。

```python
# report.py(抜粋)
FRIDAY_EVENING = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)  # 金曜の夕方。2026年11月27日17:00 UTC。


def simulated(program):  # 金曜17:00から始まる、仮想の時間で走らせる。
    return scheduled(sim_time_handler(start_time=FRIDAY_EVENING)(program))  # 仮想の時計を、scheduledの内側に付ける。
```

```python
for line in run(simulated(daily_report([3, 12, 5]))):  # 3分・12分・5分かかる作成の3日分を、仮想の時間で走らせる。
    print(line)  # 1日に1行を印字する。
```

```text
Mon 30 09:00-09:03 built in 3 min
Tue 01 09:00-09:10 timed out
Wed 02 09:00-09:05 built in 5 min
```

週末が過ぎ、12分かかる作成は10分の期限に当たりました。`within`があきらめた時、時計は09:10を指していました。この実行は、仮想の時間で金曜の夕方から水曜の朝までを進み、数ミリ秒で終わります。どの日に走らせても、毎回同じ行を印字します。

### 後から時計を読むテスト

```python
# test_report.py
from datetime import datetime, timezone  # 時計が止まった時刻を、UTCの日時と比べる。

from doeff import run  # プログラムを最後まで走らせる。
from doeff_core_effects.scheduler import scheduled  # タスクのエフェクトに答える、doeffの参考のハンドラ。
from doeff_time import SimClock, sim_time_handler  # 仮想の時計そのものと、それで答えるハンドラ。

from report import FRIDAY_EVENING, daily_report  # 確かめるプログラムと、その始まりの時刻。


def test_a_slow_build_times_out_and_the_next_day_still_runs():  # 遅い作成は期限で切られ、次の日もまた走る。
    clock = SimClock(FRIDAY_EVENING)  # 金曜の夕方から始まる仮想の時計。
    lines = run(scheduled(sim_time_handler(clock=clock)(daily_report([3, 12, 5]))))  # その時計をハンドラに渡して走らせる。
    assert lines[1] == "Tue 01 09:00-09:10 timed out"  # 火曜の12分の作成は、10分で切られる。
    assert lines[2] == "Wed 02 09:00-09:05 built in 5 min"  # 水曜も、また9時に作る。
    assert clock.current_time == datetime(2026, 12, 2, 9, 5, tzinfo=timezone.utc)  # 時計は、最後の作成が終わった水曜9:05で止まった。
```

`SimClock`は、仮想の時計そのものです。`sim_time_handler`に渡すと、テストは時刻がどこで止まったかを読めます。ここでは、最後の作成が終わった水曜の09:05です。テストは時刻をぴったりの値で確かめます。実行の中に、機械の速さで変わる物が何も無いからです。

### どの時計の周りにも、自分の層を

ハンドラは、プログラムと時計の間に入ることもできます。次の`timeline`は、待ちが始まるたびに、その待ちを印字します。

```python
# report.py(抜粋)
@handler  # timelineを、プログラムに付けられるハンドラにする。
def timeline(effect: DelayEffect | WaitUntilEffect, k):  # 待ちが始まるたびに、その待ちを印字する。
    now = yield GetTime()  # このハンドラも、外の時計に今の時刻を問う。
    if isinstance(effect, DelayEffect):  # 秒数で待つ依頼なら、
        print(f"{now:%a %H:%M} wait {effect.seconds:g} s")  # 今の時刻と、待つ秒数を印字する。
    else:  # 時刻まで待つ依頼なら、
        print(f"{now:%a %H:%M} wait until {effect.target:%a %H:%M}")  # 今の時刻と、待つ先の時刻を印字する。
    return (yield Pass(effect, k))  # 待ちとkを、外の時計へ渡す。
```

```python
run(simulated(timeline(daily_report([3, 12]))))  # timelineを、プログラムと仮想の時計の間に付けて走らせる。
```

```text
Fri 17:00 wait until Mon 09:00
Mon 09:00 wait 180 s
Mon 09:03 wait until Tue 09:00
Tue 09:00 wait 720 s
```

`timeline`は、自分でもエフェクトを`yield`します。その`GetTime`は、ほかの`GetTime`と同じく外の時計まで届きます。続く`Pass(effect, k)`は、待ちと`k`を外の時計へ渡し、`k`をいつ動かすかは時計が決めます。`timeline`が受け取るのは`Delay`と`WaitUntil`だけです。だから、プログラムの`GetTime`と`RaceWithin`の期限は、`timeline`を通り過ぎて時計へ届きます。この層を付けると、実行の結果だけでなく、その実行が組んだ予定の全体が見えます。そのために、プログラムにログの処理を書く必要はありません。次の節の実時間の時計の周りに付ければ、同じ層が、待ちを起きたとおりに印字します。

## 同じコードを実時間で

```python
# clock.py(抜粋)
def wall_clock(program):  # 実時間の時計。asyncioで待つ。
    return scheduled(await_handler()(async_time_handler()(program)))  # 時計がasyncioの待ちを頼み、await_handlerがそれをイベントループで走らせる。
```

```python
print(run(wall_clock(two_naps())))  # 同じtwo_napsを、実時間の時計で走らせる(20秒かかる)。
```

この実行は20秒かかり、`[20.05, 10.05]`ほどを印字します。実時間の時計のタイマーは、少し遅れて起きるからです。`async_time_handler`は、`Delay`に`asyncio.sleep`のawaitで答えます。`GetTime`には、システムの時計で答えます。`await_handler`は、そのawaitを、スケジューラの下の`asyncio`のイベントループで走らせます。`run(wall_clock(daily_report([3, 12, 5])))`も、次の平日の9時まで待ちます。`within`のタイムアウトは、実時間の10分で作成を取り消します。

`nap`も`daily_report`も、`async def`ではありません。`asyncio`を選ぶのはプログラムでなく、ハンドラです。待つ間スレッドを止めるコードには、`time.sleep`で待つ`sync_time_handler`があります。

| 実行 | 時計 | 同じ`daily_report`がすること |
|---|---|---|
| テスト | `sim_time_handler(start_time=FRIDAY_EVENING)` | 金曜の夕方から水曜の朝までを数ミリ秒で進み、毎回同じ行を印字する |
| 時計を読むテスト | `sim_time_handler(clock=SimClock(FRIDAY_EVENING))` | 同じ動きに加え、テストが時刻の止まった所を読む |
| 予定のテスト | `simulated`の内側の`timeline` | 待ちの1つ1つを、仮想の時刻とともに印字する |
| 本番 | `await_handler()`の内側の`async_time_handler()` | `asyncio`で待ち、タイムアウトは実時間の10分で取り消す |

## ほかの方法との比べ

Pythonには、テストで待ちを速くする方法がもうあります。20秒と10秒待つ2つのタスクで、それぞれを走らせました。使った版は次のとおりです。

- CPython 3.12
- trio 0.34.0・looptime 0.9
- freezegun 1.5.5・time-machine 3.6.0

| | テストの中の待ち | 2つの待ちが時刻の順に終わるか | 日付と時刻が待ちとともに進むか | あなたのコード |
|---|---|---|---|---|
| `asyncio.sleep`を、すぐ返すように書き換える | すぐ返る | いいえ(どちらも0秒と報告し、始めた順に終わる) | いいえ | 変えない |
| freezegunの`freeze_time()` | 返らない(ループの時計も止まり、`real_asyncio=True`なら実時間で待つ) | — | いいえ(`datetime.now()`は止めた時刻のまま) | 変えない |
| time-machineの`travel(..., tick=False)` | 実時間で待つ | はい(ただし実時間で) | いいえ | 変えない |
| trioのテスト用の時計(`autojump_threshold=0`)、`asyncio`ならlooptime | すぐ返る | はい | 一部(`trio.current_time()`や`loop.time()`は進み、`datetime.now()`は進まない) | 変えない(trioの時計には、trioで書いたコードが要る) |
| doeff-timeの`sim_time_handler` | すぐ返る | はい | はい(`GetTime`と待ちに、1つのハンドラが答える) | `yield`とdoeff-timeのエフェクトで書く |

コードがもう`async`で、待ちを速くするだけでよい場合があります。それなら、trioの自動で進む時計とlooptimeが、コードに触れずに待ちを速くします。その場合は、そちらのほうが簡単です。doeffが加えるのは、時計がイベントループの一部でなく、あなたのハンドラの重なりの中の1つのハンドラだという点です。日付は待ちと同じ時計から来るので、「次の平日の9時」のような予定も仮想の時間で走ります。上の`ticking`のように時計を自分で書くことも、`timeline`のような層を周りに付けることもできます。代わりに払うのは、入門と同じく、コードの中の`yield`と`@do`です。

## 限界

- 仮想の時間が進むのは、全部のタスクがエフェクトで待っている時だけです。ループで計算を続けるタスクは、`yield`するまで時計を止めたままにします。`time.sleep`やソケットの読み込みのように止まる呼び出しは、どの時計の上でも、全部のタスクを止めます。
- 仮想になるのは、エフェクトで頼んだ時間だけです。`datetime.now()`と`time.time()`は、システムの時計を読みます。あなたのコードの中でも、そのコードが呼ぶライブラリの中でも同じです。`next_weekday_at`のように時刻を引数で渡すか、`GetTime`で問います。
- `sim_time_handler`は、`scheduled(...)`の内側に付けます。そして、それを待つタスクを始める全部の`Spawn`の外側に付けます。そうすると、タスクが1つの時計を共有します。
- doeff-timeの`ScheduleAt`に渡したプログラムは、時間のハンドラの外側のハンドラの下で走ります。だから、そのプログラムは自分では時間を頼めません。
- 実時間の時計のタイマーは、上の実行のとおり、少し遅れて起きます。時刻をぴったりの値で確かめるテストは、仮想の時計の上で走らせます。
- doeffは0.x、doeff-timeは0.1なので、版が変わると名前が変わることがあります。

## 試す

```bash
pip install doeff doeff-time pytest   # CPython 3.11から3.14で入る。
```

この記事の例はどれも、doeff 0.8.0とdoeff-time 0.1.0で、載せたとおりに走ります。完全なファイルは[examples/time_as_an_effect](https://github.com/proboscis/doeff/tree/main/examples/time_as_an_effect)にあります。[doeff-timeのREADME](https://github.com/proboscis/doeff/blob/main/packages/doeff-time/README.md)は、時計ごとに守る約束を挙げています。

今のテストで、時間が遅さや不安定さの元になっているのはどこですか。backoffを挟むリトライでしょうか、cronのような予定でしょうか、タイムアウトでしょうか。issueは https://github.com/proboscis/doeff/issues で受け付けています。

`yield`と`await`は、9本目の[async/awaitがあるのに、なぜyieldで書くのか](doeff-coroutines.md)で比べます。ハンドラが返す答えは、別のハンドラが記録することもできます。記録から同じプログラムを走らせ直す話は、6本目の[外部APIのテストで通信するのをやめる](doeff-replay.md)にあります。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月29日に確かめました。PyPIのdoeff 0.8.0とdoeff-time 0.1.0を使いました。Pythonは3.11・3.12・3.13・3.14で試しました。

- [この記事の完全な例(clock.py・report.py・test_report.py)](https://github.com/proboscis/doeff/tree/main/examples/time_as_an_effect)
- [doeff-timeのREADME(時計ごとの約束)](https://github.com/proboscis/doeff/blob/main/packages/doeff-time/README.md)
- [Why Effects?の節「Virtual time: hours of waiting in an instant」](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant)
- [trioのテスト用の時計](https://trio.readthedocs.io/en/stable/reference-testing.html)
- [looptime](https://github.com/nolar/looptime)
- [freezegun](https://github.com/spulec/freezegun)
- [time-machine](https://github.com/adamchainz/time-machine)
- 前の回:[Claude Codeを起動してテストするのをやめる](doeff-agents.md)
- 次の回:[依存を引数で引き回すのをやめる](doeff-di.md)
