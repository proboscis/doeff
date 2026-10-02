"""doeff-hy-check の位置の戻し(展開した Python の位置 → Hy の位置)の索引の検(agora-redesign #2846)。

locate は以前、診断ごとに展開の全文を行へ割り直し、全部の範囲を頭から調べていた(赤が 1,402 件の file で 26 秒)。今は展開ごとに
1 度だけ索引(行 → その行に掛かる範囲)を作り、点の行に掛かる範囲だけを元の順で調べる。答えは全部の範囲を調べた時と同じでなければ
ならない — 下の `naive` は前の locate の写しで、比べの元。

- 入れ子・同じ範囲の重なり(後ろの範囲が選ばれる)・幅 0 の範囲(始まり = 点)・複数行の範囲・行の外の点・UTF-16 の列を、決まった
  乱数で作った範囲と点の全部で比べる。
- 失敗ケース: 範囲を始まりの行にだけ載せる索引(終わりの行まで載せない)は、複数行の範囲の後ろの行の点で答えが変わる。
"""

import random
from collections.abc import Iterator
from pathlib import Path

from doeff_hy.static_check import (
    HyPosition,
    Projection,
    Span,
    SpanIndex,
    _utf16_to_index,
    locate,
    span_index,
)


def naive(projection: Projection, line: int, character: int) -> HyPosition:
    """前の locate(全部の範囲を調べる)— 比べの元。"""
    lines = projection.text.split("\n")
    column = _utf16_to_index(lines[line], character) if line < len(lines) else character
    point = (line, column)
    best: Span | None = None
    for span in projection.spans:
        contains = span.start <= point < span.end or span.start == point
        inner = best is None or (span.start >= best.start and span.end <= best.end)
        if contains and inner:
            best = span
    if best is None:
        return HyPosition(1, 1)
    return HyPosition(best.hy_line, best.hy_column)


def random_spans(chooser: random.Random, rows: int) -> Iterator[Span]:
    """決まった乱数で、入れ子・交わり・幅 0・複数行の範囲と、同じ範囲の重なりを出す。"""
    for number in range(chooser.randint(0, 60)):
        first, second = sorted(
            (
                (chooser.randint(0, rows - 1), chooser.randint(0, 14)),
                (chooser.randint(0, rows - 1), chooser.randint(0, 14)),
            )
        )
        if chooser.random() < 0.1:
            second = first  # 幅 0 の範囲(始まり = 終わり)
        yield Span(first, second, number + 1, chooser.randint(1, 9))
        if chooser.random() < 0.15:
            yield Span(first, second, number + 100, 1)  # 同じ範囲の重なり(後ろが選ばれる)


def random_projection(seed: int) -> Projection:
    """決まった乱数で、入れ子の範囲・同じ範囲の重なり・幅 0 の範囲・複数行の範囲を持つ展開を作る。"""
    chooser = random.Random(seed)
    rows = chooser.randint(1, 30)
    text = "\n".join(
        "".join(chooser.choice("ab(😀 )") for _ in range(chooser.randint(0, 12)))
        for _ in range(rows)
    )
    return Projection(
        source=Path("probe.hy"), module="probe", text=text, spans=tuple(random_spans(chooser, rows))
    )


def all_points(projection: Projection) -> list[tuple[int, int]]:
    rows = len(projection.text.split("\n"))
    return [(line, character) for line in range(rows + 2) for character in range(18)]


def test_the_index_answers_the_same_as_scanning_every_span() -> None:
    for seed in range(300):
        projection = random_projection(seed)
        index = span_index(projection)
        for line, character in all_points(projection):
            assert locate(index, line, character) == naive(projection, line, character), (
                seed,
                line,
                character,
            )


def test_a_multi_line_span_is_found_on_its_last_line() -> None:
    # 2 行目から 4 行目の 3 列まで掛かる範囲の中の、4 行目の点。内側の範囲は 1 行目だけ。
    projection = Projection(
        source=Path("probe.hy"),
        module="probe",
        text="a\nbbbb\ncccc\ndddd\n",
        spans=(Span((0, 0), (0, 1), 1, 1), Span((1, 0), (3, 3), 7, 2)),
    )
    assert locate(span_index(projection), 3, 1) == HyPosition(7, 2)
    assert naive(projection, 3, 1) == HyPosition(7, 2)


def test_an_index_that_lists_spans_only_on_their_first_line_answers_differently() -> None:
    # 失敗ケース: 範囲を始まりの行にだけ載せた索引は、複数行の範囲の後ろの行で範囲を見落とし、答えが比べの元と変わる。
    projection = Projection(
        source=Path("probe.hy"),
        module="probe",
        text="a\nbbbb\ncccc\ndddd\n",
        spans=(Span((0, 0), (0, 1), 1, 1), Span((1, 0), (3, 3), 7, 2)),
    )
    full = span_index(projection)
    first_line_only = SpanIndex(
        full.spans,
        full.lines,
        tuple(
            tuple(number for number in numbers if full.spans[number].start[0] == row)
            for row, numbers in enumerate(full.covering)
        ),
    )
    assert locate(first_line_only, 3, 1) != naive(projection, 3, 1)
