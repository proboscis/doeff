"""Tests for the single place that splits JSON Lines into lines (doeff_hy/_json_lines.hy).

What is checked:
- Only newlines split: a raw U+2028, U+2029 or U+0085 (characters str.splitlines splits on) inside a JSON string does not split the line. Whitespace-only lines are dropped.
- The consumed position is the end of a newline-terminated line (it does not cut in the middle of a multi-byte character).
"""

import json

import hy  # noqa: F401  — installs the machinery that imports Hy modules
from doeff_hy._json_lines import jsonl_lines, whole_lines_end

from doeff import run

# Characters that can be written raw inside a JSON string and that str.splitlines splits on (U+2028, U+2029, U+0085).
SEPARATORS = chr(0x2028) + chr(0x2029) + chr(0x85)


def test_a_line_separator_inside_a_json_string_does_not_split_the_line() -> None:
    # Failure case: str.splitlines also splits at U+2028 etc. inside a JSON string, turning a valid JSON line into non-JSON pieces.
    text = (
        json.dumps({"type": "assistant", "text": "web の本文" + SEPARATORS + "終わり"}, ensure_ascii=False)
        + "\n\n  \n"
        + json.dumps({"type": "user"})
        + "\n"
    )
    assert len([line for line in text.splitlines() if line.strip()]) != 2
    assert [json.loads(line)["type"] for line in run(jsonl_lines(text))] == ["assistant", "user"]


def test_the_reader_stops_at_the_end_of_the_last_whole_line() -> None:
    cases = [b"", b"ab", b"ab\n", b"ab\ncd", "あ\nい".encode()]
    assert [run(whole_lines_end(data)) for data in cases] == [0, 0, 3, 3, 4]
