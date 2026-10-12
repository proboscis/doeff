"""例 1 本を `hy <例>` と同じに走らせ、Traverse の走りごとに最初の件を件の中で失敗させる(test_examples_run.py の子)。

使い方: python run_example_failing_a_case.py <例の path>

件の中の失敗は、Traverse が件の関数で作る Program の中で例外を上げる事で起こす — 例の source の文字列(handler の式・mock の値)
を書き換える変異は、出力を変えない例の中の書き換えでも検の file を直させた(card ki-f8a58fb49dd9 の所見 4)。Traverse の handler は
件の中の例外を件の失敗として記録して先へ進むので、例はふつう 0 で終わり、出力の比べ方だけが失敗を捕まえる。

終わる時に、件の関数を呼んだ数(件の実行の印)を標準エラーへ CALLS_LINE の形で書く。0 なら例は件を 1 つも走らせていない。
"""

import atexit
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from doeff_traverse.effects import Traverse

from doeff import Program, do

Item = TypeVar("Item")
Answer = TypeVar("Answer")

# 件の関数を呼んだ数を書く行(test_examples_run.py の _CASE_CALLS が読む)。
CALLS_LINE = "case-calls: {}"

# 件の関数を呼んだ数(件の実行の印)。
_calls: Counter[str] = Counter()


class InjectedCaseError(Exception):
    """この入口が件の中で上げる失敗。"""


@do
def _failing_case(value: object):
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


def main(example: str) -> None:
    Traverse.__init__ = _init
    atexit.register(lambda: print(CALLS_LINE.format(_calls["case"]), file=sys.stderr))
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1])
