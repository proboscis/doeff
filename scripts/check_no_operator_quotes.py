#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fail when a dated, verbatim quote of the maintainer's chat words enters the repository.

doeff is public. Design records keep *what* was decided and *why*; they must not carry the
maintainer's own words copied from private chat sessions together with the time they were said
(for example ``operator 2026-09-29 "go"`` or ``利用者 10-12 22:38「…」``). Provenance is written as
a short mark such as ``decision 2026-09-29`` or as a pointer to the internal tracker instead.

Two scopes are checked:

* ``--tree`` (default): every tracked file of the working tree.
* ``--commits <rev-range>``: the messages of the commits in the range.

Exit status is 1 when any line matches, 0 otherwise. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# Files that must name the forbidden shapes in order to define or test them.
ALLOWED_PATHS = frozenset(
    {
        "scripts/check_no_operator_quotes.py",
        "tests/misc/test_check_no_operator_quotes.py",
    }
)

_SPEAKER = r"(?:operator|Operator|OPERATOR|maintainer|Maintainer|利用者|ユーザー|オペレーター)"
_DATE = (
    r"(?:20\d{2}-\d{2}-\d{2}"  # 2026-09-29
    r"|(?<![\d\-/.])\d{1,2}-\d{2}(?![\d\-/.])"  # 10-26
    r"|(?<![\d:])\d{1,2}:[0-5x][0-9x](?![\d])"  # 22:38 / 01:0x
    r")"
)
# An opening quote: Japanese brackets, curly quotes, an escaped straight quote, or a straight
# quote that opens a span (after a space, colon or bracket) and is followed by quoted text.
_OPEN = r"(?:「|『|“|\\\"|(?:(?<=[\s:：(（])|^)\"(?=[^\s,)\]};\"]))"
_GAP = r"[^「『“\"\n]"

QUOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # speaker … date … opening quote
    re.compile(_SPEAKER + _GAP + r"{0,80}?" + _DATE + _GAP + r"{0,60}?" + _OPEN + r"[^」』”\"\n]{2}"),
    # date … speaker … opening quote
    re.compile(_DATE + _GAP + r"{0,40}?" + _SPEAKER + _GAP + r"{0,60}?" + _OPEN + r"[^」』”\"\n]{2}"),
)
# An explicitly verbatim quote ("逐語", "原文", "verbatim") on a line that names the speaker — the date is
# often on the line before.
VERBATIM_PATTERN = re.compile(r"(?:逐語|原文|verbatim)" + _GAP + r"{0,40}?" + _OPEN + r"[^」』”\"\n]{2}")
SPEAKER_PATTERN = re.compile(_SPEAKER)


@dataclass(frozen=True)
class Finding:
    where: str
    line_no: int
    text: str

    def render(self) -> str:
        return f"{self.where}:{self.line_no}: {self.text.strip()[:160]}"


def quote_spans(line: str) -> list[re.Match[str]]:
    """Return the matches of a dated verbatim quote on one line."""
    spans = [m for pattern in QUOTE_PATTERNS for m in pattern.finditer(line)]
    if SPEAKER_PATTERN.search(line):
        spans.extend(VERBATIM_PATTERN.finditer(line))
    return spans


def scan_text(where: str, text: str) -> Iterator[Finding]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if quote_spans(line):
            yield Finding(where, line_no, line)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def scan_tree(root: Path, paths: Iterable[str]) -> Iterator[Finding]:
    for rel in paths:
        if rel in ALLOWED_PATHS:
            continue
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        yield from scan_text(rel, data.decode("utf-8", errors="replace"))


def scan_commits(rev_range: str) -> Iterator[Finding]:
    shas = _git("rev-list", "--no-merges", rev_range).split()
    for sha in shas:
        yield from scan_text(f"commit {sha[:12]}", _git("log", "-1", "--format=%B", sha))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commits", metavar="REV_RANGE", help="check commit messages instead")
    parser.add_argument("paths", nargs="*", help="limit the tree scan to these tracked paths")
    args = parser.parse_args(argv)

    if args.commits:
        findings = list(scan_commits(args.commits))
    else:
        root = Path(_git("rev-parse", "--show-toplevel").strip())
        findings = list(scan_tree(root, args.paths or tracked_files(root)))

    for finding in findings:
        print(finding.render())
    if findings:
        print(
            f"\n{len(findings)} line(s) quote the maintainer's chat words with a date. Keep the"
            " decision and its reason, drop the quoted words, and write the provenance as a short"
            " mark (e.g. 'decision 2026-09-29') or a pointer to the internal tracker.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
