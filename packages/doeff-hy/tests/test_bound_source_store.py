"""辿れない大域を覆う source の文の読み(doeff_hy_bytecode_guard._bound_source.binding_of)を、置き場(DOEFF_HY_CODE_STORE)に覚える形の
失敗ケース。

置き場の読みは、同じ中身・同じ名・同じ読み方の時だけ前の読みを返す — 返す物は、その process で解き直した読みと同じでなければ
ならない。process をまたぐ形は、process の中の覚えを捨てて同じ口を呼び直すことで作る(子の process の形は
test_macro_use_record.py の CROSS_CHILD)。
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _bound_source as bound_source


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """名指しの置き場(検ごとに空)と、解いた回数を数える口。process の中の覚えは検の前後で捨てる。"""
    place = tmp_path / "store"
    place.mkdir()
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(place))
    _forget_process_memory()
    yield place
    _forget_process_memory()


def _forget_process_memory() -> None:
    """process の中の覚え(中身・解いた物・束縛)を捨てる — 次の呼び出しを新しい process の呼び出しと同じにするため。"""
    for name in ("_SOURCES", "_PARSED", "_BINDINGS"):
        remembered = vars(bound_source).get(name)
        if isinstance(remembered, dict):
            remembered.clear()


def _parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """source を解いた path の並び(呼ばれる度に足す)— 置き場が当たったかを、解いたかどうかで見るため。"""
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
    """置き場の束縛の entry(並びは名の順)。"""
    return tuple(sorted(store.glob("*/*.binding")))


def test_an_unparsable_source_is_remembered_as_unreadable(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 直す前は赤: 解けない source(構文の誤り)の読み None は覚えず、新しい process が毎回解き直した。
    broken = tmp_path / "broken.py"
    broken.write_text("TABLE = {\n", encoding="utf-8")
    parses = _parses(monkeypatch)
    assert bound_source.binding_of(str(broken), "TABLE") is None
    _forget_process_memory()
    assert bound_source.binding_of(str(broken), "TABLE") is None
    assert len(parses) == 1, f"解けない source を新しい process でも解き直した: {parses}"


def test_a_file_that_cannot_be_read_is_not_remembered(tmp_path: Path, store: Path) -> None:
    # 読めない file(無い path)は中身で決まる読みではない — 置き場に何も足さない。
    assert bound_source.binding_of(str(tmp_path / "missing.py"), "TABLE") is None
    assert _entries(store) == (), "読めない file の読みを置き場に足した"


def test_the_same_text_read_as_python_and_as_hy_does_not_share_an_entry(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 拡張子で読み方が違う(Python の ast と Hy の読み)— 同じ中身でも、片方の読みをもう片方に返さない。
    text = "TABLE = 1\n"
    python = tmp_path / f"same_{uuid.uuid4().hex[:6]}.py"
    hy = python.with_suffix(".hy")
    python.write_text(text, encoding="utf-8")
    hy.write_text(text, encoding="utf-8")
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")  # 置き場を使わない読み(解き直した物)
    fresh_python = bound_source.binding_of(str(python), "TABLE")
    fresh_hy = bound_source.binding_of(str(hy), "TABLE")
    assert fresh_python != fresh_hy, "Python と Hy の読みが同じになった — 検の前提が崩れた"
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(store))
    _forget_process_memory()
    assert bound_source.binding_of(str(python), "TABLE") == fresh_python
    _forget_process_memory()
    assert bound_source.binding_of(str(hy), "TABLE") == fresh_hy, (
        "Hy の source の読みに、同じ中身の Python の source の読みを返した"
    )
    assert len(_entries(store)) == 2, f"2 つの読み方の entry が分かれていない: {_entries(store)}"


def test_a_custom_hy_repr_keeps_hy_readings_out_of_the_store(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hy の文の形(hy.repr)は process の中の共有の登録の表に依る。Hy 自身と組み込みの外の関数が登録された process は、置き場を
    # 使わない(その関数の code は鍵で覆えない)。
    import hy

    class Probe:
        """検だけの型。"""

    # 登録の表(hy.repr を定義した Hy の module の大域)へ、検の後に消える形で登録する(hy.repr-register と同じ行の形)。
    registry = hy.repr.__globals__["_registry"]
    monkeypatch.setitem(registry, Probe, (lambda _value: "probe", None))
    source = tmp_path / "table.hy"
    source.write_text('(setv TABLE {"a" 1})\n', encoding="utf-8")
    binding = bound_source.binding_of(str(source), "TABLE")
    assert binding is not None
    assert binding.statements, binding
    assert _entries(store) == (), (
        "Hy 自身の外の hy.repr の登録がある process で、Hy の読みを置き場に足した"
    )
    assert hy.repr(Probe()) == "probe"
