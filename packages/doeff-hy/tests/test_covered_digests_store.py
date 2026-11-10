"""Test that the digests the .pyc record check rebuilds per covered marker of a macro closure walk are kept in the store (code_store) per walk material,
and that the next process takes them instead of hashing the material again.

Measurement (2026-11-10, importing agora-controllers' declaration tool scripts.declare_round on node-h): the records of the 3,000 Hy modules it loads hold
about 1,100 distinct covered markers over 23 walks, and hashing the whole material for each was about 1.2 seconds of the record check's 2.3 seconds of CPU.
A digest is a pure function of the material and the marker, so the stored one is the same as a rebuilt one.
"""

from __future__ import annotations

import marshal
from pathlib import Path

import pytest

from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard import _macro_use as macro_use


def _walk() -> macro_use.ClosureWalk:
    """A walk material of three functions (segments around them, a shape per function) -- the shape a closure walk builds (:meth:`_Walker.walked`)."""
    code = compile("def f():\n    return 1\n", "<walk>", "exec")
    slots = tuple(macro_use.CodeSlot(code, shape) for shape in ("a" * 64, "b" * 64, "c" * 64))
    return macro_use.ClosureWalk((b"head", b"one", b"two", b"tail"), slots, forced=0b001, reads_code=0)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A named store of its own, and an empty in-process memory of stored digests."""
    monkeypatch.setenv(code_store.STORE_ENV, str(tmp_path))
    monkeypatch.setattr(macro_use, "_STORED_DIGESTS", {})
    return tmp_path


def test_a_stored_digest_is_the_hashed_digest_and_the_next_process_takes_it(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _walk()
    made = {covered: first.digest(covered) for covered in (0b001, 0b011, 0b101)}
    assert made == {covered: first._hashed(covered) for covered in made}
    macro_use._write_digests()
    assert len(list(store.rglob("*" + code_store.DIGESTS_SUFFIX))) == 1

    # The next process: a new walk of the same material, with the memory of this process gone. Only the whole-material digest that names the entry is hashed.
    monkeypatch.setattr(macro_use, "_STORED_DIGESTS", {})
    hashed: list[int] = []
    plain = macro_use.ClosureWalk._hashed

    def counting(walk: macro_use.ClosureWalk, covered: int) -> str:
        hashed.extend([covered])
        return plain(walk, covered)

    monkeypatch.setattr(macro_use.ClosureWalk, "_hashed", counting)
    second = _walk()
    assert {covered: second.digest(covered) for covered in made} == made
    assert hashed == [second.everything]


def test_a_stored_entry_of_the_wrong_shape_is_discarded_and_the_digest_is_hashed(store: Path) -> None:
    walk = _walk()
    walk.digest(0b011)
    macro_use._write_digests()
    (entry,) = store.rglob("*" + code_store.DIGESTS_SUFFIX)
    entry.write_bytes(marshal.dumps(("not", "rows")))

    again = macro_use.ClosureWalk(walk.segments, walk.slots, walk.forced, walk.reads_code)
    macro_use._STORED_DIGESTS.clear()
    assert again.digest(0b011) == walk._hashed(0b011)
    assert not entry.exists()


def test_without_a_store_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(code_store.STORE_ENV, "off")
    monkeypatch.setattr(macro_use, "_STORED_DIGESTS", {})
    walk = _walk()
    assert walk.digest(0b011) == walk._hashed(0b011)
    assert macro_use._stored_digests(walk) is None
