"""並行待機、指定時刻、優先度と、実時間ハンドラへの切り替えを確認する。"""

from datetime import datetime, timedelta, timezone  # UTC日時と秒単位の時刻差を作る。

from doeff_core_effects.effects import Tell  # 実行した処理の名前を記録として送る。
from doeff_core_effects.handlers import (  # 待機と記録を受け持つハンドラを選ぶ。
    await_handler,  # 非同期待機をスケジューラへ接続する。
    state,  # 集めた記録の置き場を持つ。
    writer,  # Tellで送られた記録を集める。
    writer_log,  # 集めた記録を、処理の途中でも読む。
)
from doeff_core_effects.scheduler import (  # 並行タスクの開始・待機・優先度を指定する。
    PRIORITY_HIGH,  # 実行可能な通常タスクより先に選ばれる優先度。
    PRIORITY_IDLE,  # 通常・高優先度の実行可能タスクがなくなってから選ばれる優先度。
    Gather,  # 複数タスクの結果を指定順で集める。
    Spawn,  # Programを別タスクとして開始し、Taskを返す。
    Wait,  # Taskの終了を待ち、その結果または例外を受け取る。
    scheduled,  # タスクの実行と再開を管理する。
)
from doeff_time import (  # 時間への依頼と、その解釈を選べるようにする。
    Delay,  # 秒数を指定して待機する。
    DelayEffect,  # Delay(...)が作る依頼の型。
    GetTime,  # 選んだ時計の現在日時を取得する。
    ScheduleAt,  # 指定時刻に別のProgramを開始する。
    WaitUntil,  # 指定日時に達するまで待機する。
    async_time_handler,  # asyncioによる実時間の非同期待機を選ぶ。
    sim_time_handler,  # 待機予定へ時刻を進める仮想時計を選ぶ。
    sync_time_handler,  # 実行スレッドを止める実時間の待機を選ぶ。
)

from doeff import (  # 処理とハンドラを定義し、実行する。
    Pass,  # 担当外の依頼を外側のハンドラへ渡す。
    Resume,  # 止まった処理へ値を渡して再開する。
    do,  # 関数を、実行前の計算を表すProgramにする。
    handler,  # 関数を、Programに取り付けられるハンドラにする。
    run,  # 検証の入口で、Programを最後まで動かす。
    with_handlers,  # ハンドラをProgramの外側に取り付ける。
)


@do  # 呼んだだけでは動かない処理(Program)にする。
def job(seconds: float):  # 待つ秒数だけを受け取る。
    yield Delay(seconds)  # 「seconds秒待ちたい」と外側へ依頼する。
    return "完了"  # 待ちが終わると、この文字列を返す。


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


@do  # 優先度の例で、実行された順序を記録する。
def record_label(label: str):  # 表示名だけを受け取る。
    yield Tell(label)  # 実行した時点で、ラベルを記録として送る。
    return label  # Waitでこのラベルを受け取れる。


@do  # 優先度は時間の設定と別に、スケジューラへの依頼として指定する。
def priorities():  # 実行可能な2タスクがどちらから選ばれるかを記録する。
    background = yield Spawn(  # 背景処理を登録するが、通常優先度の親処理は先へ進める。
        record_label("背景処理"), priority=PRIORITY_IDLE  # 実行を後回しにする。
    )
    urgent = yield Spawn(  # 高優先度の応答処理を追加する。
        record_label("応答処理"), priority=PRIORITY_HIGH  # 背景処理より先に選ばれる。
    )
    yield Wait(urgent)  # 応答処理の完了を確認する。
    yield Wait(background)  # 背景処理も完了させ、未完了タスクを残さない。
    return (yield writer_log())  # 記録された実行順を返す。


def recorded(program):  # 記録を集めるwriterと、その置き場のstateを外側に付ける。
    return state()(writer(program))  # 実行ごとに空の記録から始まる。


def test_job_finishes_without_waiting():  # 記事の最初のテスト。
    program = sim_time_handler()(job(20))  # 仮想時計のハンドラを外側に付ける。
    assert run(scheduled(program)) == "完了"  # 20秒待たずに、すぐ終わる。


@handler  # この関数を、Programに取り付けられるハンドラにする。
@do  # ハンドラ自身もyieldで、再開や委譲を指示する。
def skip_delay(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, DelayEffect):  # 待ちの依頼だけを受け持つ。
        return (yield Resume(k, None))  # 待たずに、すぐ続きを再開する。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def test_job_with_own_handler():  # 自作のハンドラでも、同じjobが終わる。
    assert run(with_handlers([skip_delay], job(20))) == "完了"  # 時計なしで、すぐ終わる。


def test_workflow_in_virtual_time():  # 20秒の流れを、待たずに確かめる。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 時計の開始を00:00:00に固定する。
    program = sim_time_handler(start_time=start)(workflow())  # 仮想時計を取り付ける。
    assert run(scheduled(program)) == [20.0, 10.0]  # 20秒後と10秒後に再開した。


def test_schedule_at_8_seconds():  # 3秒と8秒の流れを、待たずに確かめる。
    program = sim_time_handler()(timeline())  # 仮想時計を取り付ける。
    assert run(scheduled(program)) == ["予定", 3.0, 8.0]  # 3秒で一度戻り、8秒で予定が終わる。


def test_priority_order():  # 時間ハンドラを使わず、タスクの選ばれる順を確かめる。
    order = run(recorded(scheduled(priorities())))  # 2つのタスクを優先度つきで動かす。
    assert order == ["応答処理", "背景処理"]  # 高優先度が先に実行された。


def test_same_job_on_three_clocks():  # 時計を替えても、処理本体と戻り値は同じ。
    simulated = run(scheduled(sim_time_handler()(job(0.002))))  # 同じjobを仮想時間で実行する。
    asynchronous = run(  # 実時間の確認には2ミリ秒だけ待つjobを使う。
        scheduled(await_handler()(async_time_handler()(job(0.002))))  # 非同期の待機を処理する。
    )
    synchronous = run(sync_time_handler()(job(0.002)))  # 単独の待機を同期ハンドラで処理する。
    assert simulated == asynchronous == synchronous == "完了"  # 時計を変えても戻り値が同じ。


def verify() -> None:  # 記事のテストと、完全な例だけの確認を順に実行する。
    test_job_finishes_without_waiting()  # 仮想時計で20秒の待ちがすぐ終わる。
    test_job_with_own_handler()  # 自作ハンドラが待ちの依頼を受けて、すぐ再開する。
    test_workflow_in_virtual_time()  # 20秒と10秒の並行待機が[20.0, 10.0]になる。
    test_schedule_at_8_seconds()  # 3秒の時点と8秒の予定を確認する。
    test_priority_order()  # 高優先度のタスクが先に選ばれる。
    test_same_job_on_three_clocks()  # 仮想時間・非同期・同期の3つで同じ戻り値になる。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    verify()  # 外部通信なしで、仮想時間・実時間・優先度を確認する。
    print("並行待機・指定時刻・優先度・自作ハンドラ・時間ハンドラ切り替え: OK")  # 全確認後に表示する。
