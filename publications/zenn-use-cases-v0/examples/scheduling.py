"""並行待機、指定時刻、優先度と、実時間ハンドラへの切り替えを確認する。"""

from datetime import datetime, timedelta, timezone  # UTC日時と秒単位の時刻差を作る。

from doeff_core_effects.handlers import await_handler  # 非同期待機をスケジューラへ接続する。
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
    GetTime,  # 選んだ時計の現在日時を取得する。
    ScheduleAt,  # 指定時刻に別のProgramを開始する。
    SetTime,  # 仮想時計の日時を設定する。
    WaitUntil,  # 指定日時に達するまで待機する。
    async_time_handler,  # asyncioによる実時間の非同期待機を選ぶ。
    sim_time_handler,  # 待機予定へ時刻を進める仮想時計を選ぶ。
    sync_time_handler,  # 実行スレッドを止める実時間の待機を選ぶ。
)

from doeff import do, run  # 処理をProgramにし、検証の入口で実行する。


@do  # 秒数だけを受け取る処理を、時計から独立したProgramにする。
def job(seconds: float):  # 呼び出しただけでは待機を始めない。
    yield Delay(seconds)  # 選んだ時間ハンドラが待機を終えるまで中断する。
    return "完了"  # 待機が終わると、この文字列を呼び出し元へ返す。


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


def verify() -> None:  # テストの入口だけでProgramをrunし、観測結果を比較する。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 並行例の開始日時を固定する。
    elapsed = run(scheduled(sim_time_handler(start_time=start)(workflow())))  # 仮想時計で走らせる。
    assert elapsed == [20.0, 10.0]  # 結果順と各タスクの再開日時を確認する。
    events: list[str] = []  # 指定時刻の予定だけを観測する空のリスト。
    end = run(scheduled(sim_time_handler()(timeline(events))))  # 3秒と8秒の予定を処理する。
    assert end == start + timedelta(seconds=8)  # 最後の再開時刻が8秒後であることを確認する。
    assert events == ["予定"]  # 登録した予定が1回だけ動いたことを確認する。
    priority_events: list[str] = []  # 優先度の例は別のリストで観測する。
    run(scheduled(priorities(priority_events)))  # 時間ハンドラを使わず、タスクの順序を検証する。
    assert priority_events == ["応答処理", "背景処理"]  # 高優先度が先に実行されたことを確認する。
    simulated = run(scheduled(sim_time_handler()(job(0.002))))  # 同じjobを仮想時間で実行する。
    asynchronous = run(  # 実時間の確認には2ミリ秒だけ待つjobを使う。
        scheduled(await_handler()(async_time_handler()(job(0.002))))  # 非同期の待機を処理する。
    )
    synchronous = run(sync_time_handler()(job(0.002)))  # 単独の待機を同期ハンドラで処理する。
    assert simulated == asynchronous == synchronous == "完了"  # 時計を変えても戻り値が同じ。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    verify()  # 外部通信なしで、仮想時間・実時間・優先度を確認する。
    print("並行待機・時刻設定・予定登録・優先度・時間ハンドラ切り替え: OK")  # 全確認後に表示する。
