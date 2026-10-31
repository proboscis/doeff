"""The docs, the README and the package READMEs show handlers only in the current style.

A handler is a def marked with ``@handler``, and it is applied to a program as it is:
``run(budget(10)(research([...])))``. The old style wraps an unmarked function at the call,
``handler(fn)(program)``; it was still in about 35 places of the docs after the README had moved
on. This check counts the old style in the pages a reader opens and keeps it
at 0. The records of their time are left out: ``docs/proposals`` and ``docs/specs`` (a proposal that
still shows the old style says so in a note), and the design notes, design checks and history,
whose evidence quotes code as it was.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ``handler(fn)(`` — an unmarked function wrapped at the call and applied to a program.
OLD_STYLE = re.compile(r"\bhandler\([a-z_]+\)\(")
# Records of their time, kept as written.
HISTORICAL = (
    "docs/proposals/",
    "docs/specs/",
    "docs/design/",
    "docs/design-checks/",
    "docs/history/",
)


def _pages() -> list[Path]:
    """The docs pages, the README and the package READMEs, without the historical records."""
    docs = [
        page
        for page in sorted((ROOT / "docs").rglob("*.md"))
        if not page.relative_to(ROOT).as_posix().startswith(HISTORICAL)
    ]
    return [ROOT / "README.md", *docs, *sorted(ROOT.glob("packages/*/README.md"))]


def test_the_pages_are_found() -> None:
    # With no pages, the count below is 0 for nothing.
    pages = {page.relative_to(ROOT).as_posix() for page in _pages()}
    assert {"README.md", "docs/20-why-effects-over-di.md", "packages/doeff-notify/README.md"} <= pages


def test_no_page_installs_a_handler_in_the_old_style() -> None:
    found = [
        f"{page.relative_to(ROOT).as_posix()}:{number}: {line.strip()}"
        for page in _pages()
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1)
        if OLD_STYLE.search(line)
    ]
    assert found == [], (
        "handler(fn)(program) is the old style: put @handler above the def of fn and apply it "
        "to the program as it is, fn(program)\n" + "\n".join(found)
    )
