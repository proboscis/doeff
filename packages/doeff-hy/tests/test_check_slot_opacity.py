"""doeff_hy.check_slot(M-slot)が、機体の検の枠の正本の方策と持ち物を開かない形の機械の検(設計検証 lt-5KHSMYM9R1HW7AKJBRG32NNF4S)。

守る知識: 正本の方策の中身(欄の名・値)・入れ子の時の幅の規則・env の名は正本の持ち物で、M-slot は知らない。M-slot が正本から
受ける物は、方策(read_policy の答え)と持ち物(take の答え)という **開かない物** と、並走の数(process_width の答え)という整数だけ。

直す前の検(test_the_canon_policy_goes_straight_into_take)は「read_policy の答えは take の 1 つ目の引数にしか現れない」を見た。
守りたい知識より狭く、広かった(card ki-1bfe2c42dce6)。
- 入る向きだけを見た: take の答えから欄を読む実装(`holding.policy.processes_per_slot`)は通った(盲検 B)。
- 正しい形を断った: 方策を名に束ねて正本の公開の口(process_width)へ渡す形は read_policy の答えが take 以外に現れて赤(盲検 A)。

この検は「開かない物の使い道」を文法で決める。開かない物(正本の read_policy・take の答え)が現れてよい位置は次の 4 つだけ:
  1. 正本の宣言した口の呼びの引数 — `module.take(policy, …)`・`module.process_width(policy, holding, …)`。
  2. 解く口の取り出し — `holding.release`(呼ぶか、SlotHeld に預ける。その先の属性は開かない)。
  3. None との比較 — `holding is None`(祖先が枠を持つ時の印で、中は見ない)。
  4. 自分の束縛 — `policy = module.read_policy()`(単純な名への 1 回の代入。別の名への写し・属性や添字への代入は開く道なので断る)。
さらに、この module が正本の module から読んでよい名は宣言した口と例外だけ(`module.Policy`・`module.PROCESSES_ENV` などを断る)。
正本の持ち物の名(宣言の鍵・env の名・旧い幅の関数)は doeff_hy のどの source にも現れない。
並走の数を使う側: 宣言が届かない機体の既定 EXPANDER_LIMIT は、expander_limit(宣言の数)の中でだけ読む — 別の入口(展開の子の pool を
使う新しい道具)が既定を直に引数の既定や定数として使うと、枠を取らずに宣言と無関係な数で子を起こす。

限界(表現できない物): 新しい入口が pool を使うのに枠を取らない(expander_limit に渡す数が無いので書き手が気づく — 検は取り忘れ自体は見ない)。
規則の組(OPENED・MOUTHS)を書き換えれば通る — だが組はこの file の固定で、変えるのは意図した契約の変更。
SlotHeld.let_go を static_check 側で開く道(`slot.let_go.__self__`)と、process_width の答えを使わずに幅を自前で決める形は、この
検は見ない(後者は幅の持ち主が 2 つになる — 設計の限界として design.md に記した)。
"""

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy import check_slot

#: 正本の宣言した口(開かない物を渡してよい呼び)・開かない物を返す口・捕まえてよい例外。固定 — 変えるのは契約の変更。
MOUTHS = frozenset({"read_policy", "take", "process_width"})
OPENED = frozenset({"read_policy", "take"})
ERRORS = frozenset({"PolicyUnreadable", "SlotWaitExpired"})
#: 正本が持ち、doeff が持たない名(宣言の鍵・方策の欄・env の名・旧い幅の関数)。検の写しで、正本の変更に追随して足す。
CANON_OWNED = frozenset(
    {
        "processesPerSlot",
        "processes_per_slot",
        "waitLimitSeconds",
        "wait_limit_s",
        "AI_CHECK_SLOT_PROCESSES",
        "AI_CHECK_SLOT_HOLDER",
        "PROCESSES_ENV",
        "HOLDER_ENV",
        "width_for_child",
    }
)
SOURCE = inspect.getsource(check_slot)
PACKAGE = Path(inspect.getfile(check_slot)).parent


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _canon_modules(tree: ast.AST) -> frozenset[str]:
    """正本の module を指す名 — `canon_module(…)` の答えを受ける名と、`ModuleType` で注釈した引数。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "canon_module"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if isinstance(node, ast.arg) and node.annotation is not None and "ModuleType" in ast.unparse(node.annotation):
            names.add(node.arg)
    return frozenset(names)


def _mouth_call(node: ast.AST, modules: frozenset[str], names: frozenset[str]) -> bool:
    """`module.<names のどれか>(…)` の呼びか。"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in modules
        and node.func.attr in names
    )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _argument_of(node: ast.AST, call: ast.AST) -> bool:
    return isinstance(call, ast.Call) and (
        node in call.args or any(node is keyword.value for keyword in call.keywords)
    )


def _release_use_is_closed(release: ast.Attribute, parents: dict[ast.AST, ast.AST]) -> bool:
    """`holding.release` の使い道: 呼ぶ・None との分岐の枝・SlotHeld への預け・単純な名への代入。その先の属性・添字は開く道。"""
    parent = parents[release]
    if isinstance(parent, ast.Call):
        return parent.func is release or (
            _argument_of(release, parent) and isinstance(parent.func, ast.Name) and parent.func.id == "SlotHeld"
        )
    if isinstance(parent, ast.IfExp):
        return True
    return isinstance(parent, ast.Assign) and all(isinstance(target, ast.Name) for target in parent.targets)


def _name_findings(tree: ast.AST, modules: frozenset[str]) -> Iterator[str]:
    """正本の module から読む名は、宣言した口と例外だけ。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            if node.attr not in MOUTHS | ERRORS:
                yield f"{node.lineno}: 正本の {node.attr} は宣言した口でも例外でもない"


def _bound_names(tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str]) -> frozenset[str]:
    """開かない物を返す口の答えを受ける名(単純な名への 1 回の代入の左辺)。"""
    calls = (node for node in ast.walk(tree) if _mouth_call(node, modules, OPENED))
    assigns = (parents[call] for call in calls)
    return frozenset(
        parent.targets[0].id
        for parent in assigns
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name)
    )


def _answer_findings(tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str]) -> Iterator[str]:
    """開かない物を返す口の呼びは、単純な名への代入の右辺か、宣言した口の引数のどちらか。"""
    for node in ast.walk(tree):
        if not _mouth_call(node, modules, OPENED) or not isinstance(node, ast.Call):
            continue
        parent = parents[node]
        named = isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name)
        if not (named or (_mouth_call(parent, modules, MOUTHS) and _argument_of(node, parent))):
            yield f"{node.lineno}: 正本の {ast.unparse(node.func)} の答えを名に束ねず他へ使っている"


def _compare_with_none(node: ast.Name, parent: ast.AST) -> bool:
    return (
        isinstance(parent, ast.Compare)
        and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops)
        and all(_is_none(other) for other in (parent.left, *parent.comparators) if other is not node)
    )


def _use_findings(
    tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str], bound: frozenset[str]
) -> Iterator[str]:
    """束ねた名の使い道は、頭の註の 4 つの位置だけ。"""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in bound and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents[node]
        if _mouth_call(parent, modules, MOUTHS) and _argument_of(node, parent):
            continue
        if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr == "release":
            if not _release_use_is_closed(parent, parents):
                yield f"{node.lineno}: {node.id}.release の先を開いている"
            continue
        if _compare_with_none(node, parent):
            continue
        yield f"{node.lineno}: 正本の答え {node.id} を {type(parent).__name__} の中で開いている({ast.unparse(parent)})"


def opened_uses(source: str) -> list[str]:
    """正本の開かない物(read_policy・take の答え)と、正本の module の使い道の違反(行つき・空 = 閉じている)。"""
    tree = ast.parse(source)
    parents = _parents(tree)
    modules = _canon_modules(tree)
    if not modules:
        return ["正本の module を受ける名(canon_module の答え)が見つからない — 検が何も見ていない"]
    bound = _bound_names(tree, parents, modules)
    return [
        *_name_findings(tree, modules),
        *_answer_findings(tree, parents, modules),
        *_use_findings(tree, parents, modules, bound),
    ]


def _identifiers_and_strings(source: str) -> Iterator[tuple[int, str]]:
    """code の中の名(識別子・属性・引数・定義の名)と、docstring でない文字列の定数(行つき)。コメントと docstring は含めない。"""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg
        elif isinstance(node, ast.alias):
            yield getattr(node, "lineno", 0), node.name.rpartition(".")[2]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.lineno, node.name
        elif isinstance(node, ast.keyword) and node.arg is not None:
            yield getattr(node, "lineno", 0), node.arg
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            for owned in CANON_OWNED:
                if owned in node.value:
                    yield node.lineno, owned


def default_limit_uses(source: str) -> list[str]:
    """既定の展開の子の数 EXPANDER_LIMIT を読む場所(行と、囲む関数の名)。定義(module の段の代入)は数えない。"""
    tree = ast.parse(source)
    parents = _parents(tree)

    def enclosing(node: ast.AST) -> str:
        cursor: ast.AST | None = parents.get(node)
        while cursor is not None and not isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cursor = parents.get(cursor)
        return "<module>" if cursor is None else cursor.name

    reads = (
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "EXPANDER_LIMIT" and isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.Attribute) and node.attr == "EXPANDER_LIMIT")
        or (isinstance(node, ast.alias) and node.name.rpartition(".")[2] == "EXPANDER_LIMIT")
    )
    return [f"{getattr(node, 'lineno', 0)}: {enclosing(node)}" for node in reads]


def canon_owned_names(source: str) -> list[str]:
    """正本が持つ名が code に現れる行(空 = 現れない)。"""
    return [f"{line}: {name}" for line, name in _identifiers_and_strings(source) if name in CANON_OWNED]


# ─────────────────────────────── 実物

#: 既定の展開の子の数を読んでよい場所(static_check.py の関数 expander_limit だけ)。
DEFAULT_LIMIT_READERS = {"static_check.py": ["expander_limit"]}

def test_the_mouths_this_module_declares_are_the_ones_this_guard_allows() -> None:
    """宣言した口の組は、この検の固定の組と同じ — 口を足す・替えるのは意図した契約の変更で、この file の組と一緒に変える。"""
    assert frozenset(check_slot.CANON_CALLS) | frozenset(check_slot.CANON_OPTIONAL_CALLS) == MOUTHS
    assert frozenset(check_slot.CANON_ERRORS) == ERRORS


def test_check_slot_opens_neither_the_canon_policy_nor_what_take_returns() -> None:
    assert opened_uses(SOURCE) == []


def test_the_default_child_count_is_read_only_where_the_slot_width_may_be_missing() -> None:
    """宣言が届かない機体の既定 EXPANDER_LIMIT は、expander_limit(宣言の数)の中でだけ読まれる — 関数の引数の既定・別の道具の
    定数・直の import は、宣言と無関係な数で展開の子を起こす 2 つ目の持ち主になる。"""
    found = {
        path.name: [hit.partition(": ")[2] for hit in default_limit_uses(path.read_text(encoding="utf-8"))]
        for path in sorted(PACKAGE.rglob("*.py"))
    }
    readers = {name: functions for name, functions in found.items() if functions}
    assert readers == DEFAULT_LIMIT_READERS, readers


def test_no_doeff_hy_source_names_what_the_canon_owns() -> None:
    """宣言の鍵・方策の欄・env の名・旧い幅の関数は、doeff_hy の code(docstring・コメントを除く)に現れない。.hy は行の文字列で見る。"""
    in_python = [
        f"{path.name}:{hit}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for hit in canon_owned_names(path.read_text(encoding="utf-8"))
    ]
    in_hy = [
        f"{path.name}:{number}: {name}"
        for path in sorted(PACKAGE.rglob("*.hy"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith(";")
        for name in sorted(CANON_OWNED)
        if name in line
    ]
    assert in_python + in_hy == []


# ─────────────────────────────── 検の検(正常例と失敗例)— 検が実際に拾い、正しい形を通す事

#: 正しい形の最小(M-slot の骨)。開かない物は口へそのまま渡し、解く口だけを預け、幅は process_width の答えを受ける。
OPAQUE_ONLY = '''
def take_slot(home, label, out):
    module = canon_module(home, out)
    environ = EnvironmentByName()
    try:
        policy = module.read_policy()
        holding = module.take(policy, label, environ, out)
    except module.PolicyUnreadable as error:
        return SlotHeld()
    except module.SlotWaitExpired as error:
        return SlotExpired(str(error))
    let_go = None if holding is None else holding.release
    width = module.process_width(policy, holding, environ)
    return SlotHeld(let_go, width)
'''

#: 正しい別の形(盲検 A): 方策を束ねず、正本の公開の口の引数の中で作る。直す前の検の形(read_policy の答えは take の引数)と、
#: 名に束ねて process_width にも渡す形(骨)の両方が通る。
OPAQUE_ONLY_INLINE = OPAQUE_ONLY.replace(
    "policy = module.read_policy()\n        holding = module.take(policy, label, environ, out)",
    "holding = module.take(module.read_policy(), label, environ, out)",
).replace("module.process_width(policy, holding, environ)", "module.process_width(module.read_policy(), holding, environ)")


def test_the_guard_passes_the_opaque_forms() -> None:
    assert "policy = module.read_policy()" in OPAQUE_ONLY
    assert OPAQUE_ONLY_INLINE != OPAQUE_ONLY and "policy = module.read_policy()" not in OPAQUE_ONLY_INLINE
    assert opened_uses(OPAQUE_ONLY) == []
    assert opened_uses(OPAQUE_ONLY_INLINE) == []


def test_the_guard_looks_at_something() -> None:
    """canon_module の答えを受ける名が無い source は『何も見ていない』と断る(検が空振りで緑にならない)。"""
    assert opened_uses("def f():\n    return 1\n") != []


#: 失敗例: 骨の 1 か所を、通常の実装者が機能を実現しようとして書きそうな形へ替える。(名, 替える前, 替えた後, 見つけるべき違反の語)
OPENING_MUTANTS = [
    # 盲検 B: take の答えから方策の欄を読んで幅を決める。
    (
        "read-the-field-from-the-holding",
        "width = module.process_width(policy, holding, environ)",
        "width = holding.policy.processes_per_slot",
        "holding を Attribute の中で開いている",
    ),
    (
        "read-the-field-from-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = policy.processes_per_slot",
        "policy を Attribute の中で開いている",
    ),
    (
        "getattr-the-field",
        "width = module.process_width(policy, holding, environ)",
        "width = getattr(holding, 'policy')",
        "holding を Call の中で開いている",
    ),
    (
        "dump-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = vars(policy)",
        "policy を Call の中で開いている",
    ),
    (
        "rebuild-the-policy",
        "holding = module.take(policy, label, environ, out)",
        "holding = module.take(dataclasses.replace(policy, wait_limit_s=1.0), label, environ, out)",
        "policy を Call の中で開いている",
    ),
    (
        "alias-the-policy",
        "holding = module.take(policy, label, environ, out)",
        "spare = policy\n        holding = module.take(spare, label, environ, out)",
        "policy を Assign の中で開いている",
    ),
    (
        "keep-the-holding-on-the-answer",
        "return SlotHeld(let_go, width)",
        "return SlotHeld(holding, width)",
        "holding を Call の中で開いている",
    ),
    (
        "open-through-the-release",
        "let_go = None if holding is None else holding.release",
        "let_go = holding.release.__self__.policy",
        "holding.release の先を開いている",
    ),
    (
        "format-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = f'{policy}'",
        "policy を FormattedValue の中で開いている",
    ),
    (
        "unpack-into-a-tuple",
        "policy = module.read_policy()",
        "policy, spare = module.read_policy(), 0",
        "答えを名に束ねず他へ使っている",
    ),
    (
        "use-the-answer-inline-elsewhere",
        "let_go = None if holding is None else holding.release",
        "let_go = module.take(module.read_policy(), label, environ, out).release",
        "答えを名に束ねず他へ使っている",
    ),
    # 正本の module から宣言した口以外を読む。
    (
        "read-a-canon-constant",
        "width = module.process_width(policy, holding, environ)",
        "width = int(environ.get(module.PROCESSES_ENV, '2'))",
        "正本の PROCESSES_ENV は宣言した口でも例外でもない",
    ),
    (
        "call-the-old-width-function",
        "width = module.process_width(policy, holding, environ)",
        "width = int(module.width_for_child(policy, holding is None, environ.get('X')))",
        "正本の width_for_child は宣言した口でも例外でもない",
    ),
]


@pytest.mark.parametrize(("name", "before", "after", "expected"), OPENING_MUTANTS, ids=[m[0] for m in OPENING_MUTANTS])
def test_the_guard_refuses_each_way_of_opening_what_the_canon_gave(
    name: str, before: str, after: str, expected: str
) -> None:
    assert before in OPAQUE_ONLY, f"{name}: 骨が変わった(替える前の行が無い)"
    found = opened_uses(OPAQUE_ONLY.replace(before, after))
    assert any(expected in line for line in found), (name, found)


def test_the_guard_refuses_the_blind_b_patch_as_it_was_written() -> None:
    """盲検 B の差分(設計検証 lt-W8F3HD41FWBR4PS26EDSEJDJMT)の核: 持ち物の Protocol に policy を足し、SlotHeld が持ち物から
    processes_per_slot を読んで幅を決める。持ち物の欄の名は code の名として現れるので、正本の名の検が拒む。"""
    patch = '''
class SlotPolicy(Protocol):
    processes_per_slot: int


class Holding(Protocol):
    policy: SlotPolicy

    def release(self) -> None: ...


class SlotHeld:
    def width(self, wanted):
        return max(1, min(wanted, self.holding.policy.processes_per_slot))
'''
    assert canon_owned_names(patch) != []


@pytest.mark.parametrize(
    "written",
    [
        "environ = EnvironmentByName()\nwidth = int(environ['AI_CHECK_SLOT_PROCESSES'])",
        "limit = declaration['processesPerSlot']",
        "from machine_check_slots import width_for_child",
        "slots = policy.processes_per_slot",
    ],
)
def test_the_name_guard_catches_what_the_canon_owns_in_any_code_position(written: str) -> None:
    assert canon_owned_names(written) != [], written


def test_the_name_guard_lets_prose_talk_about_the_canon() -> None:
    prose = '"""正本の processesPerSlot と AI_CHECK_SLOT_PROCESSES の話(docstring)。"""\n# processes_per_slot の話(コメント)\nx = 1\n'
    assert canon_owned_names(prose) == []


@pytest.mark.parametrize(
    ("written", "reader"),
    [
        # 別の道具(型の宣言の一致の検)が展開の子の pool を使う実装 — 引数の既定として既定の数を読む。
        (
            "def project_each(root, roots, sources, limit: int = EXPANDER_LIMIT):\n    return _Expanders(limit)\n",
            "project_each",
        ),
        ("from doeff_hy.static_check import EXPANDER_LIMIT\n", "<module>"),
        ("def stale(root):\n    return _Expanders(static_check.EXPANDER_LIMIT)\n", "stale"),
        ("def stale(root):\n    return _Expanders(EXPANDER_LIMIT)\n", "stale"),
    ],
)
def test_the_default_child_count_guard_catches_a_second_reader(written: str, reader: str) -> None:
    assert [hit.partition(": ")[2] for hit in default_limit_uses(written)] == [reader], written


def test_the_default_child_count_guard_accepts_the_one_reader_and_the_definition() -> None:
    allowed = "EXPANDER_LIMIT = 4\n\n\ndef expander_limit(declared):\n    return EXPANDER_LIMIT if declared is None else declared\n"
    assert [hit.partition(": ")[2] for hit in default_limit_uses(allowed)] == ["expander_limit"]
