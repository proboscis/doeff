"""Python の呼び出しの数え(``sys.monitoring`` の CALL 事象)— 印 ``--doeff-python-calls`` を付けた走りだけ、歩数を宣言した検の
call の間に数える(agora-redesign #2854・card ki-a4042e38352a の受入条 c4c2e04dc・親の設計 #2670 の §2)。

何のためにあるか: doeff-vm の歩数は検の筋書きの大きさで決まり、Python 側の無駄(例: #2593 の isinstance 18 万回)は歩数に出ない。
呼び出しの数はそれを日々の推移で見せる。数は同じ検で 0.4% 以内に再現する(CPU 秒は同じ歩数で ±14% 揺れる)。

設計の決め(依頼書 lt-HXGQN32BB70Z8PY7W8ZV0KQHQ8 の確定した決定):

- 数える物は CALL 事象(builtin の呼びも数える — PY_START だけだと isinstance のような呼びが見えない)。cProfile は使わない
  (検ごとに区切れず、thread を跨げない)。数は CALL 事象の数と定義し、cProfile の ``total_calls`` とは比べない。
- 範囲は各検の call の間だけ(収集・setup・teardown は数えない)。call の前に ``set_events`` で入れ、後に 0 にする(入り切り 1 回
  約 40 マイクロ秒)。
- 対象は歩数を宣言した検(``declare_step_budget``)だけ。監視の上乗せ(素の 1.5〜2.3 倍)を全部の検に乗せると日次の pytest の段が
  伸びて回の上限に近づくため。
- 印の無い走り(既定)は ``sys.monitoring`` の枠を 1 つも取らず、``set_events`` を 1 度も呼ばない — 毎回の検の CPU は変わらない。
- 枠は 4 → 5 → 3 の順に空きを取る(3 は doeff-hy の ``doeff-hy-called-code`` が使いうる・0〜2 は debugger・coverage・profiler の
  名の枠なので取らない)。全部埋まっていれば数えず、終わりに「測れない」の 1 行を出して走りは落とさない。
- 数えは ``itertools.count`` の ``next``(原子的な加算)。free-threaded の Python では callback の ``+= 1`` が複数 thread で数を
  6 割近く落とす(4 thread × 20 万回で 345,383)。

純粋な部分(枠の選び方・文の整形)と ``sys.monitoring`` に触る部分(:class:`CallCounter`)を分ける。pytest の hook は
``doeff_hy_pytest.budget`` に在る(歩数の口と検の宣言を同じ所で読むため)。
"""

import itertools
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# pytest の命令行の印(既定は無し)— 日次の全体検証の 1 日 1 回の回だけが付ける。
PYTHON_CALLS_OPTION = "--doeff-python-calls"
PYTHON_CALLS_DEST = "doeff_python_calls"
TOOL_NAME = "doeff-hy-pytest-python-calls"
# 空きを探す枠の順。0・1・2(debugger・coverage・profiler)は取らない。
TOOL_CANDIDATES = (4, 5, 3)
SUMMARY_HEAD = "Python の呼び出し(sys.monitoring CALL)"
UNMEASURABLE_HEAD = "python-calls: 測れない"
# 終わりの 1 行に名指す検の数(呼び出しの多い順)。
TOP_TESTS = 3


@dataclass(frozen=True)
class FreeTool:
    """取れる枠の番号。"""

    tool_id: int


@dataclass(frozen=True)
class NotCounted:
    """数えない(理由つき — 枠が全部使用中・歩数を測れない・上限の設定が無い)。印を付けた走りの終わりに理由を 1 行出す。"""

    reason: str


def choose_tool(owner_of: Callable[[int], str | None]) -> FreeTool | NotCounted:
    """空いた枠を候補の順に選ぶため(owner_of = 枠の持ち主の名・空きは None — ``sys.monitoring.get_tool`` の形)。"""
    for candidate in TOOL_CANDIDATES:
        if owner_of(candidate) is None:
            return FreeTool(candidate)
    taken = "・".join(f"{candidate}={owner_of(candidate)}" for candidate in TOOL_CANDIDATES)
    return NotCounted(f"sys.monitoring の枠が全部使用中: {taken}")


@dataclass(frozen=True)
class CalledTest:
    """数えた検 1 本 — call の間の Python の呼び出しの数と doeff-vm の歩数。"""

    key: str
    calls: int
    steps: int


def per_step_text(calls: int, steps: int) -> str:
    """1 歩あたりの呼び出しの数の書き方(歩数 0 は割らずに — と書く)。"""
    return f"{calls / steps:.1f}" if steps else "—"


def summary_line(tests: Sequence[CalledTest]) -> str:
    """終わりの要約の 1 行(session ごと)— 日次の報告が全部の session を束ねて ``python-calls:`` の 1 行にする。"""
    calls = sum(test.calls for test in tests)
    steps = sum(test.steps for test in tests)
    top = sorted(tests, key=lambda test: test.calls, reverse=True)[:TOP_TESTS]
    top_text = "・".join(f"{test.key} {test.calls}" for test in top) or "なし"
    return (
        f"{SUMMARY_HEAD}: 歩数を宣言した検 {len(tests)} 本で {calls} 回・歩数 {steps}・"
        f"1 歩あたり {per_step_text(calls, steps)} 回・多い順(上位 {TOP_TESTS}): {top_text}"
    )


def called_test_line(test: CalledTest) -> str:
    """``-v`` の時の検ごとの 1 行。"""
    return f"  {test.key}: {test.calls} 回・歩数 {test.steps}・1 歩あたり {per_step_text(test.calls, test.steps)} 回"


def unmeasurable_line(not_counted: NotCounted) -> str:
    """印を付けたのに数えなかった走りの 1 行(黙らない — 走りは落とさない)。"""
    return f"{UNMEASURABLE_HEAD}({not_counted.reason})"


class CallCounter:
    """取った枠 1 つで CALL 事象を数える。:meth:`start` から :meth:`stop` までの間だけ事象を入れる。"""

    def __init__(self, tool_id: int) -> None:
        self.tool_id = tool_id
        self._mut_tally = itertools.count()

    @classmethod
    def acquire(cls) -> "CallCounter | NotCounted":
        """空いた枠を取り、callback を置く(事象はまだ入れない)。取れなければ理由を返す。"""
        if sys.version_info < (3, 12):
            return NotCounted(f"sys.monitoring が無い(Python {sys.version_info[0]}.{sys.version_info[1]})")
        monitoring = sys.monitoring
        match choose_tool(monitoring.get_tool):
            case NotCounted() as not_counted:
                return not_counted
            case FreeTool(tool_id=tool_id):
                try:
                    monitoring.use_tool_id(tool_id, TOOL_NAME)
                except ValueError as exc:  # 選んだ後に別の道具が先に取った
                    return NotCounted(f"sys.monitoring の枠 {tool_id} を取れない: {exc}")
                counter = cls(tool_id)
                monitoring.register_callback(tool_id, monitoring.events.CALL, counter._called)
                return counter

    def _called(self, code: object, offset: int, callable_: object, arg0: object) -> None:
        """CALL 事象 1 回 — 原子的に 1 足す(free-threaded でも数を落とさない)。"""
        next(self._mut_tally)

    def start(self) -> None:
        """数えを 0 から始め、事象を入れる(検の call の直前)。"""
        self._mut_tally = itertools.count()
        sys.monitoring.set_events(self.tool_id, sys.monitoring.events.CALL)

    def stop(self) -> int:
        """事象を外し、start からの呼び出しの数を返す(検の call の直後)。"""
        sys.monitoring.set_events(self.tool_id, 0)
        return next(self._mut_tally)

    def release(self) -> None:
        """枠を返す(session の終わり)。"""
        sys.monitoring.set_events(self.tool_id, 0)
        sys.monitoring.register_callback(self.tool_id, sys.monitoring.events.CALL, None)
        sys.monitoring.free_tool_id(self.tool_id)


CallCounting = CallCounter | NotCounted
