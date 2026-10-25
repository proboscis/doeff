"""Every public name of doeff says what it is: in its docstring, and in the index of the API reference.

Two checks:

- Docstrings. The public names of ``doeff``, ``doeff_core_effects`` and ``doeff_vm`` (classes,
  functions and other callables defined in a doeff package) each carry a non-empty ``__doc__``.
  ``python -m tests.test_public_names_documented`` prints the counts
  (``doeff: 95 public names, with docstring = 95 (100%)``).
- The API reference. The "Index of Public Names" of docs/13-api-reference.md has one row for each
  name ``from doeff import ...`` gives, and the "Removed APIs" table lists each removed name that
  is still importable. A name added to (or dropped from) doeff without its row fails here.
"""

from __future__ import annotations

import collections.abc
import importlib
import re
import types
import typing
from pathlib import Path

import doeff

ROOT = Path(__file__).resolve().parents[1]
API_REFERENCE = ROOT / "docs" / "13-api-reference.md"
INDEX_HEADING = "## Index of Public Names"
REMOVED_HEADING = "## Removed APIs"

# The packages whose public names are counted, as the completeness gauge counts them.
COUNTED_PACKAGES = ("doeff", "doeff_core_effects", "doeff_vm")

# A name kept only to raise an error naming its replacement (``doeff.KleisliProgram`` etc.).
_REMOVED_TYPE = type(doeff.KleisliProgram)


def counted_names(package: str) -> dict[str, object]:
    """The public names of ``package`` that the docstring count covers.

    A name is counted when it does not start with ``_``, its object is callable and the object's
    ``__module__`` is in a doeff package (``doeff``, ``doeff_vm``, ``doeff_core_effects``, ...):
    names re-exported from ``typing`` or the standard library are not doeff's to document.
    """
    module = importlib.import_module(package)
    return {
        name: obj
        for name in dir(module)
        if not name.startswith("_")
        and callable(obj := getattr(module, name))
        and (getattr(obj, "__module__", None) or "").startswith("doeff")
    }


def undocumented(package: str) -> list[str]:
    """The counted names of ``package`` whose ``__doc__`` is missing or blank."""
    return sorted(
        name for name, obj in counted_names(package).items() if not (obj.__doc__ or "").strip()
    )


def doeff_public_names() -> tuple[frozenset[str], frozenset[str]]:
    """The names ``from doeff import ...`` gives: (current names, removed names).

    Submodules (``doeff.result``, ...) and the names doeff's own imports leave in its namespace
    (``Any``, ``Generator``, ``TYPE_CHECKING``, ...) are not part of the API.
    """
    current: set[str] = set()
    removed: set[str] = set()
    for name in dir(doeff):
        if name.startswith("_"):
            continue
        obj = getattr(doeff, name)
        if isinstance(obj, types.ModuleType):
            continue
        if any(getattr(source, name, None) is obj for source in (typing, collections.abc)):
            continue
        (removed if isinstance(obj, _REMOVED_TYPE) else current).add(name)
    return frozenset(current), frozenset(removed)


def _section(text: str, heading: str) -> str:
    """The lines of ``text`` from ``heading`` to the next heading of the same level."""
    start = text.index(f"\n{heading}\n") + 1
    next_heading = text.find("\n## ", start + len(heading))
    return text[start : next_heading if next_heading != -1 else len(text)]


_ROW = re.compile(r"^\| `([A-Za-z_][A-Za-z0-9_]*)` \| (?P<what>[^|]*) \| (?P<see>[^|]*) \|$")


def index_rows() -> dict[str, tuple[str, str]]:
    """The rows of the index of the API reference: name -> (what it is, link)."""
    section = _section(API_REFERENCE.read_text(encoding="utf-8"), INDEX_HEADING)
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        if match := _ROW.match(line):
            assert match.group(1) not in rows, f"the index has two rows for {match.group(1)}"
            rows[match.group(1)] = (match["what"].strip(), match["see"].strip())
    return rows


def removed_table_names() -> frozenset[str]:
    """The names in the first column of the Removed APIs table of the API reference."""
    section = _section(API_REFERENCE.read_text(encoding="utf-8"), REMOVED_HEADING)
    first_cells = (line.split("|")[1] for line in section.splitlines() if line.startswith("| `"))
    return frozenset(name for cell in first_cells for name in re.findall(r"`([^`]+)`", cell))


def test_every_counted_name_has_a_docstring() -> None:
    missing = {package: undocumented(package) for package in COUNTED_PACKAGES}
    assert missing == {package: [] for package in COUNTED_PACKAGES}, (
        f"public names without a docstring: {missing} — add a one-line docstring in the module "
        "that defines each (a Rust pyclass takes it from its `///` doc comment)"
    )


def test_the_count_covers_the_api() -> None:
    # The filter of counted_names must not shrink the population to nothing: names of each kind
    # (Python class, function, Rust pyclass, removed name) are counted.
    assert {"Ask", "run", "Ok", "Semaphore", "KleisliProgram"} <= counted_names("doeff").keys()
    assert {"Ask", "AcquireSemaphore", "scheduled"} <= counted_names("doeff_core_effects").keys()
    assert {"Pure", "Err", "EffectBase"} <= counted_names("doeff_vm").keys()


def test_the_api_reference_index_lists_every_public_name() -> None:
    current, _ = doeff_public_names()
    listed = index_rows().keys()
    assert sorted(current - listed) == [], f"add a row to '{INDEX_HEADING}' of {API_REFERENCE}"
    assert sorted(listed - current) == [], f"rows of '{INDEX_HEADING}' for names doeff lacks"


def test_every_index_row_says_what_the_name_is_and_links_to_it() -> None:
    bare = {
        name: row
        for name, row in index_rows().items()
        if not row[0] or not re.fullmatch(r"\[[^\]]+\]\([^)]+\)", row[1])
    }
    assert bare == {}, "each row needs a one-line description and a link to an example"


def test_the_removed_apis_table_lists_every_removed_name() -> None:
    _, removed = doeff_public_names()
    assert sorted(removed - removed_table_names()) == [], (
        f"add the removed names to '{REMOVED_HEADING}' of {API_REFERENCE}"
    )


def main() -> None:
    """Print the docstring counts of COUNTED_PACKAGES (the completeness gauge's numbers)."""
    for package in COUNTED_PACKAGES:
        total = len(counted_names(package))
        missing = undocumented(package)
        documented = total - len(missing)
        print(
            f"{package}: {total} public names, with docstring = {documented} "
            f"({documented * 100 // total}%)"
            + (f" — missing: {', '.join(missing)}" if missing else "")
        )


if __name__ == "__main__":
    main()
