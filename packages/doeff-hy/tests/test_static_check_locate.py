"""Tests for the index of doeff-hy-check's position mapping (expanded Python position -> Hy position).

locate used to re-split the whole expansion into lines for every diagnostic and examine all spans from the start (26 seconds for a file with 1,402 errors). Now it builds an index
(line -> spans covering that line) once per expansion and examines only the spans covering the point's line, in the original order. The answer must be the same as when all spans are
examined -- `naive` below is a copy of the previous locate, the baseline for comparison.

- Nesting, overlapping identical spans (the later span is chosen), zero-width spans (start = point), multi-line spans, points outside lines, and UTF-16 columns are compared over all spans and points built
  with a fixed random seed.
- Failure case: an index that puts a span only on its start line (not through its end line) changes the answer for points on later lines of a multi-line span.
"""

import random
from collections.abc import Iterator
from pathlib import Path

from doeff_hy._static_check import (
    HyPosition,
    Projection,
    Span,
    SpanIndex,
    _utf16_to_index,
    locate,
    span_index,
)
from doeff_hy_bytecode_guard._records import MacroRecord

#: The position tests do not read the macros used by the expansion, so give it an empty record.
NOTHING_USED = MacroRecord("probe", (), (), (), (), ())


def naive(projection: Projection, line: int, character: int) -> HyPosition:
    """The previous locate (examines all spans) -- the baseline for comparison."""
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
    """With a fixed random seed, produce nested and crossing spans, zero-width and multi-line spans, and overlapping identical spans."""
    for number in range(chooser.randint(0, 60)):
        first, second = sorted(
            (
                (chooser.randint(0, rows - 1), chooser.randint(0, 14)),
                (chooser.randint(0, rows - 1), chooser.randint(0, 14)),
            )
        )
        if chooser.random() < 0.1:
            second = first  # zero-width span (start = end)
        yield Span(first, second, number + 1, chooser.randint(1, 9))
        if chooser.random() < 0.15:
            yield Span(first, second, number + 100, 1)  # overlapping identical span (the later is chosen)


def random_projection(seed: int) -> Projection:
    """With a fixed random seed, build an expansion that has nested spans, overlapping identical spans, zero-width spans and multi-line spans."""
    chooser = random.Random(seed)
    rows = chooser.randint(1, 30)
    text = "\n".join(
        "".join(chooser.choice("ab(😀 )") for _ in range(chooser.randint(0, 12)))
        for _ in range(rows)
    )
    return Projection(
        source=Path("probe.hy"), module="probe", text=text, spans=tuple(random_spans(chooser, rows)),
        used=NOTHING_USED,
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
    # A point on line 4, inside the span that runs from line 2 to column 3 of line 4. The inner span covers only line 1.
    projection = Projection(
        source=Path("probe.hy"),
        module="probe",
        text="a\nbbbb\ncccc\ndddd\n",
        spans=(Span((0, 0), (0, 1), 1, 1), Span((1, 0), (3, 3), 7, 2)),
        used=NOTHING_USED,
    )
    assert locate(span_index(projection), 3, 1) == HyPosition(7, 2)
    assert naive(projection, 3, 1) == HyPosition(7, 2)


def test_an_index_that_lists_spans_only_on_their_first_line_answers_differently() -> None:
    # Failure case: an index that puts a span only on its start line misses the span on a later line of a multi-line span, and the answer differs from the baseline.
    projection = Projection(
        source=Path("probe.hy"),
        module="probe",
        text="a\nbbbb\ncccc\ndddd\n",
        spans=(Span((0, 0), (0, 1), 1, 1), Span((1, 0), (3, 3), 7, 2)),
        used=NOTHING_USED,
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
