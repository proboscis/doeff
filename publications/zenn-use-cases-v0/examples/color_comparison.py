"""同じProgramを、同期・非同期・仮想時間の3つの解釈で動かす。"""

from datetime import timedelta  # 仮想時計が指定した秒数だけ進むことを比較する。

from doeff_core_effects import Ask  # 呼び出し元へ設定取得の依頼を返せるようにする。
from doeff_core_effects.handlers import await_handler, lazy_ask  # 非同期待機と設定取得を解釈する。
from doeff_core_effects.scheduler import scheduled  # 待っているProgramの再開を管理する。
from doeff_time import (  # 待機の依頼と、選択可能な3種類の時計を使う。
    Delay,  # 待ち方を指定せず、経過してほしい秒数を表す。
    GetTime,  # 選んだ時計で現在時刻を読む。
    async_time_handler,  # Delayをasyncioによる実時間の待機にする。
    sim_time_handler,  # Delayを仮想時計の進行にする。
    sync_time_handler,  # Delayを実行スレッドが停止する実時間の待機にする。
)

from doeff import Program, do, run  # 未実行の計算、合成用デコレータ、検証用の実行境界を使う。


@do  # 待機と結果を、呼び出し元がyieldできるProgramにする。
def worker(seconds: float):  # 必要な待機時間を受け取り、待ち方はハンドラへ委ねる。
    yield Delay(seconds)  # 指定した秒数の経過を依頼し、完了すると次の行へ進む。
    return "完了"  # 待機が終わった後に、呼び出し元へ文字列を返す。


@do  # 設定取得と子の待機処理を、ひとつのProgramへ合成する。
def workflow(seconds: float):  # 3種類の時計で共有する処理の順序を表す。
    prefix = yield Ask("result_prefix")  # この実行範囲の設定から「処理結果」を受け取る。
    result = yield worker(seconds)  # 子のProgramを進め、待機後に「完了」を受け取る。
    return f"{prefix}: {result}"  # 設定と子の結果を使い、「処理結果: 完了」を返す。


@do  # 同じworkflowを外から観測する検証用のProgramにする。
def elapsed(seconds: float):  # 選択した時計で、処理の前後の時刻差を返す。
    before = yield GetTime()  # 待機を始める前の時刻を記録する。
    result = yield workflow(seconds)  # 本体を書き換えず、設定取得と待機を実行する。
    after = yield GetTime()  # 待機を終えた後の時刻を読み直す。
    return result, after - before  # 本体の結果と、選んだ時計上の経過時間を返す。


p_workflow: Program[str] = workflow(0.01)  # まだ待機せず、10ミリ秒を待つ処理を値として保持する。


def verify() -> None:  # 同一Programの3通りの実行と、仮想時間の進み方をオフラインで確認する。
    assert isinstance(p_workflow, Program)  # 関数呼び出しの返り値が「完了」の文字列ではないと確認する。
    configured = lazy_ask({"result_prefix": "処理結果"})(p_workflow)  # 共通の設定をハンドラで供給する。
    blocking = run(scheduled(sync_time_handler()(configured)))  # 実行スレッドで10ミリ秒待って結果を得る。
    simulated = run(scheduled(sim_time_handler()(configured)))  # 実時間の待機なしで仮想時計を10ミリ秒進める。
    asynchronous = run(  # asyncioとの橋渡しを取り付け、同じ本体を非同期の時計で動かす。
        scheduled(await_handler()(async_time_handler()(configured)))  # Delayが内部で発行するAwaitを処理する。
    )
    assert blocking == simulated == asynchronous == "処理結果: 完了"  # 3種類の解釈で結果が一致すると確認する。
    measured = lazy_ask({"result_prefix": "処理結果"})(elapsed(10))  # 今度は仮想時間で10秒待つ同じ本体を観測する。
    assert run(scheduled(sim_time_handler()(measured))) == (  # 実時間を10秒待つことなく、時刻差を確かめる。
        "処理結果: 完了",  # 設定取得と子Programの結果が保持されることを期待する。
        timedelta(seconds=10),  # 仮想時計では正確に10秒経過することを期待する。
    )
    print("同期・非同期・仮想時間: 処理結果: 完了、仮想時計の経過: 10秒")  # 検証を通過した値を表示する。


if __name__ == "__main__":  # import時には待機せず、直接起動したときだけ検証する。
    verify()  # 外部通信せず、実時間20ミリ秒程度の待機を含む比較を行う。
