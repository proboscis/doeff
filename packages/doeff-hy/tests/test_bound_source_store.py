"""Failure cases for remembering, in the store (DOEFF_HY_CODE_STORE), the reading of source statements that cover untraceable globals (doeff_hy_bytecode_guard._bound_source.binding_of).


The store returns a previous reading only for the same content, the same name, and the same way of reading - what it returns must equal
the reading re-solved in that process. The cross-process shape is made by dropping the in-process memory and calling the same entry point again (the child-process shape is
CROSS_CHILD in test_macro_use_record.py).
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _bound_source as bound_source


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A named store (empty for each check) and an entry point that counts the number of solves. The in-process memory is dropped before and after each check."""
    place = tmp_path / "store"
    place.mkdir()
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(place))
    _forget_process_memory()
    yield place
    _forget_process_memory()


def _forget_process_memory() -> None:
    """Drop the in-process memory (contents, solved items, bindings) - to make the next call the same as a call in a new process."""
    for name in ("_SOURCES", "_PARSED", "_BINDINGS"):
        remembered = vars(bound_source).get(name)
        if isinstance(remembered, dict):
            remembered.clear()


def _parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The list of paths whose source was solved (appended on each call) - to see whether the store hit by whether it solved."""
    seen: list[str] = []
    reading = bound_source._read

    def counting(
        data: bytes, path: str
    ) -> "bound_source.PythonModule | bound_source.HyModule | None":
        seen.append(path)
        return reading(data, path)

    monkeypatch.setattr(bound_source, "_read", counting)
    return seen


def _entries(store: Path) -> tuple[Path, ...]:
    """Binding entries in the store (listed in name order)."""
    return tuple(sorted(store.glob("*/*.binding")))


def test_an_unparsable_source_is_remembered_as_unreadable(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix: the reading None for unsolvable source (syntax error) was not remembered, and each new process re-solved it.
    broken = tmp_path / "broken.py"
    broken.write_text("TABLE = {\n", encoding="utf-8")
    parses = _parses(monkeypatch)
    assert bound_source.binding_of(str(broken), "TABLE") is None
    _forget_process_memory()
    assert bound_source.binding_of(str(broken), "TABLE") is None
    assert len(parses) == 1, f"unsolvable source was re-solved in a new process too: {parses}"


def test_a_file_that_cannot_be_read_is_not_remembered(tmp_path: Path, store: Path) -> None:
    # An unreadable file (nonexistent path) has a reading not determined by content - add nothing to the store.
    assert bound_source.binding_of(str(tmp_path / "missing.py"), "TABLE") is None
    assert _entries(store) == (), "added the reading of an unreadable file to the store"


def test_the_same_text_read_as_python_and_as_hy_does_not_share_an_entry(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The way of reading differs by extension (Python ast vs Hy reading) - even with the same content, do not return one reading to the other.
    text = "TABLE = 1\n"
    python = tmp_path / f"same_{uuid.uuid4().hex[:6]}.py"
    hy = python.with_suffix(".hy")
    python.write_text(text, encoding="utf-8")
    hy.write_text(text, encoding="utf-8")
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")  # reading without the store (the re-solved one)
    fresh_python = bound_source.binding_of(str(python), "TABLE")
    fresh_hy = bound_source.binding_of(str(hy), "TABLE")
    assert fresh_python != fresh_hy, "the Python and Hy readings became the same - the premise of the check broke"
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(store))
    _forget_process_memory()
    assert bound_source.binding_of(str(python), "TABLE") == fresh_python
    _forget_process_memory()
    assert bound_source.binding_of(str(hy), "TABLE") == fresh_hy, (
        "returned the reading of Python source with the same content for the reading of Hy source"
    )
    assert len(_entries(store)) == 2, f"entries of the two reading ways are not separated: {_entries(store)}"


def test_a_custom_hy_repr_keeps_hy_readings_out_of_the_store(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shape of Hy statements (hy.repr) depends on the in-process shared registration table. A process where functions outside Hy itself and the builtins are registered does not use
    # the store (the code of those functions cannot be covered by the key).
    import hy

    class Probe:
        """A type used only by the check."""

    # Register into the registration table (the globals of the Hy module that defines hy.repr) in a form that disappears after the check (the same line shape as hy.repr-register).
    registry = hy.repr.__globals__["_registry"]
    monkeypatch.setitem(registry, Probe, (lambda _value: "probe", None))
    source = tmp_path / "table.hy"
    source.write_text('(setv TABLE {"a" 1})\n', encoding="utf-8")
    binding = bound_source.binding_of(str(source), "TABLE")
    assert binding is not None
    assert binding.statements, binding
    assert _entries(store) == (), (
        "Added the Hy reading to the store in a process with hy.repr registrations from outside Hy itself"
    )
    assert hy.repr(Probe()) == "probe"
