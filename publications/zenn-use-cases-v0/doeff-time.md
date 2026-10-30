---
title: "テストで20秒待つのをやめる"
emoji: "⏱️"
type: "tech"
topics: ["python", "doeff", "テスト", "asyncio"]
published: false
---

20秒待ってから次へ進む処理は、テストでも20秒止まります。この記事では、本番と同じ処理のまま、仮想の時計でテストを20秒待たずに終わらせます。待ちを含む処理のテストには、よく次の悩みがあります。

- `time.sleep(20)`を含む処理は、テストも20秒かかります。
- `sleep`をモックで消すと速くなりますが、2つの待ちのどちらが先に終わるかは試せません。
- 今の時刻を読む処理は、テストを走らせた時刻で結果が変わります。

Pythonのライブラリdoeffを使い、この3つをまとめて解きます。doeffは、処理の中の「待つ」「読む」といった操作を、外側の部品で差し替えられるライブラリです。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード

「待つ」を処理の外側への依頼にして、テストでは仮想の時計に答えさせます。

```python
from doeff import do, run  # 処理の定義と、その実行を使う。
from doeff_core_effects.scheduler import scheduled  # タスクの再開と時計の進行を管理する。
from doeff_time import Delay, sim_time_handler  # 待ちの依頼と、仮想時計のハンドラを使う。


@do  # 呼んだだけでは動かない処理（Program）にする。
def job(seconds: float):  # 待つ秒数だけを受け取る。
    yield Delay(seconds)  # 「seconds秒待ちたい」と外側へ依頼する。
    return "完了"  # 待ちが終わると、この文字列を返す。


def test_job_finishes_without_waiting():  # pytestが集めるテスト関数。
    program = sim_time_handler()(job(20))  # 仮想時計のハンドラを外側に付ける。
    assert run(scheduled(program)) == "完了"  # 20秒待たずに、すぐ終わる。
```

`job(20)`は「20秒待ちたい」と頼むだけで、自分では待ちません。テストでは仮想時計が答えるので、時計を20秒進めてすぐ続きを動かします。

![同じjobに、時計を選んで付ける](/images/zenn-use-cases-v0/generated/time-concept.png)
*処理本体は`Delay(20)`を頼むだけです。外側のハンドラが、仮想時計を進めるか、実際に待つかを決めます。*

## 「待つ」を外側への依頼にする

コードに出てきた語を、先に整理します。

- `@do`を付けた関数は、呼んでもすぐには動きません。実行する手順を表す`Program`を返します。
- `yield Delay(20)`は、「20秒待ちたい」という依頼を外側へ渡します。
- このような依頼をエフェクト（effect）と呼びます。
- 依頼を受け、続きをいつ・どう動かすかを決める部品を、ハンドラ（handler）と呼びます。
- `run`は、ハンドラを付けた`Program`を最後まで動かします。
- `scheduled`は、待っているタスクの再開と、仮想時計の進行を受け持ちます。

`job`自身は、`time.sleep`も`asyncio.sleep`も呼びません。そのため、どの時計で待つかを外側で選べます。

| 処理の内側が書くこと | 外側のハンドラが決めること |
| --- | --- |
| 20秒待ちたい | 実際に待つか、仮想時計を進めるか |
| 今の時刻を知りたい | OSの時計を読むか、仮想時計を読むか |
| 別の処理を始め、結果を待ちたい | どのタスクを、いつ再開するか |

処理の手順と、その動かし方を分けるわけです。

### ハンドラの中身：待ちを飛ばすだけのハンドラ

ハンドラは自分でも書けます。次のハンドラは待ちの依頼を受けると、待たずにすぐ続きを動かします。ここからのコードは前のコードの続きで、`job`もそのまま使います。

```python
from doeff import Pass, Resume, handler, with_handlers  # 依頼の委譲・再開・取り付けを使う。
from doeff_time import DelayEffect  # Delay(...)が作る依頼の型。


@handler  # この関数を、Programに取り付けられるハンドラにする。
def skip_delay(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, DelayEffect):  # 待ちの依頼だけを受け持つ。
        return (yield Resume(k, None))  # 待たずに、すぐ続きを再開する。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def test_job_with_own_handler():  # 自作のハンドラでも、同じjobが終わる。
    assert run(with_handlers([skip_delay], job(20))) == "完了"  # 時計なしで、すぐ終わる。
```

- `isinstance`で、届いた依頼が待ちかどうかを見分けます。
- 待ちの依頼は、頼まれた秒数を`effect.seconds`に持っています。
- `Resume(k, None)`は、止まっていた`job`へ`None`を渡して再開します。
- 待ち以外の依頼は、`Pass`で外側のハンドラへ渡します。
- `with_handlers`は、ハンドラを`Program`の外側に取り付けます。

ただし、このハンドラは時計を持ちません。待ちが2つあると、どちらが先に終わるかを試せません。ここからは、doeff-timeの仮想時計を使います。

## 見せ場：20秒と10秒の待ちを、仮想時間で並べて動かす

20秒待つ処理と10秒待つ処理を同時に始めます。それぞれが、開始から何秒後に再開したかを調べます。

```python
from datetime import datetime, timezone  # 仮想時計の開始日時をUTCで固定する。

from doeff_core_effects.scheduler import Gather, Spawn  # タスクを始め、結果を集める。
from doeff_time import GetTime  # 選んだ時計の、今の日時を読む。


@do  # 待ってから、再開した日時を返す。
def worker(seconds: float):  # 20または10を受け取る。
    yield Delay(seconds)  # 仮想時間なら、時計が指定秒数進むまで止まる。
    return (yield GetTime())  # 再開した時点の日時を返す。


@do  # 2つの待ちを、通常のyieldで組み立てる。
def workflow():  # 実時間でも仮想時間でも、同じ処理を使う。
    start = yield GetTime()  # 経過秒数の基準になる開始日時を読む。
    slow = yield Spawn(worker(20))  # 20秒待つ処理を始め、Taskを受け取る。
    fast = yield Spawn(worker(10))  # 10秒待つ処理も始める。
    finished = yield Gather(slow, fast)  # slow、fastの順で日時を受け取る。
    return [(t - start).total_seconds() for t in finished]  # 開始からの秒数へ直す。


def test_workflow_in_virtual_time():  # 20秒の流れを、待たずに確かめる。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 時計の開始を00:00:00に固定する。
    program = sim_time_handler(start_time=start)(workflow())  # 仮想時計を取り付ける。
    assert run(scheduled(program)) == [20.0, 10.0]  # 20秒後と10秒後に再開した。
```

- `Spawn`は、処理を別のタスクとして始め、その`Task`を返します。
- `Gather`は、渡した順に結果を返します。終わった順ではありません。
- 開始日時を固定するので、`GetTime`の値は走らせる時刻によりません。

![仮想時計は、次の予定の時刻へ飛ぶ](/images/zenn-use-cases-v0/generated/time-flow.png)
*2つのタスクが0秒で待ち始めます。時計は10秒、20秒の順に飛び、Gatherは`[20.0, 10.0]`を返します。*

仮想時計は、動けるタスクが無くなった時だけ、次の予定の時刻まで進みます。時計を進める係は、低い優先度のタスクとして動きます。動ける処理が残っている間に、時計だけが先へ進むことはありません。

**「何を返すか」に加えて、「いつ続きを動かすか」もハンドラが決めます。** 先ほどの自作のハンドラとの違いは、ここにあります。

2026年10月16日に、この記事のテスト関数4つをpytestで走らせました。4つとも通りました。pytestが測った関数の実行時間は、4つ合わせて約0.2秒でした。ライブラリを読み込む時間は、この数に含みません。

## 実時間で動かす時は、外側だけを替える

本番で実際に待つ時も、`worker`と`workflow`はそのまま使います。

```python
from doeff_core_effects.handlers import await_handler  # 非同期の待ちを、スケジューラへ繋ぐ。
from doeff_time import async_time_handler  # asyncioで実際に待つハンドラ。

program = async_time_handler()(workflow())  # 同じworkflowに、実時間の時計を付ける。
result = run(scheduled(await_handler()(program)))  # 今度は実際に約20秒待つ。
print(result)  # おおむね[20.0, 10.0]。起床の遅れで、わずかにずれる。
```

- `async_time_handler`は、内部で`asyncio`を使って実際に待ちます。
- `await_handler`は、その非同期の待ちをdoeffのスケジューラへ繋ぎます。

2026年10月16日に走らせると、表示は`[20.091931, 10.058906]`でした。全体で約20秒かかりました。

処理本体には、`async def`も`asyncio.sleep`もありません。`asyncio`をやめる話ではありません。**`asyncio`への依存を、ハンドラの側に寄せる**という話です。スレッドを止めて待つ`sync_time_handler`もあり、3つの時計から選べます。

## 指定時刻の処理と、優先度も同じ書き方で

指定した日時に処理を始めることもできます。次の例は8秒後の予定を登録し、先に3秒後まで待ちます。

```python
from datetime import timedelta  # 開始日時からの秒数の差を作る。

from doeff_core_effects.scheduler import Wait  # 予定したタスクの終わりを待つ。
from doeff_time import ScheduleAt, WaitUntil  # 日時を指定して、始めたり待ったりする。


@do  # 時計を使わない、予定の本文。
def alarm_body():  # 予定の本文は、時間ハンドラの外側で動く。
    return "予定"  # Waitで受け取る値。


@do  # 絶対日時で予定を組み立てる。
def timeline():  # 3秒の時点と、8秒の予定を並べる。
    start = yield GetTime()  # 開始日時を読む。
    alarm = yield ScheduleAt(start + timedelta(seconds=8), alarm_body())  # 8秒後の予定を登録する。
    yield WaitUntil(start + timedelta(seconds=3))  # 3秒後まで待つ。
    middle = yield GetTime()  # 3秒の時点の日時を読む。
    label = yield Wait(alarm)  # 予定が終わるまで待ち、その値を受け取る。
    end = yield GetTime()  # 予定が終わった日時を読む。
    return [label, (middle - start).total_seconds(), (end - start).total_seconds()]  # 秒数へ直す。


def test_schedule_at_8_seconds():  # 3秒と8秒の流れを、待たずに確かめる。
    program = sim_time_handler()(timeline())  # 仮想時計を取り付ける。
    assert run(scheduled(program)) == ["予定", 3.0, 8.0]  # 3秒で一度戻り、8秒で予定が終わる。
```

- `ScheduleAt`は、指定した日時に処理を始める予定を登録し、`Task`を返します。
- `WaitUntil`は、指定した日時になるまで待ちます。
- `Wait`は、予定の処理が終わるのを待ち、その戻り値を受け取ります。

予定の本文は、今の実装では時間ハンドラの外側で動きます。本文の中で`GetTime`を使うと、受け手のいない依頼になります。本文で時計が要る時は、ハンドラを付ける範囲を明示します。

仮想時計の日時は、`start_time`の引数か`SetTime`で決めます。`SetTime`を扱うのは仮想時計だけです。実時間のハンドラで、OSの時計を変えることはできません。

優先度も、タスクを始める時の引数で書きます。`Spawn(処理, priority=TaskPriority.HIGH)`のように渡すと、動ける時に先に選ばれます。動いているCPU処理を、途中で止める仕組みではありません。優先度の例は、[完全な例](examples/scheduling.py)に入れています。

## まとめ

- 「待つ」を`yield Delay(...)`の依頼にすると、どの時計で待つかを外側で選べます。
- テストでは仮想時計のハンドラを付け、20秒の流れを一瞬で確かめます。
- 開始日時を固定するので、時刻を読む処理も毎回同じ結果になります。
- 本番では実時間のハンドラに替えるだけで、処理本体は変えません。
- 指定時刻の処理と優先度も、同じ`yield`の書き方で書けます。

時間以外の依頼との組み合わせは、[ハンドラの合成](doeff-handlers.md)で扱います。`await`との違いは[coroutineとの違い](doeff-coroutines.md)、ゲームの進行と組み合わせる例は[カードゲームの記事](doeff-games.md)にあります。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain（commit `9adc4f8`）でも、同じく動きました。`doeff`・`doeff-core-effects`・`doeff-time`の3つのパッケージが要ります。

doeff-timeはPyPIに無いので、GitHubから入れます。`--no-sources`を付けないと、doeff本体もGitHubから入り、Rustのビルドが要ります。完全な例を`scheduling.py`として保存し、次のコマンドで実行します。

```bash
uv run --no-sources --with "doeff==0.6.0" --with "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time" python scheduling.py  # doeff本体はPyPI、doeff-timeだけGitHubから入れて、完全な例を実行する。
```

- [この記事のコードをまとめた完全な例](examples/scheduling.py)
- [doeff本体（この記事で確かめた版）](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c)
- [実時間・仮想時間のハンドラ](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-time)
- [タスクと優先度を扱うスケジューラ](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/scheduler.py)
- [ハンドラの能力と関数の色の整理](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/docs/22-capability-classes.md)
- 前の回:[Claude Codeを起動してテストするのをやめる](doeff-agents.md)
- 次の回:[依存を引数で引き回すのをやめる](doeff-di.md)
