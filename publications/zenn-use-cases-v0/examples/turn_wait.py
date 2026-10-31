# ruff: noqa: E402, I001  # 本文の枠と同じ順に、importを枠ごとに置く。
"""記事「止まったClaude Codeを1つずつ開くのをやめた」の完全な例。本文のPythonの枠3つを、この順のまま1つのファイルにした。

checks/turn-wait-check.pyが、本文の枠を上から順に走らせてtest関数を呼ぶ。このファイルは同じ枠を単独で走らせる。
本文の最初の枠との違いは、標準入力を読む条件に引数`--hook`を足した1行だけ。引数なしなら試験を順に呼ぶ。

    python turn_wait.py                                   # 2つの試験を走らせる。
    echo '<Stopの出来事のJSON>' | python turn_wait.py --hook   # Claude CodeのStop hookと同じ入出力で1回答える。
"""

import itertools  # 最後の行から上へ、WAITの行が続く間だけ読む。
import json  # hookに届く出来事のJSONを読み、答えをJSONで書く。
import re  # 返事の行が、WAITの行の形かを見る。
import sys  # 標準入力から出来事を読む。

WAIT_LINE = re.compile(r"^WAIT:\s*(user:(?:decide|review|discuss)|work)\s*[—-]\s*(.+)$")  # 待ちの1行の形。区切りは「—」か「-」。
GUIDE = ("止まる前に、何を待っているかを最後の行に書いてください。\n"  # 止めた時に、AIへ返す文。
         "WAIT: user:decide — <あなたに決めてほしい事>\n"  # 人の判断を待つ時の書き方。
         "WAIT: work — <待っているCIや、別の会話の名>")  # CIや別の会話を待つ時の書き方。


def waits_of(reply):  # 返事の末尾に続くWAITの行を、上から順の組にする。
    matches = (WAIT_LINE.match(line.strip()) for line in reversed(reply.strip().splitlines()))  # 最後の行から上へ見る。
    tail = list(itertools.takewhile(bool, matches))  # WAITでない行に着いたら止める。本文の途中の行は拾わない。
    return [found.groups() for found in reversed(tail)]  # 例: [("user:decide", "AかBか")]


def on_stop(event):  # Stopの出来事1つから、止まらせてよいかを決める。
    if event.get("stop_hook_active"):  # このhookで1度止めた後の停止なら、
        return None  # 必ず通す。止め続けると、ターンが終われなくなる。
    if waits_of(event.get("last_assistant_message", "")):  # 最後の行にWAITがあれば、
        return None  # 何も返さず、止まらせる。
    return {"decision": "block", "reason": GUIDE}  # 無ければ1度だけ止め、書き方を教える。


CASES = [  # 返事の全文と、読めるべき行の組。
    ("直しました。\n\nWAIT: work — CIの結果", [("work", "CIの結果")]),  # 最後の1行を読む。
    ("済みました。\nWAIT: user:decide — AかBか\nWAIT: work — 別の会話の返事",  # 末尾に続く2行は、
     [("user:decide", "AかBか"), ("work", "別の会話の返事")]),  # 上から順に2つ読む。
    ("前は WAIT: user:decide — AかB で終えた。\n今は続きを進めています。", []),  # 本文の途中の行は読まない。
    ("見てください。\nWAIT: user — 出来た物", []),  # 種類の無いuserは、行と読まない。
    ("", []),  # 空の返事も、行が無いと読む。
]


def test_read_and_stop():  # 読み方と止め方を、同じ表で確かめる。
    for reply, expected in CASES:  # 例を1つずつ見る。
        assert waits_of(reply) == expected, reply  # 読めた行が、表と同じ。
        first = on_stop({"stop_hook_active": False, "last_assistant_message": reply})  # 1度目の停止。
        assert (first is None) == bool(expected), reply  # 行が無い時だけ止める。
        assert on_stop({"stop_hook_active": True, "last_assistant_message": reply}) is None, reply  # 2度目は通す。


from dataclasses import dataclass  # 依頼を、書き換えない値にする。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行を使う。

PARTY = re.compile(r"@[\w.-]+|(?<![A-Za-z])CI(?![A-Za-z])|(?<!この)会話|利用者|配備|ジョブ")  # 外の相手を名で書いた印。


@dataclass(frozen=True)  # 一度出した依頼を、途中で書き換えない。
class DeclareWait(Effect):  # 「この会話の今の待ちを、この行で置き換えたい」という依頼。
    waits: tuple  # 読めたWAITの行。空なら、待ちの解消。


@dataclass(frozen=True)  # 一度出した依頼を、途中で書き換えない。
class PostContinue(Effect):  # 「同じ会話へ、続きの入力を1通置きたい」という依頼。
    body: str  # 置く入力の本文。


@do  # 呼ぶと、ターンの終わりの手順(Program)ができる。
def close_turn(reply):  # 終わったターンの返事1つを、待ちの申告と続きに変える。
    waits = tuple(waits_of(reply))  # 最後の行を読む。
    yield DeclareWait(waits)  # 会話の今の待ちを、丸ごと置き換える。
    alone = [reason for kind, reason in waits if kind == "work" and not PARTY.search(reason)]  # 相手の無いwork。
    if waits and len(alone) == len(waits):  # 全部が、外の相手を書かないworkなら、
        yield PostContinue("続けて — " + "・".join(alone))  # 人の代わりに「続けて」を置く。
    return waits  # 読めた行を返す。


def fake_board(board, posted, session):  # 試験用: 申告と続きを、メモリに書くハンドラ。
    @handler  # Programに取り付けられるハンドラにする。
    def answer(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, DeclareWait):  # 待ちの申告は、
            board[session] = effect.waits  # 会話ごとの表を置き換える。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        if isinstance(effect, PostContinue):  # 続きの入力は、
            posted[session] = effect.body  # 置いた物として記録する。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。
    return answer  # 表を閉じ込めたハンドラを返す。


def test_sort_and_continue():  # 3つの会話の返事を、あなたの番・外を待つ・続けるに分ける。
    board, posted = {}, {}  # 会話ごとの今の待ちと、置いた続きの入力。
    replies = {"a": "案を2つ書きました。\nWAIT: user:decide — AかBか",  # 筆者の判断を待つ会話。
               "b": "マージの列に載せました。\nWAIT: work — CIの結果",  # CIを待つ会話。
               "c": "単位1を終えました。\nWAIT: work — 単位2をこの会話で続ける"}  # 自分の次の作業を書いた会話。
    for session, reply in replies.items():  # 会話ごとに、ターンの終わりを処理する。
        run(with_handlers([fake_board(board, posted, session)], close_turn(reply)))  # 試験用のハンドラで動かす。
    assert [s for s, waits in board.items() if any(k.startswith("user") for k, _ in waits)] == ["a"]  # 筆者の番はaだけ。
    assert posted == {"c": "続けて — 単位2をこの会話で続ける"}  # 続きはcにだけ置く。


if __name__ == "__main__":  # 引数で、hookの形か試験の形かを選ぶ。
    if "--hook" in sys.argv:  # Claude CodeのStop hookとして呼ばれた時は、
        answer = on_stop(json.load(sys.stdin))  # 標準入力の出来事から決める。
        if answer:  # 止める時だけ書く。
            print(json.dumps(answer, ensure_ascii=False))  # 標準出力のJSONを、Claude Codeが読む。
    else:  # 引数なしなら、試験を順に呼ぶ。
        test_read_and_stop()  # 読み方と止め方の試験。
        test_sort_and_continue()  # 分け方と続きの試験。
        print("OK: 2つの試験が通りました")  # 全部通った事を示す。
