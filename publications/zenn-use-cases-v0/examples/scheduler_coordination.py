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
