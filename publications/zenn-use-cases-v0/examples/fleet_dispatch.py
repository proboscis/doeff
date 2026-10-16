"""開発の記録の完全な例: 配る判断をProgramにし、固定の盤面と仮想時間の模擬で確かめる。"""

from dataclasses import dataclass, replace  # 書き換えない型と、列だけを替えた写し。

from doeff_core_effects import Ask  # 設定の値を、キーで依頼する。
from doeff_core_effects.scheduler import Gather, Spawn, scheduled  # タスクを始め、終わりを待つ。
from doeff_time import Delay, GetTime, sim_time_handler  # 待ちと時刻の依頼と、仮想の時計。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行。


@dataclass(frozen=True)  # 書き換えないデータ型にする。
class Card:  # 作業ボードのcard 1枚。
    key: str  # cardの鍵。
    column: str  # 今の列(todo・doing・done)。
    needs: tuple[str, ...] = ()  # 先に終わるべきcardの鍵。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadBoard(Effect):  # 「作業ボードを読みたい」という依頼。
    pass  # 引数は持たない。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Move(Effect):  # 「cardを別の列へ動かしたい」という依頼。
    key: str  # 動かすcardの鍵。
    column: str  # 動かす先の列。


CARDS = (  # 記事と同じ6枚のcard。
    Card("A", "done"),  # 終わったcard。
    Card("B", "todo", ("A",)),  # Aの後に配れる。
    Card("C", "todo", ("D",)),  # Dが終わるまで配れない。
    Card("D", "todo"),  # すぐ配れる。
    Card("E", "todo"),  # すぐ配れる。
    Card("F", "todo"),  # すぐ配れる。
)
WORK = {"B": 30, "D": 40, "E": 60, "C": 20, "F": 10}  # cardごとの仕事の分数。


@do  # 呼ぶと、配る判断の手順(Program)ができる。
def pick_cards():  # 新しい会話へ配るcardを選ぶ。
    ceiling = yield Ask("ceiling")  # 同時に走らせる会話の上限を読む。
    cards = yield ReadBoard()  # cardの一覧を読む。
    done = {card.key for card in cards if card.column == "done"}  # 終わったcardの鍵。
    running = sum(card.column == "doing" for card in cards)  # 走っている会話の数(ここではdoingの枚数)。
    ready = [card.key for card in cards if card.column == "todo" and set(card.needs) <= done]  # 配れるtodo。
    return ready[: max(0, ceiling - running)]  # 上限までの空きの数だけ選ぶ。


@do  # 会話1つの仕事を、待ちと列の移動で表す。
def conversation(key, minutes):  # 仕事にかかる分数を受け取る。
    yield Delay(minutes * 60)  # 仕事の間、待つ。
    yield Move(key, "done")  # 仕事を終えたcardをdoneへ動かす。


@do  # 配り役の周期を、配るcardが無くなるまで回す。
def dispatcher(work):  # cardの鍵と、仕事の分数の表を受け取る。
    start, log, tasks = (yield GetTime()), (), ()  # 開始の時刻・配った記録・会話のタスク。
    while len(log) < len(work):  # まだ配っていないcardがある間、回す。
        for key in (yield pick_cards()):  # 配れるcardを選ぶ。
            minute = int(((yield GetTime()) - start).total_seconds()) // 60  # 開始から何分目かを読む。
            yield Move(key, "doing")  # 配ったcardをdoingへ動かす。
            task = yield Spawn(conversation(key, work[key]))  # 新しい会話に仕事を始めさせる。
            log, tasks = (*log, (minute, key)), (*tasks, task)  # 配った記録とタスクを足した組を作る。
        yield Delay(60)  # 1分ごとに盤面を読み直す。
    yield Gather(*tasks)  # 全部の会話の仕事が終わるまで待つ。
    end = yield GetTime()  # 全部が終わった時刻を読む。
    return list(log), int((end - start).total_seconds()) // 60  # 配った記録と、かかった分数を返す。


def answer_ceiling(value):  # 上限の依頼だけに決めた値を答える、取り替えられる設定の受け手を作る。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def handle(effect, k):  # 上限の依頼だけを受け持つ。
        if isinstance(effect, Ask) and effect.key == "ceiling":  # 上限の依頼には、
            return (yield Resume(k, value))  # 決めた値を返す。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return handle  # 作ったハンドラを返す。


def memory_board(board, peak):  # 本番の記録のサービスの代わりに、メモリの盤で答える受け手を作る。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def handle(effect, k):  # 届いた依頼に、メモリの盤面で答える。
        if isinstance(effect, ReadBoard):  # 盤面の依頼には、
            return (yield Resume(k, list(board.values())))  # 今の盤面を返す。
        if isinstance(effect, Move):  # 列の移動の依頼には、
            board[effect.key] = replace(board[effect.key], column=effect.column)  # 列を書き換える。
            peak[0] = max(peak[0], sum(card.column == "doing" for card in board.values()))  # 最大を更新する。
            return (yield Resume(k, None))  # 続きを再開する。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return handle  # 作ったハンドラを返す。


def simulate(ceiling, inner=()):  # 上限と内側に差すハンドラを決めて、模擬の配りを1回動かすため。
    board, peak = {card.key: card for card in CARDS}, [0]  # 毎回、記事と同じ盤面から始める。
    handlers = [answer_ceiling(ceiling), memory_board(board, peak), *inner]  # 外側から内側の順(最後が最も内側)。
    program = sim_time_handler()(with_handlers(handlers, dispatcher(WORK)))  # 仮想の時計を外側に付ける。
    return run(scheduled(program)), peak[0]  # 配った記録・かかった分数と、同時の最大を返す。


def test_pick_cards_on_fixed_board():  # 冒頭の枠: 上限3・作業中1で、BとEを選ぶ。
    board = {card.key: card for card in CARDS} | {"D": Card("D", "doing")}  # Dだけを作業中にする。
    handlers = [answer_ceiling(3), memory_board(board, [0])]  # 上限3と盤面を答える。
    assert run(with_handlers(handlers, pick_cards())) == ["B", "E"]  # Cは待ち、Fは空きが無い。


def test_dispatch_in_virtual_time():  # 模擬の枠: 上限2で、90分の流れをすぐ確かめる。
    (log, minutes), peak = simulate(2)  # 上限2で模擬を動かす。
    assert log == [(0, "B"), (0, "D"), (30, "E"), (40, "C"), (60, "F")]  # 配った時刻と順。
    assert (minutes, peak) == (90, 2)  # 90分で終わり、同時の最大は上限の2。


def test_ceiling_one_runs_one_at_a_time():  # 上限1なら、会話は1つずつ走る。
    (_, minutes), peak = simulate(1)  # 上限1で模擬を動かす。
    assert peak == 1  # 同時に走った会話は1つだけ。
    assert minutes >= sum(WORK.values())  # 仕事の分数の和(160分)より短くならない。


def test_broken_handler_is_caught():  # わざと壊したハンドラを差すと、約束の確かめが赤になる。
    broken = answer_ceiling(99)  # 上限を99と読み違える、壊したハンドラ。
    _, peak = simulate(2, inner=[broken])  # 内側に差すので、上限2より先に答える。
    assert peak > 2  # 「同時の最大は上限まで」の約束が破れたことを、確かめが捕まえる。


def verify() -> None:  # 記事のテストと、完全な例だけの確認を順に実行する。
    test_pick_cards_on_fixed_board()  # 冒頭の枠と同じ答えになる。
    test_dispatch_in_virtual_time()  # 模擬の枠と同じ答えになる。
    test_ceiling_one_runs_one_at_a_time()  # 上限1の時は1つずつ走る。
    test_broken_handler_is_caught()  # 壊したハンドラで約束が破れたことを捕まえる。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    verify()  # 外部通信なしで、配る判断と模擬を確認する。
    print("配る判断・仮想時間の模擬・上限の約束・壊したハンドラ: OK")  # 全確認後に表示する。
