"""doeff-hy-check の運び(`--carry` — doeff_hy/_static_carry.py)の失敗ケース。

agora-controllers の日次の型の測り(hy-type-trend)は範囲の .hy 約 3,000 本を毎回全部 pyright に当て、上限 7,200 秒で 2 回続けて切れた。
運びは、前の回から入力の変わらない .hy を pyright に当てず、前の回の診断を運ぶ。運んだ答えが全部当てた答えと
違えば、型の赤を取りこぼす(直した赤が残る・新しい赤が出ない)ので、ここで留める:

- 表面: 型を全部宣言した関数の本体と docstring は、使い手の入力に入らない。署名・型を宣言しない関数の本体・self の属性へ代入する
  method の本体は入る。
- 依存: 使う名の宣言だけに依る(同じ module の別の名の変更は伝えない)。推移で届く宣言・新しい file が import を解く変更・py.typed・
  star import・読めない module(毎回当てる)は伝える。
- 運び: 2 回目は入力の変わった file だけを pyright に渡し、運んだ診断が出力に入る。運びの file が読めなければ全部を当てる。
- 本物の pyright で、運んだ回の答えが、同じ木を全部当てた回の答えと同じ(本体だけの変更・使う名の署名の変更・推移の型の変更・
  解けなかった import が解ける変更)。
"""

import contextlib
import importlib.util
import io
import json
import shlex
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest
from doeff_hy import CarryUnreadableError
from doeff_hy import _static_carry as static_carry
from doeff_hy import _static_check as static_check
from doeff_hy._check_slot import SlotHeld
from doeff_hy._static_carry import (
    CarriedFile,
    CarriedRow,
    CarryRecord,
    Environment,
    FileInputs,
    FileRows,
    InputGraph,
    module_facts,
    next_record,
    places_of,
    plan_carry,
    read_carry,
)

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")


# --- 表面 ------------------------------------------------------------------------------------------------------------------


def _surfaces(text: str) -> dict[str, tuple[str, ...]]:
    """module の名ごとの表面(その名を束ねる文の ast.dump の並び)。"""
    facts = module_facts(text)
    return {
        name: tuple(facts.statements[i].surface for i in indexes)
        for name, indexes in facts.bound.items()
    }


def test_the_body_and_docstring_of_a_fully_annotated_function_are_not_its_surface() -> None:
    """型を全部宣言した関数は、本体と docstring を替えても表面が同じ — 使い手を当て直さない。"""
    before = _surfaces('def f(x: int) -> str:\n    "説明"\n    return str(x)\n')
    after = _surfaces('def f(x: int) -> str:\n    "別の説明"\n    y = x + 1\n    return str(y)\n')
    assert before["f"] == after["f"]


def test_the_signature_of_a_function_is_its_surface() -> None:
    """引数か答えの型を替えれば表面が変わる。"""
    assert (
        _surfaces("def f(x: int) -> str:\n    return ''\n")["f"]
        != _surfaces("def f(x: int) -> int:\n    return 0\n")["f"]
    )
    assert (
        _surfaces("def f(x: int) -> str:\n    return ''\n")["f"]
        != _surfaces("def f(x: str) -> str:\n    return ''\n")["f"]
    )


def test_the_body_of_a_function_without_its_return_type_is_its_surface() -> None:
    """答えの型を宣言しない関数は、pyright が本体から答えの型を推すので、本体も表面。"""
    assert (
        _surfaces("def f(x: int):\n    return 1\n")["f"]
        != _surfaces("def f(x: int):\n    return 'a'\n")["f"]
    )


def test_an_empty_body_and_a_full_body_have_different_surfaces() -> None:
    """空の本体(Protocol の実装の無い method と数えられる)と中身の在る本体は、表面で分かれる。"""
    assert (
        _surfaces("def f(x: int) -> int:\n    ...\n")["f"]
        != _surfaces("def f(x: int) -> int:\n    return x\n")["f"]
    )


def test_a_method_that_assigns_to_self_keeps_its_body_in_the_class_surface() -> None:
    """self の属性へ代入する method の本体は class の表面(pyright はそこから属性の型を推す)。代入しない method の本体は表面でない。"""
    assigning = "class C:\n    def __init__(self, x: int) -> None:\n        self.x = {}\n"
    assert _surfaces(assigning.format("x"))["C"] != _surfaces(assigning.format("str(x)"))["C"]
    reading = "class C:\n    def size(self, x: int) -> int:\n        return {}\n"
    assert _surfaces(reading.format("x"))["C"] == _surfaces(reading.format("x + 1"))["C"]


def test_each_name_of_a_module_import_statement_is_its_own_surface() -> None:
    """1 つの import の文に名を足しても、他の名の表面は変わらない(doeff/__init__ の長い import の文)。"""
    before = _surfaces("from m import a, b\n")
    after = _surfaces("from m import a, b, c\n")
    assert before["a"] == after["a"]
    assert before["b"] == after["b"]


# --- 計画と運びの文書 ------------------------------------------------------------------------------------------------------


def _carried(path: str, inputs: str, checked: int, *messages: str) -> CarriedFile:
    """運びの文書の file 1 つ(診断は文言だけを替えた行)。"""
    return CarriedFile(
        path, inputs, checked, tuple(CarriedRow(1, 1, "error", "rule", m) for m in messages)
    )


def test_without_a_previous_carry_every_file_is_checked() -> None:
    """運びが無い・読めない・環境が違う回は全部を当てる。"""
    inputs = (FileInputs("a.hy", "1"), FileInputs("b.hy", "2"))
    assert {c.reason for c in plan_carry(None, "f", inputs, 12).checking} == {"absent"}
    assert {c.reason for c in plan_carry(CarryUnreadableError("x"), "f", inputs, 12).checking} == {
        "unreadable"
    }
    previous = CarryRecord("old", 3, (_carried("a.hy", "1", 3), _carried("b.hy", "2", 3)))
    plan = plan_carry(previous, "new", inputs, 12)
    assert {c.reason for c in plan.checking} == {"environment"}
    assert plan.run == 4


def test_only_changed_and_new_files_are_checked_and_the_oldest_are_refreshed() -> None:
    """入力の変わった file と前の回に無い file を当て、変わらない file のうち当てた回の一番古い ceil(本数 / 周期) 本を見直す。"""
    previous = CarryRecord(
        "f",
        9,
        (
            _carried("a.hy", "1", 9),
            _carried("b.hy", "2", 4),
            _carried("c.hy", "3", 7),
            _carried("d.hy", "4", 9),
        ),
    )
    inputs = (
        FileInputs("a.hy", "1"),
        FileInputs("b.hy", "2"),
        FileInputs("c.hy", "x"),
        FileInputs("d.hy", "4"),
        FileInputs("e.hy", "5"),
    )
    plan = plan_carry(previous, "f", inputs, 5)
    assert {c.path: c.reason for c in plan.checking} == {
        "c.hy": "changed",
        "e.hy": "new",
        "b.hy": "refresh",
    }
    assert sorted(c.path for c in plan.carried) == ["a.hy", "d.hy"]
    assert plan.run == 10
    assert {c.path for c in plan_carry(previous, "f", inputs, 0).checking} == {
        "c.hy",
        "e.hy",
    }  # 0 = 見直さない


def test_the_next_record_keeps_carried_rows_and_names_refresh_mismatches() -> None:
    """当てた file は今回の診断と番号、運んだ file は前の回の記録のまま。見直しで答えが違えば名乗る。"""
    previous = CarryRecord(
        "f",
        2,
        (
            _carried("a.hy", "1", 2, "a1"),
            _carried("b.hy", "2", 1, "b1"),
            _carried("c.hy", "3", 2, "c1"),
        ),
    )
    inputs = (FileInputs("a.hy", "1"), FileInputs("b.hy", "2"), FileInputs("c.hy", "9"))
    plan = plan_carry(previous, "f", inputs, 3)
    fresh = (
        FileRows("b.hy", (CarriedRow(1, 1, "error", "rule", "b2"),)),
        FileRows(
            "c.hy",
            (CarriedRow(2, 1, "error", "rule", "c2"), CarriedRow(1, 1, "error", "rule", "c3")),
        ),
    )
    record, summary = next_record(plan, "f", inputs, fresh)
    by_path = {f.path: f for f in record.files}
    assert by_path["a.hy"] == previous.files[0]
    assert by_path["b.hy"].checked == 3
    assert [r.message for r in by_path["c.hy"].rows] == ["c2", "c3"]  # pyright の答えの順のまま
    assert summary.mismatched == ("b.hy",)
    assert (summary.checked, summary.carried) == (2, 1)
    text = static_carry.carry_text(record, summary)
    assert json.loads(text)["summary"]["refresh_mismatches"] == 1


def test_a_partial_record_keeps_only_finished_and_still_valid_files() -> None:
    """組ごとの途中の文書は、当て終えた file の今回の診断・運んだ file・まだ当てていない見直しの file の前の記録を持ち、まだ当てていない
    変わった・新しい file を持たない(次の回は new として当てる)。数は当て終えた file だけ。"""
    previous = CarryRecord(
        "f",
        2,
        (
            _carried("a.hy", "1", 2, "a1"),
            _carried("b.hy", "2", 1, "b1"),
            _carried("c.hy", "3", 2, "c1"),
        ),
    )
    inputs = (
        FileInputs("a.hy", "1"),
        FileInputs("b.hy", "2"),
        FileInputs("c.hy", "9"),
        FileInputs("d.hy", "4"),
    )
    plan = plan_carry(previous, "f", inputs, 4)  # 見直しは ceil(4 / 4) = 1 本 — 当てた回の一番古い b
    assert {c.path: c.reason for c in plan.checking} == {
        "c.hy": "changed",
        "d.hy": "new",
        "b.hy": "refresh",
    }
    fresh = (FileRows("c.hy", (CarriedRow(1, 1, "error", "rule", "c2"),)),)
    record, summary = next_record(plan, "f", inputs, fresh, frozenset({"c.hy"}))
    by_path = {f.path: f for f in record.files}
    assert sorted(by_path) == ["a.hy", "b.hy", "c.hy"]
    assert by_path["a.hy"] == previous.files[0]
    assert by_path["b.hy"] == previous.files[1]  # まだ見直していない — 前の記録のまま
    assert ([r.message for r in by_path["c.hy"].rows], by_path["c.hy"].checked) == (["c2"], 3)
    assert (summary.checked, summary.carried, summary.mismatched) == (1, 1, ())
    # 次の回は、まだ当てていなかった d を new として当て、当て終えた c は運ぶ。
    after = plan_carry(record, "f", inputs, 0)
    assert {c.path: c.reason for c in after.checking} == {"d.hy": "new"}


def test_the_carry_file_round_trips_and_a_broken_one_is_unreadable(tmp_path: Path) -> None:
    """書いた運びの file は同じ記録に読める。壊れた file・版の違う file は CarryUnreadableError(黙って空と読まない)。"""
    record = CarryRecord("f", 5, (_carried("a.hy", "1", 5, "m"),))
    plan = plan_carry(None, "f", (FileInputs("a.hy", "1"),), 12)
    _, summary = next_record(plan, "f", (FileInputs("a.hy", "1"),), ())
    path = tmp_path / "carry.json"
    static_carry.write_carry(path, static_carry.carry_text(record, summary))
    assert read_carry(path) == record
    assert read_carry(tmp_path / "missing.json") is None
    for broken in (
        "{",
        '{"version": 2}',
        '{"version": 1, "fingerprint": "f", "run": 1, "files": {"a": {}}}',
    ):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(CarryUnreadableError):
            read_carry(path)


# --- 環境の指紋 ------------------------------------------------------------------------------------------------------------


def _function_from(place: Path, source: str) -> Callable[..., object]:
    """source を module の file に書いて読み、その中の関数 f を返す(inspect が source を読める関数)。"""
    place.mkdir(parents=True)
    path = place / "semantics_case.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"semantics_case_{place.name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return vars(module)["f"]


def test_a_comment_only_change_keeps_the_semantics_digest(tmp_path: Path) -> None:
    """診断を作る関数の註・空行だけの書き換えは指紋を替えず(全部の .hy を当て直さない)、code の変更は替える。"""
    plain = _function_from(tmp_path / "a", "def f(x: int) -> int:\n    return x + 1\n")
    commented = _function_from(
        tmp_path / "b", "def f(x: int) -> int:  # noqa: DOEFF007 - 註\n\n    return x + 1  # 註\n"
    )
    changed = _function_from(tmp_path / "c", "def f(x: int) -> int:\n    return x + 2\n")
    assert static_carry.semantics_digest([plain]) == static_carry.semantics_digest([commented])
    assert static_carry.semantics_digest([plain]) != static_carry.semantics_digest([changed])


def test_an_editable_distribution_version_is_not_in_the_listing(tmp_path: Path) -> None:
    """editable で入れた配布物(doeff の checkout)の版の上げは探し道の指紋の材料を替えず、editable でない配布物の版の上げは替える。"""

    def listing(editable_version: str, other_version: str) -> list[str]:
        place = tmp_path / f"{editable_version}-{other_version}"
        editable = place / f"doeff_hy-{editable_version}.dist-info"
        editable.mkdir(parents=True, exist_ok=True)
        (editable / "direct_url.json").write_text(
            '{"url": "file:///x", "dir_info": {"editable": true}}', encoding="utf-8"
        )
        (place / f"httpx-{other_version}.dist-info").mkdir(exist_ok=True)
        return static_carry._listing(place)

    assert listing("0.5.0", "0.28.1") == listing("0.6.0", "0.28.1")
    assert listing("0.5.0", "0.28.1") != listing("0.5.0", "0.29.0")


# --- 依存の digest ---------------------------------------------------------------------------------------------------------


class _Tree:
    """入力の digest を測る小さな木 — 根の下の target.hy(展開の代わりの Python を staged に渡す)と、根の下の .py の module。"""

    def __init__(self, root: Path, target: str) -> None:
        self.root = root
        self.target = target

    def write(self, relative: str, text: str) -> None:
        """根の下に file を書く(dir も作る)。"""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def digest(self, searched: tuple[Path, ...] = ()) -> str:
        """target.hy の入力の digest(毎回新しい graph — 1 回の実行と同じ)。searched = 中身を file ごとに見る python の探し道。"""
        source = self.root / "target.hy"
        environment = Environment("fingerprint", searched, places_of(self.root))
        graph = InputGraph(
            self.root, [self.root], self.root / "typings", environment, {source: self.target}, ()
        )
        return graph.inputs_of(source, "own")


@pytest.fixture
def tree(tmp_path: Path) -> Iterator[_Tree]:
    root = tmp_path / "repo"
    root.mkdir()
    return _Tree(root, "from pkg.impl import used\n\ndef go() -> int:\n    return used(1)\n")


def test_only_the_declarations_a_file_uses_are_its_inputs(tree: _Tree) -> None:
    """使う名の署名を替えれば入力が変わり、使う名の本体・同じ module の使わない名の署名を替えても変わらない。"""
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused(x: int) -> int:\n    return x\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x + 1\n\ndef unused(x: str) -> str:\n    return x\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> str:\n    return ''\n\ndef unused(x: str) -> str:\n    return x\n",
    )
    assert tree.digest() != first


def test_a_declaration_reached_through_a_signature_is_an_input(tree: _Tree) -> None:
    """使う名の署名が読む型(別の module の class)を替えれば入力が変わる — 推移で届く宣言。"""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/kinds.py", "class Size:\n    n: int\n")
    tree.write(
        "pkg/impl.py",
        "from pkg.kinds import Size\n\ndef used(x: int) -> 'Size':\n    return Size()\n",
    )
    first = tree.digest()
    tree.write("pkg/kinds.py", "class Size:\n    n: str\n")
    assert tree.digest() != first


def test_a_cycle_of_declarations_still_carries_a_change_across_it(tree: _Tree) -> None:
    """宣言が輪になっても(a.used の署名が b.Size を読み、b.Size の method が a.used を呼ぶ)、輪の中の宣言の変更は使い手へ伝わり、輪の中の
    関数の本体だけの変更は伝わらない。"""
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py", "from pkg.kinds import Size\n\ndef used(x: 'Size') -> int:\n    return 1\n"
    )
    tree.write(
        "pkg/kinds.py",
        "from pkg.impl import used\n\nclass Size:\n    n: int\n\n    def twice(self) -> int:\n        return used(self) * 2\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py", "from pkg.kinds import Size\n\ndef used(x: 'Size') -> int:\n    return 2\n"
    )
    assert tree.digest() == first
    tree.write(
        "pkg/kinds.py",
        "from pkg.impl import used\n\nclass Size:\n    n: str\n\n    def twice(self) -> int:\n        return used(self) * 2\n",
    )
    assert tree.digest() != first


def test_a_re_export_through_a_package_init_reaches_the_declaration(tree: _Tree) -> None:
    """package の __init__ が名を写す形(from .impl import used)でも、写した先の宣言に依る。__init__ の別の名は伝えない。"""
    tree.target = "from pkg import used\n\ndef go() -> int:\n    return used(1)\n"
    tree.write("pkg/__init__.py", "from .impl import used\nfrom .impl import other\n")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef other() -> int:\n    return 1\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef other() -> str:\n    return ''\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: str) -> int:\n    return 0\n\ndef other() -> str:\n    return ''\n",
    )
    assert tree.digest() != first


def test_a_new_file_that_resolves_an_import_changes_the_inputs(tree: _Tree) -> None:
    """解けなかった import を解く file を足せば入力が変わる(前の回の「解けない」の赤を運ばない)。"""
    tree.write("pkg/__init__.py", "")
    first = tree.digest()
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    assert tree.digest() != first


def test_a_py_typed_marker_changes_the_inputs(tree: _Tree) -> None:
    """package の py.typed(pyright の読み方を変える)を足せば入力が変わる。"""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    first = tree.digest()
    tree.write("pkg/py.typed", "")
    assert tree.digest() != first


def test_a_star_import_depends_on_the_whole_module(tree: _Tree) -> None:
    """star import は star の module の全部の名に依る。"""
    tree.target = "from pkg.impl import *\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n",
    )
    assert tree.digest() != first


def test_a_module_used_as_an_attribute_chain_depends_on_the_attribute_only(tree: _Tree) -> None:
    """`import pkg.impl as m` の m.used は used の宣言だけに依る。"""
    tree.target = "import pkg.impl as m\n\ndef go() -> int:\n    return m.used(1)\n"
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused() -> int:\n    return 1\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused() -> str:\n    return ''\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> str:\n    return ''\n\ndef unused() -> str:\n    return ''\n",
    )
    assert tree.digest() != first


def test_another_name_in_all_does_not_change_the_inputs_but_the_used_name_does(tree: _Tree) -> None:
    """__all__ に別の名を足しても使う名の入力は変わらず、使う名を __all__ から外せば変わる(名ごとに在否だけを見る)。"""
    body = "\n\ndef used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "__all__ = ['used']" + body)
    first = tree.digest()
    tree.write("pkg/impl.py", "__all__ = ['used']\n__all__ += ['more']" + body)
    assert tree.digest() == first
    tree.write("pkg/impl.py", "__all__ = ['more']" + body)
    assert tree.digest() != first


def test_an_all_pyright_cannot_read_changes_the_inputs_of_every_name(tree: _Tree) -> None:
    """__all__ を pyright の読めない形(式で作る)で書く module は、__all__ の文の変更を全部の名の使い手へ伝える。"""
    body = "\n\ndef used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "__all__ = ['used'] + []" + body)
    first = tree.digest()
    tree.write("pkg/impl.py", "__all__ = ['used'] + ['more']" + body)
    assert tree.digest() != first


def test_the_same_tree_in_another_place_has_the_same_inputs(tmp_path: Path) -> None:
    """日次の回は task ごとの作業 dir の下に根を置き、doeff の pin の checkout を根の隣に置く(回ごとに path が違う)— 同じ木と隣を別の
    置き場で測っても入力は同じ(違えば毎回全部を当てる)。"""
    digests = []
    for run in ("task_t1", "task_t2"):
        parent = tmp_path / run / "worktrees"
        tree = _Tree(
            parent / f"repo-{run}",
            "from pkg.impl import used\nfrom shared import helper\n\ndef go() -> int:\n    return used(helper())\n",
        )
        tree.write("pkg/__init__.py", "")
        tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
        (parent / "lib").mkdir()
        (parent / "lib" / "shared.py").write_text(
            "def helper() -> int:\n    return 1\n", encoding="utf-8"
        )
        digests.append(tree.digest(searched=(parent / "lib",)))
    assert digests[0] == digests[1]


def test_places_spell_the_root_and_its_parent_but_not_a_longer_name(tmp_path: Path) -> None:
    """置き場の印は、綴りの後ろが path の続きか区切りの所だけに当てる(根の名を頭に持つ別の dir は根の親の下の dir)。"""
    root = tmp_path / "worktrees" / "repo-a"
    text = json.dumps(
        [
            str(root / "x.py"),
            str(root.parent / "doeff" / "src"),
            str(root),
            f"{root}2/y.py",
            "/elsewhere/z",
        ]
    )
    assert json.loads(places_of(root).spell(text)) == [
        "@root/x.py",
        "@parent/doeff/src",
        "@root",
        "@parent/repo-a2/y.py",
        "/elsewhere/z",
    ]


def test_an_unreadable_module_makes_every_user_checked_every_run(tree: _Tree) -> None:
    """構文の誤りで読めない module を使う file は、回ごとに違う digest(毎回当てる — 使う名を割れないので)。"""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int\n")
    assert tree.digest() != tree.digest()


# --- 入口(fake の pyright)-------------------------------------------------------------------------------------------------


def _fake_pyright(tmp_path: Path, log: Path) -> Path:
    """`--version` に版を答え、検める file ごとに「seen <名>」の赤を 1 つ返す pyright の偽物(受けた file の名を log に書く)。"""
    script = tmp_path / "fake-pyright.py"
    script.write_text(
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('pyright 1.1.414')\n"
        "    raise SystemExit(0)\n"
        "targets = [a for a in sys.argv[1:] if a.endswith('.py')]\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as out:\n"
        "    out.write(' '.join(sorted(t.rsplit('/', 1)[-1] for t in targets)) + '\\n')\n"
        "diagnostics = [{'file': t, 'severity': 'error', 'rule': 'fake', 'message': 'seen ' + t.rsplit('/', 1)[-1],\n"
        "                'range': {'start': {'line': 0, 'character': 0}}} for t in targets]\n"
        "print(json.dumps({'generalDiagnostics': diagnostics}))\n",
        encoding="utf-8",
    )
    pyright = tmp_path / "fake-pyright"
    pyright.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n',
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    return pyright


def _module(body: str) -> str:
    """defk の .hy の module(defk と <- を require する)。"""
    return f"(require doeff-hy.macros [defk <-])\n{body}\n"


def _run(root: Path, *extra: str) -> tuple[int, list[dict[str, object]], str]:  # noqa: DOEFF006 — 検の補助の戻り
    """入口の main を 1 度当て、終わりの code・JSON の診断・標準エラーを返す。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = static_check.main(
            ["--root", str(root), "--json", "--no-cache", *extra, str(root / "pkg")]
        )
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else []), err.getvalue()


@pytest.fixture
def entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """機体の検の枠を取らない入口の根(pkg の下に .hy 3 本 — user が base の width を使う・alone は誰も使わない)。"""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main は根を sys.path の頭に足す
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld())
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.hy").write_text("", encoding="utf-8")
    (root / "pkg" / "base.hy").write_text(
        _module(
            '(defk width [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  "幅"\n  (len text))'
        ),
        encoding="utf-8",
    )
    (root / "pkg" / "user.hy").write_text(
        _module(
            "(import pkg.base [width])\n(defk shout [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- n (width text))\n  n)"
        ),
        encoding="utf-8",
    )
    (root / "pkg" / "alone.hy").write_text(
        _module("(defk twice [n]\n  {:pre [(: n int)] :post [(: % int)]}\n  (* n 2))"),
        encoding="utf-8",
    )
    return root


def _pyright_errors(diagnostics: list[dict[str, object]]) -> list[tuple[str, str]]:
    """偽物の赤(規則 fake)だけを (path, 文言) で。"""
    return sorted((str(d["path"]), str(d["message"])) for d in diagnostics if d["rule"] == "fake")


def test_the_second_run_checks_only_changed_files_and_carries_the_rest(
    entry: Path, tmp_path: Path
) -> None:
    """2 回目は入力の変わった file だけを pyright に渡し、運んだ file の診断は前の回のまま出力に入る(全部を当てた答えと同じ形)。"""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    common = ("--pyright", str(pyright), "--carry", str(carry), "--carry-refresh-runs", "0")
    code, first, err = _run(entry, *common)
    assert code == 1, err
    assert log.read_text(encoding="utf-8").splitlines() == ["__init__.py alone.py base.py user.py"]
    assert "pyright に当てた .hy 4 本・前の回の診断を運んだ .hy 0 本" in err
    # 本体だけを替える(width の型は同じ)— base だけを当て、user と alone は運ぶ。
    base = entry / "pkg" / "base.hy"
    base.write_text(
        base.read_text(encoding="utf-8").replace("(len text))", "(len (.strip text)))"),
        encoding="utf-8",
    )
    code, second, err = _run(entry, *common)
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py", err
    assert "pyright に当てた .hy 1 本・前の回の診断を運んだ .hy 3 本" in err
    assert _pyright_errors(second) == _pyright_errors(first)
    # 使う名の署名を替える — base と user を当てる。
    base.write_text(
        base.read_text(encoding="utf-8").replace(":post [(: % int)]", ":post [(: % str)]"),
        encoding="utf-8",
    )
    _run(entry, *common)
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py user.py"
    summary = json.loads(carry.read_text(encoding="utf-8"))["summary"]
    assert (summary["checked"], summary["carried"], summary["reasons"]["changed"]) == (2, 2, 2)


def test_groups_check_the_same_files_and_a_cut_run_keeps_the_finished_groups(
    entry: Path, tmp_path: Path
) -> None:
    """--carry-group は当てる file を組に分けて pyright に当て、答えは分けない時と同じ。2 つ目の組で pyright が倒れても、運びの file は
    1 つ目の組の診断を持ち、次の回はまだ当てていない file だけを当てる。"""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    common = ("--pyright", str(pyright), "--carry", str(carry), "--carry-refresh-runs", "0")
    _, whole, _ = _run(entry, *common)
    carry.unlink()
    code, grouped, err = _run(entry, *common, "--carry-group", "2")
    assert code == 1, err
    assert log.read_text(encoding="utf-8").splitlines()[-2:] == ["__init__.py alone.py", "base.py user.py"]
    assert _pyright_errors(grouped) == _pyright_errors(whole)
    assert "pyright に当てた .hy 4 本" in err
    # 2 つ目の組で倒れる pyright — 1 つ目の組の 2 本だけが運びの file に残る。
    carry.unlink()
    broken = tmp_path / "broken-pyright"
    broken.write_text(
        "#!/bin/sh\n"
        f"if [ -e {shlex.quote(str(tmp_path / 'once'))} ] && [ \"$1\" != --version ]; then echo broken; exit 3; fi\n"
        f"[ \"$1\" != --version ] && touch {shlex.quote(str(tmp_path / 'once'))}\n"
        f'exec {shlex.quote(str(pyright))} "$@"\n',
        encoding="utf-8",
    )
    broken.chmod(0o755)
    code, _, err = _run(
        entry, "--pyright", str(broken), "--carry", str(carry), "--carry-refresh-runs", "0", "--carry-group", "2"
    )
    assert code == 2
    assert "pyright の出力を読めない" in err
    kept = read_carry(carry)
    assert kept is not None
    assert sorted(f.path for f in kept.files) == ["pkg/__init__.hy", "pkg/alone.hy"]
    _run(entry, *common, "--carry-group", "2")
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py user.py"


def test_an_unreadable_carry_file_checks_everything_and_is_replaced(
    entry: Path, tmp_path: Path
) -> None:
    """運びの file が読めなければ名乗って全部を当て(測りを止めない)、この回の運びで置き換える。"""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    carry.write_text("{ 壊れた", encoding="utf-8")
    code, diagnostics, err = _run(entry, "--pyright", str(pyright), "--carry", str(carry))
    assert code == 1
    assert "JSON でない" in err
    assert len(_pyright_errors(diagnostics)) == 4
    assert read_carry(carry) is not None


# --- 本物の pyright --------------------------------------------------------------------------------------------------------


def _real(root: Path, *extra: str) -> list[dict[str, object]]:
    """本物の pyright で strict に当てた JSON の診断(位置・規則・文言)。"""
    code, diagnostics, err = _run(root, "--strict", *extra)
    assert code in (0, 1), err
    return diagnostics


def _same_as_full(root: Path, carry: Path) -> tuple[int, int]:  # noqa: DOEFF006 — 検の補助の戻り
    """運びの下の答えが、同じ木を全部当てた答えと同じことを確かめ、その回に pyright に当てた数と運んだ数を返す(運ばずに全部を当てても
    答えは同じなので、数で運んだことを確かめる)。"""
    carried = _real(root, "--carry", str(carry), "--carry-refresh-runs", "0")
    assert carried == _real(root)
    summary = json.loads(carry.read_text(encoding="utf-8"))["summary"]
    return summary["checked"], summary["carried"]


@needs_pyright
def test_carried_answers_match_a_full_check_across_changes(entry: Path, tmp_path: Path) -> None:
    """本物の pyright で: 変えない・使う名の本体だけを替える・使う名の答えの型を替えて使い手に赤が出る・その赤を消す — どの回も、運んだ答えが
    同じ木を全部当てた答えと同じ。"""
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (4, 0)  # 初回(全部を当てる — __init__・alone・base・user)
    assert _same_as_full(entry, carry) == (0, 4)  # 変えない(全部を運ぶ)
    base = entry / "pkg" / "base.hy"
    base.write_text(
        base.read_text(encoding="utf-8").replace("(len text))", "(len (.strip text)))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (1, 3)  # base だけ
    base.write_text(
        base.read_text(encoding="utf-8")
        .replace(":post [(: % int)]", ":post [(: % str)]")
        .replace("(len (.strip text)))", "(.strip text))"),
        encoding="utf-8",
    )
    user_errors = [
        d for d in _real(entry) if d["path"] == "pkg/user.hy" and d["severity"] == "error"
    ]
    assert user_errors, "答えの型を str にすると user の shout(int を返す)に赤が出る筈"
    assert _same_as_full(entry, carry) == (2, 2)  # base と、width を使う user
    base.write_text(
        base.read_text(encoding="utf-8")
        .replace(":post [(: % str)]", ":post [(: % int)]")
        .replace("(.strip text))", "(len text))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (2, 2)


@needs_pyright
def test_carried_answers_match_a_full_check_for_a_type_reached_through_a_signature(
    entry: Path, tmp_path: Path
) -> None:
    """本物の pyright で: 使う名の署名が読む別の module の型(record の欄)を替えると、使い手を当て直し、運んだ答えが全部当てた答えと同じ。"""
    pkg = entry / "pkg"
    (pkg / "kinds.hy").write_text(
        '(require doeff-hy.record [defrecord])\n(import dataclasses [dataclass])\n(defrecord Size "大きさ" (#^ int n))\n',
        encoding="utf-8",
    )
    (pkg / "base.hy").write_text(
        _module(
            "(import pkg.kinds [Size])\n(defk measure [text]\n  {:pre [(: text str)] :post [(: % Size)]}\n  (Size :n (len text)))"
        ),
        encoding="utf-8",
    )
    (pkg / "user.hy").write_text(
        _module(
            "(import pkg.base [measure])\n(defk total [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- size (measure text))\n  (. size n))"
        ),
        encoding="utf-8",
    )
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (5, 0)
    (pkg / "kinds.hy").write_text(
        (pkg / "kinds.hy").read_text(encoding="utf-8").replace("#^ int n", "#^ str n"),
        encoding="utf-8",
    )
    assert [d for d in _real(entry) if d["path"] == "pkg/user.hy" and d["severity"] == "error"], (
        "欄 n を str にすると total に赤が出る筈"
    )
    assert _same_as_full(entry, carry) == (
        3,
        2,
    )  # kinds と、Size を署名で読む base と、measure を使う user(__init__・alone は運ぶ)


@needs_pyright
def test_carried_answers_match_a_full_check_when_a_missing_module_appears(
    entry: Path, tmp_path: Path
) -> None:
    """本物の pyright で: 解けなかった import を解く file を足すと、解けない赤を運ばず当て直す。"""
    pkg = entry / "pkg"
    (pkg / "user.hy").write_text(
        _module(
            "(import pkg.later [late])\n(defk shout [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- n (late text))\n  n)"
        ),
        encoding="utf-8",
    )
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (4, 0)
    (pkg / "later.hy").write_text(
        _module("(defk late [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (len text))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (
        2,
        3,
    )  # 足した later と、解けるようになった user(__init__・alone・base は運ぶ)
