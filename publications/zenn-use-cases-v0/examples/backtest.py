"""バックテストと本番で、同じ処理を時刻・値段・注文のハンドラだけ替えて動かす例。

記事 doeff-backtest.md の5つの枠と同じ定義を持つ。判断の処理は中身の無い置き物で、値段は架空の数。
実際の取引所には繋がない。本番のハンドラは、固定の値を返す偽物のクライアントで確かめる。
"""

import sys  # 実時間で約60秒待つ確かめを、引数で選ぶ。
from bisect import bisect_right  # 時刻の並びから、今以前の最後の記録を探す。
from dataclasses import dataclass  # 依頼を、書き換えない値として定義する。
from datetime import datetime, timedelta, timezone  # 架空の記録の時刻を作る。
from typing import Protocol  # 本番のクライアントに求める形を書く。

import pytest  # 例外が起きることを確かめる。
from doeff_core_effects import Tell  # 「この値を書き残したい」という依頼。
from doeff_core_effects.handlers import (  # 書き残した控えを集める部品。
    state,  # 集めた控えの置き場を持つハンドラ。
    writer,  # Tellの値を集めるハンドラ。
    writer_log,  # 集めた控えを読み出す処理。
)
from doeff_core_effects.scheduler import scheduled  # 待っている処理の再開と時計の進みを受け持つ。
from doeff_time import (  # 時間の依頼と、その答え方を選ぶ。
    Delay,  # 秒数を指定して待つ依頼。
    GetTime,  # 今の時刻を読む依頼。
    sim_time_handler,  # 仮想の時計のハンドラ。
    sync_time_handler,  # OSの時計で、実際に待つハンドラ。
)

from doeff import (  # 依頼・処理・ハンドラを定義し、実行する。
    Effect,  # 自分で定義する依頼の基底の型。
    Pass,  # 担当外の依頼を、外側のハンドラへ渡す。
    Resume,  # 止まった処理へ値を渡して再開する。
    do,  # 関数を、実行前の手順を表すProgramにする。
    handler,  # 関数を、Programに取り付けられるハンドラにする。
    run,  # Programを最後まで動かす。
    with_handlers,  # ハンドラをProgramの外側に取り付ける。
)


@dataclass(frozen=True)  # 依頼の中身を、後から書き換えない。
class GetPrice(Effect):  # 「今の値段を知りたい」という依頼。
    symbol: str  # どの銘柄の値段か。


@dataclass(frozen=True)  # 依頼の中身を、後から書き換えない。
class PlaceOrder(Effect):  # 「この注文を出したい」という依頼。
    symbol: str  # どの銘柄を。
    side: str  # "buy"(買い)か"sell"(売り)か。
    quantity: int  # 何単位か。


def decide(now, price):  # 判断の置き物。この記事では中身を書かない。
    return ("buy", 1)  # 時刻も値段も見ずに、毎回同じ注文を返す。


@do  # 呼んだだけでは動かない処理(Program)にする。
def trading_loop(symbol: str, steps: int):  # 1分ごとに、steps回だけ判断と注文を繰り返す。
    for _ in range(steps):  # 決めた回数だけ回す。
        now = yield GetTime()  # 今の時刻を、外側に頼んで受け取る。
        price = yield GetPrice(symbol)  # 今の値段を、外側に頼んで受け取る。
        side, quantity = decide(now, price)  # 置き物の判断に、時刻と値段を渡す。
        receipt = yield PlaceOrder(symbol, side, quantity)  # 注文を頼み、受付の番号を受け取る。
        yield Tell((now.strftime("%H:%M"), price, receipt))  # この回の控えを、外側に書き残す。
        yield Delay(60)  # 次の1分まで待つ。


def backtest_market(history):  # 記録で値段に答え、注文を受け付けるハンドラを作る。
    times = [at for at, _ in history]  # 記録の時刻だけを、古い順に並べる。

    @handler  # Programに取り付けられるハンドラにする。
    def market(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, GetPrice):  # 値段の依頼なら、
            now = yield GetTime()  # 仮想の時計に、今の時刻を問う。
            index = bisect_right(times, now) - 1  # 今以前で最後の記録の位置。
            if index < 0:  # 今より前の記録が無ければ、
                raise LookupError(f"{now}より前の記録がありません")  # 値を作らずに止める。
            return (yield Resume(k, history[index][1]))  # その記録の値段で、処理を再開する。
        if isinstance(effect, PlaceOrder):  # 注文の依頼なら、
            now = yield GetTime()  # 注文を受けた時刻を問う。
            return (yield Resume(k, f"bt-{now:%H%M}"))  # 送らずに、時刻から作った受付の番号を返す。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return market  # 作ったハンドラを返す。


@do  # 補助の処理も、yieldで組み立てる。
def with_log(program):  # 処理を動かし終えてから、書き残した控えを読む。
    yield program  # 処理を最後まで動かす。
    return (yield writer_log())  # writerが集めた控えの一覧を受け取る。


START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)  # 架空の記録の始まり。


def backtest(history, steps, start=START):  # 記録と仮想の時計で、trading_loopを回す。
    handlers = [state(), writer, backtest_market(history)]  # 控えの置き場・集め役・値段と注文。
    program = with_handlers(handlers, with_log(trading_loop("DEMO", steps)))  # 処理に付ける。
    return run(scheduled(sim_time_handler(start_time=start)(program)))  # startから仮想の時計で回す。


history = [(START + timedelta(minutes=i), 100.0 + i) for i in range(3)]  # 架空の値段100・101・102。


def test_three_minutes():  # 3回分を仮想の時計で回し、記事の表示と同じ控えになる。
    assert backtest(history, 3) == [  # 記事の表示と同じ3回分の控え。
        ("09:00", 100.0, "bt-0900"),  # 1回目は09:00の記録100.0を読んだ。
        ("09:01", 101.0, "bt-0901"),  # 2回目は09:01の記録101.0を読んだ。
        ("09:02", 102.0, "bt-0902"),  # 3回目は09:02の記録102.0を読んだ。
    ]


def test_no_price_from_the_future():  # 30秒後に変わる値段を、今の時点で読めない。
    later = [(START, 100.0), (START + timedelta(seconds=30), 999.0)]  # 30秒後に値段が変わる記録。
    assert backtest(later, 1) == [("09:00", 100.0, "bt-0900")]  # 09:00には、まだ999.0を読めない。


def test_no_record_before_start():  # 記録より前の時刻では、値段を作らずに止まる。
    early = START - timedelta(minutes=1)  # 記録の1分前から時計を始める。
    with pytest.raises(LookupError):  # 値段が無いと、例外で分かる。
        backtest(history, 1, start=early)  # 08:59に値段を問う。


class Broker(Protocol):  # 本番の取引所のクライアントに求める2つの操作。
    def price(self, symbol: str) -> float: ...  # 今の値段を返す。
    def order(self, symbol: str, side: str, quantity: int) -> str: ...  # 注文を送り、受付の番号を返す。


def live_market(broker: Broker):  # 本番のクライアントで答えるハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    def market(effect, k):  # バックテストのハンドラと、同じ依頼を受け持つ。
        if isinstance(effect, GetPrice):  # 値段の依頼なら、
            return (yield Resume(k, broker.price(effect.symbol)))  # 取引所に問うた値段を返す。
        if isinstance(effect, PlaceOrder):  # 注文の依頼なら、
            receipt = broker.order(effect.symbol, effect.side, effect.quantity)  # 取引所へ送る。
            return (yield Resume(k, receipt))  # 取引所の受付の番号を返す。
        return (yield Pass(effect, k))  # 時刻と待ちは、外側のOSの時計へ渡す。

    return market  # 作ったハンドラを返す。


def run_live(broker: Broker, steps: int):  # 本番では、OSの時計と取引所のクライアントを付ける。
    handlers = [state(), writer, live_market(broker)]  # 値段と注文の答え方だけが違う。
    program = with_handlers(handlers, with_log(trading_loop("DEMO", steps)))  # 同じ処理を使う。
    return run(scheduled(sync_time_handler()(program)))  # 1分ごとに、実際に待ちながら動く。


class FakeBroker:  # 取引所に繋がず、固定の値を返す偽物のクライアント。
    def price(self, symbol: str) -> float:  # 値段を問われたら、
        return 100.0  # いつも架空の値段100.0を返す。

    def order(self, symbol: str, side: str, quantity: int) -> str:  # 注文を受けたら、
        return "受付A" if (symbol, side, quantity) == ("DEMO", "buy", 1) else "想定外"  # 中身を確かめる。


def test_live_handler_on_virtual_time():  # 本番のハンドラも、偽物と仮想の時計なら待たずに試せる。
    handlers = [state(), writer, live_market(FakeBroker())]  # 本番のハンドラを、偽物で動かす。
    program = with_handlers(handlers, with_log(trading_loop("DEMO", 2)))  # 2回だけ回す。
    log = run(scheduled(sim_time_handler(start_time=START)(program)))  # 時計だけ仮想にする。
    assert log == [("09:00", 100.0, "受付A"), ("09:01", 100.0, "受付A")]  # 注文は偽物へ届いた。


def test_one_week_without_waiting():  # 1週間分の1分ごとの処理を、1週間待たずに回す。
    minutes = 7 * 24 * 60  # 1週間は10,080分。
    week = [(START + timedelta(minutes=i), 100.0) for i in range(minutes)]  # 架空の値段を毎分置く。
    log = backtest(week, minutes)  # 同じ処理を、仮想の時計で回す。
    assert len(log) == minutes  # 毎分1回、10,080回の注文を受け付けた。
    assert log[-1] == ("08:59", 100.0, "bt-0859")  # 最後の回は、7日後の09:00の1分前。


if __name__ == "__main__":  # ファイルを直接実行した時だけ、確かめを走らせる。
    test_three_minutes()  # 3回分のバックテスト。
    test_no_price_from_the_future()  # 未来の値段を読めない。
    test_no_record_before_start()  # 記録より前では止まる。
    test_live_handler_on_virtual_time()  # 本番のハンドラを、偽物と仮想の時計で試す。
    test_one_week_without_waiting()  # 1週間分を待たずに回す。
    if "--real-time" in sys.argv:  # 指定した時だけ、OSの時計で約60秒待つ。
        print(run_live(FakeBroker(), 1))  # 例: [('17:38', 100.0, '受付A')]。時刻は走らせた時のUTC。
    print("OK: バックテスト・未来の値段・記録の前・本番のハンドラ・1週間分")  # 全部通った事を示す。
