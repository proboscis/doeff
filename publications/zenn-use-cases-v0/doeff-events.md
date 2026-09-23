---
title: "ポーリングのループを書かずに、イベントを待つ — doeff-events"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

注文が完了した、ファイルの準備ができた、別の処理が状態を更新した。通知を受け取るまでの待機も、処理の一部として書けます。

```python
from dataclasses import dataclass  # 通知に含める値を、不変のデータにする。
from doeff import do, run  # 計算を組み立てる@doと、検証時の実行関数を使う。
from doeff_core_effects.scheduler import Spawn, Wait, scheduled  # 待ち受けを子タスクとして動かす。
from doeff_events import Publish, WaitForEvent, event_handler  # 通知と購読をハンドラで解釈する。

@dataclass(frozen=True)  # 受信後も同じ値を指すイベントにする。
class Ready:  # 準備完了を、他の通知と区別できる型にする。
    message: str  # 受信側へ渡す本文。今回は「準備完了」。

@do  # 待ち受けも、合成できるProgramとして定義する。
def receive_ready():  # Ready型の次の通知を受け取り、本文を返す。
    event = yield WaitForEvent(Ready)  # 通知まで停止し、届くとReadyの値を受け取る。
    return event.message  # 通知から取り出した「準備完了」を呼び出し元へ返す。

@do  # 発行側と待ち受けを、同じProgramの中で組み合わせる。
def workflow():  # この例では「準備完了」を結果として返す。
    listener = yield Spawn(receive_ready())  # 現行の標準優先度では、子が先に受信待機へ進む。
    yield Publish(Ready("準備完了"))  # 待機中のReadyの購読者へ通知し、発行側にはNoneが返る。
    return (yield Wait(listener))  # 子タスクの結果「準備完了」を受け取る。

# この1行は動作確認の実行境界。イベントの状態を作り、スケジューラを設置する。
assert run(scheduled(event_handler()(workflow()))) == "準備完了"  # 通知が受信側を再開することを確認。
```

型を指定してイベントを待ち、発行側はその型の値を`Publish`します。`receive_ready()`は待ち受けを含むProgramなので、`Spawn`で起動している間も発行側を進められます。受信ループを`async def`で包む必要はありません。

現行スケジューラは、同じ優先度なら子タスクを親の再開より先に並べます。この短い例では、子が`WaitForEvent`で購読を登録した後に、親が発行します。これは`Spawn`一般の「購読完了を保証するAPI」という意味ではありません。優先度や購読前の処理を変える場合は、登録と発行の順序を改めて設計します。


![型のあるイベントで、処理をつなぐ](/images/zenn-use-cases-v0/generated/events-concept.png)

待機を繰り返しポーリングする代わりに、イベントが届いたら続きを動かします。


## 時間とイベントを、同じ計算で扱う

イベントが来るまで待つ、時刻になったらイベントを発行する、別の処理の完了を待つ。`doeff-time`とコアのスケジューラを組み合わせて書けます。

このページでは、`Race`と`Cancel`による期限の管理、Promiseによる準備完了の共有、セマフォによる同時実行数の制限まで、下の例で組み合わせます。

## 永続的なメッセージキューとは区別する

現在の`event_handler`はメモリ上の発行・購読です。待ち受け前に発行したイベントを必ず後から受け取れる履歴や、プロセスをまたぐ配送保証はありません。イベントの再生や外部メッセージ基盤を使うなら、その保証を持つ別のハンドラが必要です。

## 待ち受けを繰り返し、完了か期限で終了する

`PageReady`で文書のタイトルを受け取り、`PagesFinished`でループを終えます。`WaitForEvent`は複数の型を指定できるので、「次のタイトルか終了通知」を1か所で待てます。

`Race`は先に完了したタスクの値を返します。消費側が3秒で終了する場合と、0.5秒の期限が先に来る場合を、同じProgramで確認します。

```python
from dataclasses import dataclass  # イベントの値を、不変のデータとして表す。

from doeff_core_effects.scheduler import (  # 待機中の処理を同じスケジューラで調整する。
    Cancel,  # この例が起動したタスクを、不要になった時点で止める。
    Race,  # 消費側と期限のうち、先に完了した値を受け取る。
    Spawn,  # @doのProgramを子タスクとして開始し、Taskを受け取る。
    scheduled,  # Taskの開始・待機・取り消しを解釈する実行境界。
)
from doeff_events import (  # 発行と型を指定した受信を、エフェクトで表す。
    Publish,  # 現在待っている購読者へイベントを渡す。
    WaitForEvent,  # 指定した型の次のイベントまで続きを止める。
    event_handler,  # メモリ上の購読状態を持つハンドラを作る。
)
from doeff_time import Delay, sim_time_handler  # 待ち時間を、実時間を使わず解釈する。

from doeff import do, run  # @doで計算を組み立て、検証の境界でだけ実行する。


@dataclass(frozen=True)  # 受信後に値を書き換えないイベントにする。
class PageReady:  # 読み取り対象の文書が用意できたことを表す。
    title: str  # 消費側が結果へ蓄積するタイトル。


@dataclass(frozen=True)  # 終了の通知も、独立したイベント型で表す。
class PagesFinished:  # この通知を受け取ったら反復受信を終える。
    pass  # 終了を表す型だけで十分なので、追加の値は持たない。


@do  # 反復受信の全体を、他の@doからyieldできるProgramにする。
def read_until_end():  # 終了通知まで集めたタイトルのタプルを返す。
    titles = ()  # 初期値は空。最終的には2件のタイトルになる。
    while True:  # 1件受け取るたびに、次の待ち受けを登録する。
        event = yield WaitForEvent(PageReady, PagesFinished)  # どちらかの型が届くまで停止。
        if isinstance(event, PagesFinished):  # 終了通知なら、もう次の受信は始めない。
            return titles  # 正常完了時は("はじめに", "遊び方")を返す。
        titles = (*titles, event.title)  # 届いたタイトルを順番どおりに加える。


@do  # イベントの発行と間隔も、同じProgramの中に記述する。
def produce_pages():  # 仮想時刻1・2秒でタイトル、3秒で終了を通知する。
    for title in ("はじめに", "遊び方"):  # この順番で2件を発行する。
        yield Delay(1)  # 消費側が待ち受けを登録してから、1秒後に進む。
        yield Publish(PageReady(title))  # 現在の購読者へ、このタイトルを配る。
    yield Delay(1)  # 最後のタイトルの受信後、終了通知を待てる間隔を置く。
    yield Publish(PagesFinished())  # 消費側にタイトル収集の終了を知らせる。


@do  # 期限を、結果を返す小さなProgramとして合成する。
def deadline(seconds: float):  # 指定秒数後に、期限切れを表す値を返す。
    yield Delay(seconds)  # 時間の進め方は、設置した時間ハンドラに委ねる。
    return "期限切れ"  # Raceで期限側が先に終わった場合の結果になる。


@do  # 受信・発行・期限を、async defへ移さずに合成する。
def collect_with_deadline(seconds: float):  # 正常なタイトル列か期限切れの値を返す。
    consumer = yield Spawn(read_until_end())  # 先に消費側の待ち受けを開始する。
    producer = yield Spawn(produce_pages())  # 最初の発行は1秒後。consumerのTaskとは別に進む。
    timer = yield Spawn(deadline(seconds))  # 消費側と競わせる期限のTaskを作る。
    try:  # Raceの成功時も失敗時も、この関数が持つタスクを後始末する。
        return (yield Race(consumer, timer))  # 先に完了した側の値を、そのまま返す。
    finally:  # Race自体は、遅い側のタスクを自動で取り消さない。
        yield Cancel(producer)  # 残った発行を止める。完了済みなら何もしない。
        yield Cancel(consumer)  # 期限切れなら、残ったイベント待機を止める。
        yield Cancel(timer)  # 正常完了なら、不要になった期限待機を止める。


def verify() -> None:  # 実時間や外部サービスを使わず、両方の結果を確認する。
    cases = [(5, ("はじめに", "遊び方")), (0.5, "期限切れ")]  # 3秒で完了する場合と期限が先の場合。
    for seconds, expected in cases:  # 毎回ハンドラを新しく作り、購読状態を共有しない。
        program = event_handler()(collect_with_deadline(seconds))  # 発行・購読を同じハンドラで解釈。
        result = run(scheduled(sim_time_handler()(program)))  # スケジューラ上で仮想時間を進める。
        assert result == expected  # 2件のタイトル、または0.5秒で期限切れになることを確かめる。

verify()  # 正常完了と期限切れの両方を、外部接続なしで確認する。
```

この例の供給側はイベントの間に1秒の待機を置き、受信側が次の購読を登録できるようにしています。`Publish`は、その時点の購読者への通知です。すべてのイベントを必ず蓄積するキューの保証はありません。

`Race`は残りのタスクを自動で取り消さないため、起動した側が`finally`で3つのTaskを`Cancel`します。この例は外部資源を持ちません。外部接続の解放まで必要な場合、そのハンドラと取り消しの契約を別途確認します。

[完全な例](examples/event_loop.py)も保存しています。

## 準備完了・同時実行制限・外からの完了通知を合わせる

Promiseで準備完了を共有し、Semaphoreで同時に変換する数を制限し、Gatherで結果を集めます。準備が終わったことを記憶するPromiseは、購読の前に発行されたイベントを蓄積しない`event_handler`と役割が違います。

`ExternalPromise`の例は、外部スレッドとスケジューラを接続する境界の書き方です。完了通知はthread-safeな`complete()`から渡し、処理側は同じ`Wait`で値を受け取ります。スレッドの起動自体をエフェクトへ抽象化した例ではありません。アプリケーションでこの接続を使うときは、対応するドメインエフェクトのハンドラに置けます。

```python
"""準備完了・同時実行制限・外部通知を、同じWaitで扱う例。"""

from threading import Thread  # 外部スレッドから通知する境界だけに使う。

from doeff_core_effects.scheduler import (  # Task・Future・セマフォを同じ実行境界で解釈する。
    AcquireSemaphore,  # 許可を1つ得るまで、処理の続きを止める。
    CompletePromise,  # スケジューラ内から準備完了の値を設定する。
    CreateExternalPromise,  # 外部スレッドから完了通知できるハンドルを作る。
    CreatePromise,  # 内部で完了させるPromiseと、読取側のFutureを作る。
    CreateSemaphore,  # 同時実行の許可数を持つセマフォを作る。
    Gather,  # 複数のTaskの結果を、引数の順に集める。
    ReleaseSemaphore,  # 得た許可を返し、次の待機者を進める。
    Spawn,  # ProgramをTaskとして起動する。
    Wait,  # TaskとFutureのどちらの完了も待てる。
    scheduled,  # 上記の待機と再開を解釈する。
)
from doeff_time import Delay, GetTime, sim_time_handler  # 仮想時間を進め、経過秒数を測る。

from doeff import do, run  # 処理を合成し、検証時だけ実行する。


@do  # 準備待機から変換の終了までを、1つのProgramにする。
def convert(name, ready, limit):  # 入力名・共有のFuture・セマフォを受け取る。
    yield Wait(ready)  # 準備完了まで停止する。完了済みなら直ちに進む。
    yield AcquireSemaphore(limit)  # 同時に1件だけ変換へ入れるようにする。
    try:  # 変換が正常終了しても例外になっても、許可を返す範囲を明示する。
        yield Delay(2)  # 変換に2秒かかる状況を、時間エフェクトで表す。
        return name  # この例の変換結果は、その入力名のまま。
    finally:  # この例の通常の制御フローで、取得した許可を返す。
        yield ReleaseSemaphore(limit)  # 次のTaskが変換を始められるようにする。


@do  # 準備完了と2つの変換を、同じ計算の中で調整する。
def batch():  # 2件の結果と、処理全体の経過秒数を返す。
    start = yield GetTime()  # 同じ時間ハンドラから開始日時を受け取る。
    ready = yield CreatePromise()  # まだ完了していない、共有の準備通知を作る。
    limit = yield CreateSemaphore(permits=1)  # 変換へ入れるTaskは、同時に1つとする。
    tasks = [  # Gatherへ渡すTaskを、結果を受け取りたい順に並べる。
        (yield Spawn(convert("前半", ready.future, limit))),  # 準備を待つ最初のTaskを起動する。
        (yield Spawn(convert("後半", ready.future, limit))),  # 同じ準備と許可を共有するTaskを起動する。
    ]
    yield CompletePromise(ready, None)  # 準備を1回だけ完了させ、両TaskのWaitを再開可能にする。
    results = yield Gather(*tasks)  # 両方の完了を待ち、["前半", "後半"]を得る。
    end = yield GetTime()  # 2秒の変換を2回直列に進めた後の日時を受け取る。
    return results, (end - start).total_seconds()  # 結果と4.0秒を返す。


@do  # この関数は、外部スレッドとの接続境界を示す小さな例。
def external_result():  # スレッドの完了通知を、通常のWaitの結果へ変換する。
    completion = yield CreateExternalPromise()  # 外部から完了させられる書込側を作る。
    future = completion.future  # Futureの生成は、スケジューラ側のスレッドで行う。
    worker = Thread(target=completion.complete, args=("入力を受信",))  # thread-safeな完了操作だけ渡す。
    worker.start()  # 外部スレッドが「入力を受信」を通知する。
    result = yield Wait(future)  # 通知を待つ間は、他のTaskを進められる。
    worker.join()  # 通知を出したローカルスレッドの終了を回収する。
    return result  # Futureへ渡された「入力を受信」を返す。


def verify():  # 外部ネットワークを使わず、値と時間の両方を確認する。
    results, seconds = run(scheduled(sim_time_handler()(batch())))  # 実時間を待たずに2件を処理。
    assert results == ["前半", "後半"]  # 完了時刻順ではなく、Gatherの入力順の結果を確認。
    assert seconds == 4  # permits=1により2秒×2件となることを確認。
    assert run(scheduled(external_result())) == "入力を受信"  # 外部通知もWaitで受け取れることを確認。


if __name__ == "__main__":  # ファイルを検証として実行した場合だけ起動する。
    verify()  # 共有通知・同時実行制限・外部完了通知を確認する。
```

[共有通知と同時実行制限の完全な例](examples/scheduler_coordination.py)も保存しています。

## 処理の流れ

![待ち受けを登録してから、イベントを発行する](/images/zenn-use-cases-v0/generated/events-flow.png)

組み込みハンドラは永続キューではありません。購読前のイベントを後から受け取る保証はありません。


## 実装・実例を読む

- [イベントと待機の実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-events/src/doeff_events/handlers/memory.py)
- [イベントの型](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-events/src/doeff_events/effects/events.py)
- [時間・イベントの合成テスト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-time/tests/test_sim_migration_readiness.py)
- [スケジューラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/scheduler.py)

この草稿は上記の開発版を参照しています。このページの例は、メモリ上の通知・仮想時間・ローカルスレッドだけで実行確認しています。

[時間の扱いを変える](doeff-time.md) / [ハンドラの合成](doeff-handlers.md) / [coroutineとの違い](doeff-coroutines.md)

[メイン記事へ戻る](doeff-main.md)
