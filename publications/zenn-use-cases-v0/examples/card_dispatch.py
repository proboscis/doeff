"""配る判断の記事の完全な例: 配る順を純粋な関数にし、記録だけの運転と1日の模擬で確かめる。"""

import contextlib  # 記録だけの運転の表示を受け取る。
import io  # 表示を文字列として集める。
from collections import Counter  # 優先度ごとに、配った数を数える。
from dataclasses import dataclass  # cardと依頼を、書き換えないデータ型で表す。

from doeff_core_effects import Ask, Try  # 設定の値の依頼と、失敗を値で受ける依頼。
from doeff_core_effects.handlers import reader, try_handler  # 設定の答えと、失敗の包み。

from doeff import Effect, Err, Pass, Resume, do, handler, run, with_handlers  # 依頼と実行。

PRIORITIES = ("p0", "p1", "p2", "p3")  # 急ぐ順の優先度。p0が一番急ぐ。
WEIGHTS = {"p0": 16, "p1": 8, "p2": 4, "p3": 2}  # 優先度ごとの重み。配る数の比になる。
MIX = ("p0", "p1", "p1", "p2", "p2", "p2", "p2", "p2", "p3", "p3")  # 合成の負荷の優先度の割合(10枚ごと)。


@dataclass(frozen=True)  # 書き換えないデータ型にする。
class ReadyCard:  # 配れるcard 1枚。
    key: str  # cardの鍵。
    priority: str  # 優先度(p0〜p3)。
    ready_at: int  # 配れるようになった時刻(分)。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadReady(Effect):  # 「配れるcardの一覧を読みたい」という依頼。
    pass  # 引数は持たない。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadFree(Effect):  # 「新しく配れる数(空き)を読みたい」という依頼。
    pass  # 引数は持たない。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Dispatch(Effect):  # 「このcardを、新しい会話に頼みたい」という依頼。
    key: str  # 頼むcardの鍵。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class AwaitChange(Effect):  # 「盤面が変わるまで待ちたい」という依頼。
    pass  # 引数は持たない。


def strict(ready, free, memo):  # C 厳密な優先: 急ぐ順、同じ優先度は古い順。
    ordered = sorted(ready, key=lambda card: (PRIORITIES.index(card.priority), card.ready_at))  # 急ぐ順に並べる。
    return tuple(card.key for card in ordered[:free]), memo  # 空きの数だけ選び、持ち越す値はそのまま返す。


def fifo(ready, free, memo):  # E 先着順: 配れるようになった古い順だけ。
    ordered = sorted(ready, key=lambda card: card.ready_at)  # 古い順に並べる。
    return tuple(card.key for card in ordered[:free]), memo  # 空きの数だけ選ぶ。


def wfq(ready, free, memo):  # D 重みつきの公平な分配。
    turn, clock = memo.get("turn", {}), memo.get("clock", 0.0)  # 優先度ごとの次の番と、今の仮想の刻。
    waiting, picks = tuple(sorted(ready, key=lambda card: card.ready_at)), ()  # 同じ優先度の中は古い順に取る。
    while waiting and len(picks) < free:  # 空きがある間、1枚ずつ取る。
        # 番が一番早い優先度を選ぶ。番の刻が同じなら、急ぐ方を先にする。
        start, _, chosen = min((max(turn.get(c.priority, 0.0), clock), PRIORITIES.index(c.priority), c.priority) for c in waiting)
        card = next(c for c in waiting if c.priority == chosen)  # その優先度の一番古いcard。
        waiting, picks = tuple(c for c in waiting if c != card), (*picks, card.key)  # 待ちから外し、配るcardに足す。
        turn, clock = {**turn, chosen: start + 1 / WEIGHTS[chosen]}, start  # 次の番を、重みの逆数だけ先へ進める。
    return picks, {"turn": turn, "clock": clock}  # 選んだcardと、次の周期へ持ち越す値。


@do  # 呼ぶと、1周期の配りの手順(Program)ができる。
def tick(memo):  # 前の周期から持ち越した値を受け取る。
    strategy = yield Ask("strategy")  # 配る順の関数を、設定から受け取る。
    ready = yield ReadReady()  # 配れるcardを読む。
    free = yield ReadFree()  # 空きを読む。
    picks, memo = strategy(ready, free, memo)  # 配る順の関数に選ばせる。
    keys = {card.key for card in ready}  # 選んでよいcardの鍵。
    if len(picks) > free or len(set(picks)) < len(picks) or not set(picks) <= keys:  # 約束の外の答えなら、
        raise ValueError(f"配る順の答えが約束の外です: {picks}")  # 黙って配らず、止める。
    for key in picks:  # 選んだcardを、
        yield Dispatch(key)  # 1枚ずつ頼む。
    return memo  # 次の周期へ持ち越す値を返す。


@do  # 本番と同じ周回: 盤面が変わるたびに、1周期を配る。
def serve():  # 止める合図が来るまで回る。
    memo = {}  # 配る順の関数が持ち越す値。
    while (yield AwaitChange()):  # 変わったら回し、止める合図(False)で抜ける。
        memo = yield tick(memo)  # 1周期を配る。


def board_handler(ready, free):  # テスト用に、決めた盤面と空きを答えるハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def fixed_board(effect, k):  # 決めた盤面と空きを答える。
        if isinstance(effect, ReadReady):  # 配れるcardの依頼には、
            return (yield Resume(k, ready))  # 決めた一覧を返す。
        if isinstance(effect, ReadFree):  # 空きの依頼には、
            return (yield Resume(k, free))  # 決めた空きを返す。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return fixed_board  # 作ったハンドラを返す。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def dry_run(effect, k):  # 記録だけの運転: 会話を起こさず、配るはずだったcardを表示する。
    if isinstance(effect, Dispatch):  # 頼む依頼だけを受け持つ。
        print("配るはずだった:", effect.key)  # 本番のハンドラなら、ここで会話を開く。
        return (yield Resume(k, None))  # 処理の続きを動かす。
    return (yield Pass(effect, k))  # 読みの依頼は、外側のハンドラへ渡す。


@do  # 1周期の失敗を、値として受け取る手順にする。
def tried_tick():  # 約束の外の答えで止まったかを、例外を外へ漏らさずに調べる。
    return (yield Try(tick({})))  # 成功ならOk、止まったらErrを返す。


def simulate(strategy, cards, ceiling, minutes=1440):  # 1日の負荷を、仮想の1分刻みで回す。
    world = {"now": -1, "running": {}, "waits": {}}  # 世界の値: 今の分・走っている会話・cardごとの待ち。

    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def world_handler(effect, k):  # 配りの依頼に、世界の値で答える。
        if isinstance(effect, AwaitChange):  # 待ちの依頼には、時計を1分進め、
            world["now"] += 1  # 仮想の時計を進める。
            world["running"] = {key: end for key, end in world["running"].items() if end > world["now"]}  # 終わった会話を外す。
            return (yield Resume(k, world["now"] < minutes))  # 1日が終わったら止める合図を返す。
        if isinstance(effect, ReadReady):  # 配れるcardの依頼には、
            ready = tuple(ReadyCard(key, p, at) for key, p, at, _ in cards if at <= world["now"] and key not in world["waits"])  # 着いていて、まだ配っていないcard。
            return (yield Resume(k, ready))  # 一覧を返す。
        if isinstance(effect, ReadFree):  # 空きは、上限から走っている会話の数を引いた数。
            return (yield Resume(k, ceiling - len(world["running"])))  # 空きを返す。
        if isinstance(effect, Dispatch):  # 頼む依頼には、
            key, p, at, work = next(card for card in cards if card[0] == effect.key)  # 頼まれたcardを引く。
            world["waits"][key] = (p, world["now"] - at)  # 優先度と待った分を書く。
            world["running"][key] = world["now"] + work  # 仕事が終わる分を書く。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    run(reader(env={"strategy": strategy})(with_handlers([world_handler], serve())))  # 本番と同じserveを回す。
    waits = world["waits"]  # 配ったcardの待ち。
    left = [minutes - at for key, _, at, _ in cards if key not in waits]  # 配られずに1日を終えたcardの待ち。
    p0 = max(wait for p, wait in waits.values() if p == "p0")  # 急ぎのcardの最長の待ち。
    return len(waits), p0, max([wait for _, wait in waits.values()] + left)  # 配った枚数・急ぎの最長・全体の最長。


READY = (ReadyCard("A", "p2", 0), ReadyCard("B", "p0", 5), ReadyCard("C", "p3", 1), ReadyCard("D", "p1", 3))  # 記事の冒頭の4枚。
MANY = tuple(ReadyCard(f"U{i}", "p0", i) for i in range(10)) + (ReadyCard("L1", "p3", 0), ReadyCard("L2", "p3", 1))  # 急ぎ10枚と低い2枚。
CARDS = [(f"K{i}", MIX[i % 10], (i * 7) % 1440, 20 + (i * 53) % 160) for i in range(400)]  # 記事の合成の負荷400枚。


def dry_run_lines(strategy, ready, free):  # 記録だけの運転で1周期を回し、表示した行を返す。
    out = io.StringIO()  # 表示を集める。
    program = with_handlers([board_handler(ready, free), dry_run], tick({}))  # 盤面を外側、記録を内側に付ける。
    with contextlib.redirect_stdout(out):  # printの表示を受け取る。
        run(reader(env={"strategy": strategy})(program))  # 配る順を設定から渡して回す。
    return out.getvalue().splitlines()  # 表示した行を返す。


def test_strategies_on_four_cards() -> None:  # 冒頭の枠と同じ答えになる。
    assert strict(READY, 2, {})[0] == ("B", "D")  # 急ぐ順にp0とp1。
    assert fifo(READY, 2, {})[0] == ("A", "C")  # 古い順にAとC。


def test_dry_run_dispatches_nothing_real() -> None:  # 記録だけの運転は、配るはずだったcardを書くだけ。
    assert dry_run_lines(strict, READY, 2) == ["配るはずだった: B", "配るはずだった: D"]  # 2枚を書く。
    assert dry_run_lines(strict, READY, 0) == []  # 空きが0なら何も書かない。


def test_broken_strategy_is_stopped() -> None:  # 約束の外の答えは、黙って配らずに止まる。
    broken = (  # わざと壊した配る順の関数。
        lambda _ready, _free, memo: (("A", "B", "C"), memo),  # 空き2を越えて3枚を選ぶ。
        lambda _ready, _free, memo: (("A", "A"), memo),  # 同じcardを2度選ぶ。
        lambda _ready, _free, memo: (("Z",), memo),  # 配れないcardを選ぶ。
    )
    for strategy in broken:  # 壊し方ごとに、1周期を回す。
        program = with_handlers([board_handler(READY, 2), dry_run], tried_tick())  # 記録だけの運転で回す。
        out = io.StringIO()  # 配るはずだったcardの表示を集める。
        with contextlib.redirect_stdout(out):  # printの表示を受け取る。
            outcome = run(try_handler(reader(env={"strategy": strategy})(program)))  # 失敗をErrに包む。
        assert isinstance(outcome, Err), outcome  # 1周期が失敗として止まる。
        assert "約束の外" in str(outcome.error), outcome.error  # 止まった訳は、約束の外の答え。
        assert out.getvalue() == ""  # 止まる前に、1枚も配っていない。


def test_wfq_shares_follow_weights() -> None:  # Dは、配る数の比が重みの比になる。
    memo, picked = {}, ()  # 持ち越す値と、選んだcardの優先度の列。
    for round_ in range(30):  # 空き1の周期を30回回す。
        ready = tuple(ReadyCard(f"{p}-{round_}", p, round_) for p in PRIORITIES)  # 各優先度に1枚ずつ。
        picks, memo = wfq(ready, 1, memo)  # 1枚を選ぶ。
        picked = (*picked, picks[0].split("-")[0])  # 選んだ優先度を足した組を作る。
    assert Counter(picked) == {"p0": 16, "p1": 8, "p2": 4, "p3": 2}, picked  # 16:8:4:2の比になる。
    assert wfq(MANY, 9, {})[0].count("L1") == 1  # 記事の枠: 9枚のうち1枚を低いcardに回す。
    assert "L1" not in strict(MANY, 9, {})[0]  # Cは急ぎだけで埋める。


def test_simulated_day() -> None:  # 記事の模擬の枠と同じ値になる。
    assert simulate(strict, CARDS, ceiling=26) == (372, 12, 612)  # Cは急ぎが速く、低いcardが長く待つ。
    assert simulate(wfq, CARDS, ceiling=26) == (374, 12, 237)  # Dは急ぎも全体も短い。
    assert simulate(fifo, CARDS, ceiling=26) == (375, 131, 135)  # Eは急ぎも長く待つ。


def verify() -> None:  # 記事のテストと、完全な例だけの確認を順に実行する。
    test_strategies_on_four_cards()  # 冒頭の枠と同じ答えになる。
    test_dry_run_dispatches_nothing_real()  # 記録だけの運転は書くだけ。
    test_broken_strategy_is_stopped()  # 壊した配る順は止まる。
    test_wfq_shares_follow_weights()  # Dの配る数は重みの比になる。
    test_simulated_day()  # 1日の模擬が記事と同じ値になる。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    verify()  # 外部通信なしで、配る順・記録だけの運転・模擬を確認する。
    print("配る順・約束の確かめ・記録だけの運転・重みの比・1日の模擬: OK")  # 全確認後に表示する。
