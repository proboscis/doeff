"""JSON Lines を行に割る 1 か所(doeff_hy/json_lines.hy)の test(card ki-3d45f5857239)。

確かめること:
- 割るのは改行だけ: JSON の文字列の中の生の U+2028・U+2029・U+0085(str.splitlines が割る字)で行を割らない。空白だけの行は落とす。
- 読み終えた所は改行で閉じた行の終わり(多 byte の字の途中を切らない)。
"""

import json

import hy  # noqa: F401  — Hy の module を import する仕掛けを入れる
from doeff_hy.json_lines import jsonl_lines, whole_lines_end

from doeff import run

# JSON の文字列の中に生のまま書かれうる、str.splitlines が割る字(U+2028・U+2029・U+0085)。
SEPARATORS = chr(0x2028) + chr(0x2029) + chr(0x85)


def test_a_line_separator_inside_a_json_string_does_not_split_the_line() -> None:
    # 失敗ケース: str.splitlines は JSON の文字列の中の U+2028 などでも割り、正しい JSON の行を JSON でない片にする。
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
