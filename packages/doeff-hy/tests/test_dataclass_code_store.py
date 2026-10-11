"""dataclass が型ごとに作る関数の source(``__create_fn__``)の compile を、source の中身で引く保存先(code_store)に覚え、次の process で
compile し直さない(card ki-e77d44aa61e5)の検。

測り(2026-10-11・agora-controllers の test_emulated_screen.hy の読み込み): dataclass の作成 3798 型の費用のほぼ全部が、関数の
source の exec(compile)だった。違う source は 769 通りで、前の process の code を使うと読み込みの CPU が中央値 3.71 → 3.02 秒。
compile は source の文・Python の版・最適化の段だけで決まるので、覚えた code は今 compile した物と同じ。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

#: 子の process — 包みを入れ、関数の source の compile の回数(文のまま exec した回数と compile した回数の和)を数え、型の振る舞いを
#: 出す。dataclasses は文を exec に渡して中で compile するので、exec と compile の両方を数える。
_CHILD = """\
import builtins
import collections
import dataclasses
import doeff_hy_bytecode_guard

doeff_hy_bytecode_guard.install()
compiled = collections.Counter()
plain_compile, plain_exec = builtins.compile, builtins.exec


def is_function_source(source):
    return isinstance(source, str) and "__create_fn__" in source


def counting_compile(source, *args, **kwargs):
    compiled.update(["source"] if is_function_source(source) else [])
    return plain_compile(source, *args, **kwargs)


def counting_exec(source, *args, **kwargs):
    compiled.update(["source"] if is_function_source(source) else [])
    return plain_exec(source, *args, **kwargs)


builtins.compile, builtins.exec = counting_compile, counting_exec
Pair = dataclasses.make_dataclass("Pair", [("left", int), ("right", str)], frozen=True, order=True)
one, same, other = Pair(1, "a"), Pair(1, "a"), Pair(2, "b")
try:
    one.left = 3
    frozen = False
except dataclasses.FrozenInstanceError:
    frozen = True
print(compiled["source"], repr(one), one == same, hash(one) == hash(same), one < other, frozen)
"""


def _run_child(tmp_path: Path, store: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """子の process を 1 つ走らせ、出力の欄(compile の回数・repr・等価・hash・順序・frozen)を返す。子は検の process の環境を
    受け継ぎ、下の 2 つだけを置き換える(検の後に monkeypatch が戻す)。"""
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    # 子も checkout の下に .pyc を書かない(root の conftest の検査 — 書くと包みの module の .pyc が editable の checkout に残る)。
    # 保存先は名指しなので、.pyc を書かない設定でも足す(code_store の作法)。
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", store)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stderr
    count, *rest = completed.stdout.rsplit(maxsplit=4)
    head, _, representation = count.partition(" ")
    return [head, representation, *rest]


def test_a_second_process_reuses_the_dataclass_functions_without_compiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 つ目の process が compile した関数の code を保存先に足し、2 つ目の process は compile せずに使う。振る舞い(repr・等価・
    hash・順序・frozen)は compile した物と同じ。"""
    store = tmp_path / "store"
    first = _run_child(tmp_path, str(store), monkeypatch)
    assert int(first[0]) >= 1, f"1 つ目の process が関数の source を compile しない: {first}"
    assert list(store.rglob("*.code")), "1 つ目の process が保存先に足さない"
    second = _run_child(tmp_path, str(store), monkeypatch)
    assert second[0] == "0", f"2 つ目の process が関数の source を {second[0]} 回 compile し直した"
    assert second[1:] == first[1:] == ["Pair(left=1, right='a')", "True", "True", "True", "True"]


def test_an_off_store_compiles_in_every_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保存先を使わない設定(``off``)では覚えず、どの process も compile する(振る舞いは同じ)。"""
    first = _run_child(tmp_path, "off", monkeypatch)
    second = _run_child(tmp_path, "off", monkeypatch)
    assert int(first[0]) >= 1 and int(second[0]) >= 1
    assert second[1:] == first[1:]
