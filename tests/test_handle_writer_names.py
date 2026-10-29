"""handle・defhandler の展開が読む名は、書き手の同じ名の effect を隠さない事の検(card ki-16180480129e・設計検証
lt-CZC8SGS3C6DFDGSGG64YF49DH9 の盲検 A)。

展開は節の操作と handler のために `Resume`・`Transfer`・`Pass`・`WithHandler`(session があれば `Get`・`Put`・`Some`)を
読む。素の名で import すると、書き手が自分の effect を同じ名にした時(振込の `Transfer`・KV の `Put`)、節の頭の
`isinstance` と注記が展開の名を指し、effect が外の handler へ黙って抜けた。module の段では展開の import が書き手の
`Transfer` を VM の `Transfer` で上書きし、`(Transfer 5)` が TypeError になった。

- 書き手が 7 つの名のどれかを自分の effect にした module で、module の段・defk の本体・素の defn の本体・class の本体の
  handle・defhandler の節が、その effect を受けて答える(節の操作 resume・transfer・reperform・:when の外れ・
  session var の := を通す)。
- module の段の handle・defhandler の後も、書き手の module の名は書き手の物のまま。書き手が書いていない名を、展開が
  module の globals に置かない。
- 展開(実行時と型検査のため)は、7 つの名(と型検査のための `typed_resume`・`typed_transfer`)を、素の名で読みも束ねもしない
  — どの置き場でも。
"""

import ast
import builtins
import contextlib
import dataclasses
import sys
import types
from collections.abc import Callable
from typing import NamedTuple

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, state
from doeff_hy._static_view import static_view

from doeff import run

# 展開が読む doeff の名(handle.hy・clause_ops.hy・binding_forms.py・macros.hy の `set!`)。書き手が自分の effect の名に
# しうる名 — 振込の Transfer・KV の Put / Get・値の Some・再開の Resume・外へ渡す Pass・handler を被せる WithHandler。
_NAMES = ("Resume", "Transfer", "Pass", "WithHandler", "Get", "Put", "Some")

# 型検査のための展開だけが読む名(typed_resume / typed_transfer — 節の resume / transfer の型付きの呼び)
_STATIC_ONLY_NAMES = ("typed_resume", "typed_transfer")

_HEADER = """
(require doeff-hy.macros [defk <- handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Other [EffectBase] #^ int x)
"""


def _writer_effect(name: str) -> str:
    """書き手が、展開と同じ名の effect を定義する source。"""
    return f"(defclass [(dataclass :frozen True)] {name} [EffectBase] #^ int n)\n"


def _eval_module(*sources: str) -> types.ModuleType:
    """Hy の source を順に新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = types.ModuleType("handle_writer_names")
    sys.modules["handle_writer_names"] = mod
    for source in sources:
        for form in hy.read_many(source):
            hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run_with_state(program: object) -> object:
    """session var の Get・Put を state の handler が受ける。"""
    return run(scheduled(state(initial={})(program)))


# 書き手の effect {N} を受ける式。どれも答えは 101。
# - tail: 末尾の resume(Transfer になる)
# - pass: 受けない effect(Other)は節の無い所で Pass、:when の外れた節も Pass、外の handler の try の中の resume は
#   Resume のまま
_EXPRESSIONS = {
    "tail": "(handle ({N} 1) ({N} [n] (resume (+ n 100))))",
    "pass": """
        (handle
          (handle
            (handle ({N} 1) (Other [x] (resume x)))
            ({N} [n] :when (> n 5) (resume n)))
          ({N} [n] (try (resume (+ n 100)) (except [ValueError] (transfer 0)))))
        """,
}

# 書き手の effect {N} を受ける defhandler(session var の := は Get・Put・Some を通す)。答えは 101。
_DEFHANDLER = """
(defhandler h (session var calls 99)
  ({N} [n] (:= calls (+ calls 1)) (resume (+ n calls))))
"""
_DEFHANDLER_PROGRAM = "(h ({N} 1))"

_HOSTS = ("module", "defk", "defn", "class")


class _HostedModule(NamedTuple):
    """host に書いた Hy の source と、それを読み込んだ module から答えを出す Program を取る関数。"""

    source: str
    program: Callable[[types.ModuleType], object]


def _expression_module(host: str, expression: str) -> _HostedModule:
    """handle の式 `expression` を host に書いた source と、答えを出す Program の取り方。"""
    match host:
        case "module":
            return _HostedModule(f"(setv program {expression})", lambda mod: mod.program)
        case "defk":
            return _HostedModule(
                f"(defk subject [] {{:pre [] :post [(: % int)]}} (<- r {expression}) r)",
                lambda mod: mod.subject(),
            )
        case "defn":
            return _HostedModule(f"(defn host [] {expression})", lambda mod: mod.host())
        case "class":
            return _HostedModule(f"(defclass C [] (setv program {expression}))", lambda mod: mod.C.program)
        case _:
            raise AssertionError(host)


def _defhandler_module(host: str, name: str) -> _HostedModule:
    """defhandler を host に書いた source と、答えを出す Program の取り方。"""
    definition = _DEFHANDLER.replace("{N}", name)
    program = _DEFHANDLER_PROGRAM.replace("{N}", name)
    match host:
        case "module":
            return _HostedModule(f"{definition}(setv program {program})", lambda mod: mod.program)
        case "defk":
            return _HostedModule(
                f"(defk subject [] {{:pre [] :post [(: % int)]}} {definition} (<- r {program}) r)",
                lambda mod: mod.subject(),
            )
        case "defn":
            return _HostedModule(f"(defn host [] {definition} {program})", lambda mod: mod.host())
        case "class":
            return _HostedModule(f"(defclass C [] {definition})", lambda mod: mod.C.h(getattr(mod, name)(1)))
        case _:
            raise AssertionError(host)


def _scenarios() -> dict[str, tuple[str, str, Callable[[types.ModuleType], object]]]:
    """名 × 置き場 × 書き方ごとの (書き手の effect の名, source, Program の取り方)。"""
    found: dict[str, tuple[str, str, Callable[[types.ModuleType], object]]] = {}
    for name in _NAMES:
        for host in _HOSTS:
            for kind, expression in _EXPRESSIONS.items():
                source, program = _expression_module(host, expression.replace("{N}", name))
                found[f"{name}-{host}-handle-{kind}"] = (name, source, program)
            source, program = _defhandler_module(host, name)
            found[f"{name}-{host}-defhandler-session"] = (name, source, program)
    return found


_SCENARIOS = _scenarios()


@pytest.mark.parametrize(("name", "source", "program"), _SCENARIOS.values(), ids=_SCENARIOS.keys())
def test_clause_answers_the_writers_effect_named_like_an_expansion_name(
    name: str, source: str, program: Callable[[types.ModuleType], object]
) -> None:
    """書き手の module の効果 `{名}` を、handle・defhandler の節が受けて答える — 展開の `{名}` に隠されると節の頭の
    isinstance が VM の node を指し、effect が節を素通りして UnhandledEffect になる(module の段では書き手の名が
    上書きされて `({名} 1)` が TypeError)。"""
    mod = _eval_module(_HEADER, _writer_effect(name), source)
    assert _run_with_state(program(mod)) == 101


@pytest.mark.parametrize("name", _NAMES)
def test_module_level_handle_and_defhandler_leave_the_writers_names_alone(name: str) -> None:
    """module の段の handle・defhandler は、書き手の module の名(`Transfer`・`Pass`・`Put` など)を別の物に束ね直さない。"""
    mod = _eval_module(_HEADER, _writer_effect(name))
    written = mod.__dict__[name]
    source, _ = _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", name))
    for form in hy.read_many(source + _DEFHANDLER.replace("{N}", name)):
        hy.eval(form, mod.__dict__, module=mod)
    assert mod.__dict__[name] is written


def test_module_level_handle_and_defhandler_do_not_put_the_bare_names_in_the_globals() -> None:
    """書き手が書いていない `Resume`・`Transfer`・`Pass`・`WithHandler`・`Get`・`Put`・`Some` を、展開が module の globals に
    置かない(置くと書き手の名と 1 つの名を分け合う)。"""
    mod = _eval_module(
        _HEADER,
        _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", "Other"))[0],
        _DEFHANDLER.replace("{N}", "Other"),
    )
    assert {name for name in _NAMES if name in vars(mod)} == set()


# ---------------------------------------------------------------------------
# 展開の構造 — 実行時の展開と型検査のための展開が、素の名を読みも束ねもしない
# ---------------------------------------------------------------------------

_EXPANSION_HEADER = _HEADER + "(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)\n"

_EXPANSION_SOURCES = {
    "module-handle": "(setv program (handle (Bump 1) (Bump [n] (resume (+ n 100)))))",
    "module-handle-pass": _EXPRESSIONS["pass"].replace("{N}", "Bump").join(["(setv program ", ")"]),
    "module-defhandler": _DEFHANDLER.replace("{N}", "Bump"),
    "defk-handle": (
        "(defk subject [] {:pre [] :post [(: % int)]} (<- r (handle (Bump 1) (Bump [n] (resume n)))) r)"
    ),
    "defn-defhandler": (
        "(defn host [] (defhandler h (session var calls 0) (Bump [n] (:= calls (+ calls 1)) "
        "(reperform effect))) h)"
    ),
    "class-handle": "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (resume n)))))",
    "class-defhandler": _DEFHANDLER.replace("{N}", "Bump").join(["(defclass C [] ", ")"]),
    "legacy-set!": (
        "(defhandler h (lazy-var calls 0) (Bump [n] (set! calls (+ calls 1)) (resume calls)))"
    ),
}


def _expand(source: str, *, static: bool) -> ast.Module:
    """Hy の source を展開して Python の AST にする(評価しない)。static なら型検査のための展開。"""
    mod = types.ModuleType("handle_writer_names_expand")
    sys.modules["handle_writer_names_expand"] = mod
    with static_view() if static else contextlib.nullcontext():
        return hy.compiler.hy_compile(
            hy.read_many(_EXPANSION_HEADER + source), mod, filename="<writer-names>"
        )


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _EXPANSION_SOURCES.values(), ids=_EXPANSION_SOURCES.keys())
def test_expansion_neither_reads_nor_binds_a_bare_expansion_name(source: str, static: bool) -> None:
    """展開は、書き手の名前空間と分け合う素の名を読みも束ねもしない — 読むのは `_doeff_` の頭の別名だけ。書き手が書いた
    source に 7 つの名は無いので、展開の木に素の名が在れば、それは展開の綴りが書き手の名と衝突する名。"""
    tree = _expand(source, static=static)
    watched = {*_NAMES, *_STATIC_ONLY_NAMES}
    read = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    bound = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert (read | bound) & watched == set()


# ---------------------------------------------------------------------------
# 位置 — 型検査のための展開の import の行は、利用者の handler の行(1 行目へ引き寄せない)
# ---------------------------------------------------------------------------

_PADDING = "\n" * 24


def test_static_expansion_imports_keep_the_handlers_line() -> None:
    """型検査のための展開が handler ごとに出す `_doeff_` の別名の import は、利用者の handler の行に置かれる。reader が作る model を
    import に混ぜると、位置を付ける macro が囲む式の範囲を 1 行目へ引き、診断(未使用の import・未定義の名)が 1:1 になる
    (基準版 dc3385b9a は handler の行 — 設計検証 lt-2SJXY98Q643WGJK2ASWJ3N8PWH の盲検 A の実測)。"""
    mod = types.ModuleType("handle_writer_names_position")
    sys.modules[mod.__name__] = mod
    source = _PADDING + _EXPANSION_HEADER + _EXPANSION_SOURCES["module-defhandler"]
    with static_view():
        tree = hy.compiler.hy_compile(hy.read_many(source), mod, filename="<position>")
    first_user_line = len(_PADDING) + 1
    aliased = {
        alias.asname: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None and alias.asname.startswith("_doeff_")
    }
    assert aliased, "展開が別名の import を出していない"
    assert {name: line for name, line in aliased.items() if line < first_user_line} == {}


# ---------------------------------------------------------------------------
# 展開の構造(一般の形)— 名の一覧に依らない。展開が読む・束ねる名は、書き手の物か展開の物(`_doeff_` の頭)のどちらか
# ---------------------------------------------------------------------------

# 展開が module に置く名のうち `_doeff` / `_hy` の頭でない物: Hy の compiler が module ごとに置く `hy` と、定義の宣言の記帳
# (declarations.hy)が import する doeff_hy の module、defk の本体の展開が束ねる補助(macros.hy の `_helper-imports` —
# handle・defhandler の物ではない)。書き手が同じ名を使う事は無いとして、名指しで許す(ここに無い素の名は赤)。
_EXPANSION_OWNED_PLAIN = frozenset(
    {"hy", "doeff_hy", "_install_guard_globals", "_guard_performed", "_guard_statement_value"}
)


def _expansion_owned(name: str) -> bool:
    """展開が自分の物として置く名か(`_doeff` の頭・Hy の gensym の `_hy` の頭・dunder・許した 2 つ)。"""
    return name.startswith(("_doeff", "_hy", "__")) or name in _EXPANSION_OWNED_PLAIN


def _bound_names(tree: ast.AST) -> frozenset[str]:
    """木の中で束縛される名(代入・引数・def / class・import・except の as・global の宣言)。"""
    bound: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                bound.add(name)
            case ast.arg(arg=name):
                bound.add(name)
            case (
                ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.ClassDef(name=name)
            ):
                bound.add(name)
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                bound.update(alias.asname or alias.name.split(".")[0] for alias in aliases)
            case ast.ExceptHandler(name=str(name)):
                bound.add(name)
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                bound.update(names)
    return frozenset(bound)


def _import_bound_names(tree: ast.AST) -> frozenset[str]:
    """木の中の import が束ねる名。"""
    return frozenset(
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    )


def _written_symbols(source: str) -> frozenset[str]:
    """source に書き手が書いた Symbol(Python の綴り)。dotted な綴りは頭の名も入れる。"""
    names: set[str] = set()

    def walk(form: object) -> None:
        if isinstance(form, hy.models.Symbol):
            names.update(hy.mangle(part) for part in str(form).split(".") if part)
        elif isinstance(form, hy.models.Sequence):
            for item in form:
                walk(item)

    for form in hy.read_many(source):
        walk(form)
    return frozenset(names)


def _unowned_reads(tree: ast.Module, source: str) -> frozenset[str]:
    """展開の木が読む名のうち、木の中で束縛されず・組み込みでなく・書き手が書いてもおらず・展開の物でもない名。書き手の名前空間と
    1 つの名を分け合う綴り(素の名)はここに出る。"""
    reads = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return frozenset(
        name
        for name in reads - _bound_names(tree) - _written_symbols(source)
        if not hasattr(builtins, name) and not _expansion_owned(name)
    )


def _unowned_imports(tree: ast.Module, header_tree: ast.Module) -> frozenset[str]:
    """展開の木の import が束ねる名のうち、書き手の header の import でなく・展開の物でもない名。"""
    return frozenset(
        name
        for name in _import_bound_names(tree) - _import_bound_names(header_tree)
        if not _expansion_owned(name)
    )


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _EXPANSION_SOURCES.values(), ids=_EXPANSION_SOURCES.keys())
def test_expansion_reads_and_binds_only_written_or_expansion_owned_names(
    source: str, static: bool
) -> None:
    """名の一覧に依らない形: 展開が読む名は書き手の物か展開の物(`_doeff_` の頭)、束ねる名は展開の物だけ。展開が新しい名(表の行が足す
    node など)を素の綴りで読めば、監視する名の一覧に載っていなくても赤になる(設計検証 lt-2SJXY98Q643WGJK2ASWJ3N8PWH の盲検 A・B が
    指した、手で写した名の一覧の穴)。"""
    tree = _expand(source, static=static)
    header_tree = _expand("", static=static)
    assert _unowned_reads(tree, _EXPANSION_HEADER + source) == frozenset()
    assert _unowned_imports(tree, header_tree) == frozenset()


def test_general_check_catches_a_new_node_read_bare_where_the_name_list_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表の行の書き換えが新しい node を素の Symbol で読む変異(`ResumeBare`)— 9 つの名の一覧は拾えず(一覧に無い名)、一般の形が拾う。"""
    from doeff_hy import _clause_ops as clause_ops
    from doeff_hy import _expansion_names as expansion_names

    def bare_rewrite(
        args: list[hy.models.Object], recur: Callable[[hy.models.Object], hy.models.Object]
    ) -> hy.models.Object:
        # 続きは節の続き `_doeff_k`(card ki-6af9862aed32 — 素の `k` を読むと、この変異でない読みも拾われる)
        return hy.models.Expression(
            [
                hy.models.Symbol("yield"),
                hy.models.Expression(
                    [
                        hy.models.Symbol("ResumeBare"),
                        expansion_names.continuation_symbol(),
                        recur(args[0]),
                    ]
                ),
            ]
        )

    resume = clause_ops.named_op("resume")
    assert resume is not None
    mutant = dataclasses.replace(resume, nodes=("ResumeBare",), rewrite=bare_rewrite)
    # 展開の名の表は閉じている(表に無い名は home が断る) — 変異の node の置き場も表に足す
    monkeypatch.setitem(expansion_names._HOMES, "ResumeBare", "doeff")
    monkeypatch.setattr(
        clause_ops,
        "CLAUSE_OPS",
        tuple(mutant if op.name == "resume" else op for op in clause_ops.CLAUSE_OPS),
    )
    source = "(setv program (handle (Bump 1) (Bump [n] (setv answer (resume n)) answer)))"
    tree = _expand(source, static=False)
    watched = {*_NAMES, *_STATIC_ONLY_NAMES}
    list_based = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    } & watched
    assert list_based == set()
    assert _unowned_reads(tree, _EXPANSION_HEADER + source) == frozenset({"ResumeBare"})


def test_names_the_module_level_expansion_adds_are_written_or_expansion_owned() -> None:
    """module の段の handle・defhandler(session var を含む)が module の globals に足す名は、書き手が書いた名か展開の物だけ
    (R2 の一般の形 — 素の 7 つの名だけを見る検の外の名も)。"""
    source = _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", "Other"))[
        0
    ] + _DEFHANDLER.replace("{N}", "Other")
    mod = _eval_module(_HEADER, source)
    bare = _eval_module(_HEADER)
    added = frozenset(vars(mod)) - frozenset(vars(bare))
    unowned = {
        name
        for name in added
        if not _expansion_owned(name) and name not in _written_symbols(source)
    }
    assert unowned == set()
