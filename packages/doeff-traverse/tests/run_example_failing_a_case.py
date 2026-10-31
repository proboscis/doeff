"""例 1 本を `hy <例>` と同じに走らせ、Traverse の走りごとに最初の件を件の中で失敗させる(test_examples_run.py の子)。

使い方: python run_example_failing_a_case.py <例の path>

件の中の失敗は、Traverse が件の関数で作る Program の中で例外を上げる事で起こす — 例の source の文字列(handler の式・mock の値)
を書き換える変異は、出力を変えない例の中の書き換えでも検の file を直させた。

この入口が依る traverse-lib の約束は 3 つ: Traverse は `Traverse(f, items, label=None)` で作られ、handler は件ごとに f を呼び、
件の Program の中の例外を件の失敗として記録して先へ進む。3 つ目が守られる間、子は 0 で終わり、出力の比べ方だけが失敗を
捕まえる。どれかが変われば子は 0 以外で終わるか印が 0 になり、test_examples_run.py の検が走る例の全部で赤になる
(黙って緑にならない)。その時は、この入口を新しい約束に合わせる。

終わる時に、実行の印 — 件の関数を呼んだ数と、doeff の run で Program を走らせた数 — を標準エラーへ COUNTS_LINE の形で書く。
名ではなく走った数なので、import するだけ・呼ばない関数の中・quote の中・doeff の外の同じ名(asyncio.run・例の中の run)は数えない。
run の数は、run の関数の code の実行の始まりを sys.monitoring(Python 3.12 から)で受けて数える — doeff の名前空間は
書き換えない(例がどの名で import しても同じ code)。
"""

import atexit
import sys
from collections import Counter
from collections.abc import Callable, Generator, Iterable
from types import CodeType
from typing import Any, Never, TypeVar

from doeff_traverse import Traverse

import doeff
from doeff import Program, do

Item = TypeVar("Item")
Answer = TypeVar("Answer")

# 実行の印を書く行(test_examples_run.py の _COUNTS が読む)。
COUNTS_LINE = "case-calls: {case} program-runs: {program}"

# 実行の印(case = 件の関数を呼んだ数・program = doeff の run を呼んだ数)。
_calls: Counter[str] = Counter()

# run の実行の始まりを受ける sys.monitoring の道具の番号。doeff-hy の bytecode の guard が同じ process で 3・4 を取るので、
# 名指しの番号のうち、この子の役(実行を数える)に当たる profiler の番号を使う。
_TOOL = sys.monitoring.PROFILER_ID


class InjectedCaseError(Exception):
    """この入口が件の中で上げる失敗。"""


@do
def _failing_case(value: object) -> Generator[object, object, Never]:
    """件の中で失敗する Program(走り始めてすぐ上げる — yield は generator にするためだけに在る)。"""
    raise InjectedCaseError(f"injected into the case of {value!r}")
    yield


def _first_case_fails(
    f: Callable[[Item], Program[Answer, Any]],
) -> Callable[[Item], Program[Answer, Any]]:
    """f と同じ件の関数で、最初に呼ばれた件だけ件の中で失敗する Program を返す物。"""
    failed = False

    def case(value: Item) -> Program[Answer, Any]:
        nonlocal failed
        _calls["case"] += 1
        if failed:
            return f(value)
        failed = True
        return _failing_case(value)

    return case


_traverse_init = Traverse.__init__


def _init(
    self: Traverse[Item, Answer],
    f: Callable[[Item], Program[Answer, Any]],
    items: Iterable[Item],
    label: str | None = None,
) -> None:
    """Traverse.__init__ と同じ形で、件の関数だけを包む。"""
    _traverse_init(self, _first_case_fails(f), items, label)


def _program_started(code: CodeType, offset: int) -> None:
    """sys.monitoring の知らせ — doeff の run の実行が始まった(例が Program を走らせた)事を数える。"""
    _calls["program"] += 1


def _count_program_runs() -> None:
    """doeff の run の関数の code の実行の始まりだけを受ける。"""
    monitoring = sys.monitoring
    monitoring.use_tool_id(_TOOL, "doeff-traverse-example-marks")
    monitoring.register_callback(_TOOL, monitoring.events.PY_START, _program_started)
    monitoring.set_local_events(_TOOL, doeff.run.__code__, monitoring.events.PY_START)


def _write_counts() -> None:
    """実行の印を標準エラーへ書く(例の標準出力は比べる物なので混ぜない)。"""
    print(COUNTS_LINE.format(case=_calls["case"], program=_calls["program"]), file=sys.stderr)


def main(example: str) -> None:
    Traverse.__init__ = _init
    _count_program_runs()
    atexit.register(_write_counts)
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1])
