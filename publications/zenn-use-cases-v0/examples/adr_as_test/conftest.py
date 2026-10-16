import pytest  # fixtureを定義する。
from doeff import run  # Programを最後まで動かす。
from doeff_core_effects.scheduler import scheduled  # 時計のハンドラが要る、タスクの管理を付ける。


@pytest.fixture  # deftestの関数が受け取るfixtureにする。
def doeff_interpreter():  # 名前はdoeff-adrが決めている。
    def run_program(program, *, env=None):  # deftestはProgramを渡す。envはここでは使わない。
        return run(scheduled(program))  # 時計はADRの中で付ける。ここはタスクの管理だけを付ける。

    return run_program  # 動かす関数を返す。
