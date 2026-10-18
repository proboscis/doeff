"""マージキューの記事の完全な例: 1件を入れる手順をProgramにし、門の秒の差を仮想の時計で確かめる。"""

import random  # 1日の到着の刻を、決まった種でばらつかせる。
import statistics  # 待ちの中央値を求める。
from dataclasses import dataclass  # 依頼を、書き換えないデータ型で表す。

from doeff_core_effects.scheduler import scheduled  # 仮想の時計を動かす実行の器。
from doeff_time import Delay, GetTime, sim_time_handler  # 待ちと時刻の依頼と、仮想の時計。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲と実行を使う。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Rebase(Effect):  # 「変更をmainの先端へ積み直したい」という依頼。
    change: str  # 積み直す変更の名前。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Gate(Effect):  # 「積み直した木を、門で確かめたい」という依頼。
    files: dict  # 積み直した木(ファイル名から中身を引く)。


@do  # 呼ぶと、1件を入れる手順(Program)ができる。
def land(change):  # 変更を1件、mainへ入れる。
    files = yield Rebase(change)  # mainの先端へ積み直す。衝突したらNoneが返る。
    if files is None:  # 積み直しで衝突したら、
        return "rebase-conflict"  # mainへ入れない。
    if not (yield Gate(files)):  # 門が通さなければ、
        return "gate-red"  # mainへ入れない。
    return "landed"  # 門を通ったので、mainへ入れる(ここでは結果だけを返す)。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def conflict_scan(effect, k):  # 門は、行頭の衝突の印だけを見る。
    if isinstance(effect, Gate):  # 門の依頼には、
        lines = [line for text in effect.files.values() for line in text.splitlines()]  # 木の全部の行。
        marked = any(line.startswith(("<<<<<<< ", ">>>>>>> ")) for line in lines)  # 印の行があるか。
        return (yield Resume(k, not marked))  # 印が無ければTrueを返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def fake_tests(effect, k):  # 前の形の門。ここでは「assert False」を含む木を、落ちるテストと見なす。
    if isinstance(effect, Gate):  # 門の依頼には、
        failing = any("assert False" in text for text in effect.files.values())  # 落ちるテストがあるか。
        return (yield Resume(k, not failing))  # 落ちるテストが無ければTrueを返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def sample_repo(effect, k):  # テスト用に、決めた積み直しの結果を答える。
    if isinstance(effect, Rebase):  # 積み直しの依頼には、
        trees = {"fix-typo": {"README.md": "# Hello\n"},  # 1語を直しただけの変更。
                 "left-marks": {"app.py": "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> fix\n"},  # 印を残した変更。
                 "clash": None,  # 積み直しで衝突する変更。
                 "broken-test": {"test_app.py": "def test_app():\n    assert False\n"}}  # テストが落ちる変更。
        return (yield Resume(k, trees[effect.change]))  # 決めた木を返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def taking(seconds):  # 門の依頼にseconds秒かける、ハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、待ちや委譲を指示する。
    def slow_gate(effect, k):  # 門の依頼だけを横取りする。
        if isinstance(effect, Gate):  # 門の依頼なら、
            yield Delay(seconds)  # 確かめの時間だけ待つ。
        return (yield Pass(effect, k))  # 依頼は、そのまま外側の門へ渡す。
    return slow_gate  # 作ったハンドラを返す。


@do  # 呼ぶと、列を流す手順(Program)ができる。
def queue(arrivals):  # 届いた順に1件ずつ入れ、各件の待ちの秒を返す。
    start, waits = (yield GetTime()), ()  # 始めの時刻と、待ちの記録。
    for at in arrivals:  # 到着の秒(始めからの秒)の順に、
        now = ((yield GetTime()) - start).total_seconds()  # 今が始めから何秒目かを読む。
        if now < at:  # 前の件が済み、次がまだ届いていなければ、
            yield Delay(at - now)  # 届くまで待つ。
        yield land("fix-typo")  # 1件を入れる。門の時間だけ時計が進む。
        waits = (*waits, ((yield GetTime()) - start).total_seconds() - at)  # 届いてからの秒を足した組を作る。
    return list(waits)  # 各件の待ちの秒を返す。


def flow(arrivals, seconds):  # 到着の刻と門の秒を決めて、列を仮想の時計で1回流すため。
    program = with_handlers([conflict_scan, sample_repo, taking(seconds)], queue(arrivals))  # 内側で門に時間をかける。
    return run(scheduled(sim_time_handler()(program)))  # 仮想の時計で、待たずに動かす。


def test_three_changes():  # 冒頭の枠: 1語の修正は入り、印と衝突は止まる。
    handlers = [conflict_scan, sample_repo]  # 衝突の印だけを見る門。
    # 3つの変更を、それぞれ衝突の印だけを見る門で入れてみる。
    results = {change: run(with_handlers(handlers, land(change))) for change in ["fix-typo", "left-marks", "clash"]}
    assert results == {"fix-typo": "landed", "left-marks": "gate-red", "clash": "rebase-conflict"}  # 記事の3行。


def test_swapping_the_gate_does_not_change_land():  # 門をテストの門へ替えても、landは同じまま動く。
    handlers = [fake_tests, sample_repo]  # 前の形の、テストを走らせる門。
    assert run(with_handlers(handlers, land("broken-test"))) == "gate-red"  # 落ちるテストで止まる。
    assert run(with_handlers([conflict_scan, sample_repo], land("broken-test"))) == "landed"  # 衝突の門は通す。


def test_burst_of_eight():  # 2つ目の枠: 10分に8件が届いた時の待ち。
    burst = [i * 75 for i in range(8)]  # 75秒おきに8件。
    assert [round(w) for w in flow(burst, 517)] == [517, 959, 1401, 1843, 2285, 2727, 3169, 3611]  # 積み重なる。
    assert [round(w) for w in flow(burst, 5)] == [5] * 8  # どれも門の5秒だけ。


def test_a_spread_day():  # 完全な例だけの確認: 1日に161件がばらばらに届くと、517秒の門は時間の単位で待たせる。
    rng = random.Random(16)  # 決まった種の乱数。毎回同じ到着になる。
    arrivals = sorted(rng.uniform(0, 86_400) for _ in range(161))  # 1日の中に、161件を散らす。
    slow, fast = flow(arrivals, 517), flow(arrivals, 5)  # 同じ到着で、2つの門を流す。
    assert statistics.median(slow) > 1_800  # 517秒の門は、待ちの中央値が30分を越える(この種では約54分)。
    assert max(slow) > 7_200  # 一番待つ件は、2時間を越える(この種では約2.2時間)。
    assert round(statistics.median(fast)) == 5 and max(fast) < 15  # 5秒の門は、重なっても15秒に届かない。


def verify() -> None:  # 記事のテストと、完全な例だけの確認を順に実行する。
    test_three_changes()  # 冒頭の枠と同じ答えになる。
    test_swapping_the_gate_does_not_change_land()  # 門だけを替えられる。
    test_burst_of_eight()  # 2つ目の枠と同じ答えになる。
    test_a_spread_day()  # ばらばらに届く1日分でも、門の秒の差が出る。
    print("OK: 4つの確かめが通った")  # 成功を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    verify()  # 外部通信なしで、例の振る舞いを確かめる。
