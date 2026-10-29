"""handler の節の操作(resume・transfer・finish・reperform・pass・raise)の名と性質が、表 1 つ(doeff_hy/clause_ops.hy の
CLAUSE-OPS)だけに在る事の検(card ki-b4c015d8ba1f・設計検証 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の盲検 A)。

前は名の一覧が handle.hy の `_terminates` と clause_endings.hy の `ENDING-OPS`・`RESUMING-OPS` に写しで在った。新しい操作
(例 `(throw 例外)` → TransferThrow)を `_terminates` と書き換えにだけ足すと、`(Raise [reason] (throw …))` を展開・handle の
評価・実行のどの検めも止めず、Raise の続きが走って答え 42 が返った。型検査のための展開でも TransferThrow は import されずに
読まれた。

- 表に行を足すと、節を終えるかの検め・書き換え・node の import(実行時と型検査のための展開)・Raise / Absent の節の再開の
  検め(展開の時と handle の評価の時)が、どれも足した操作に追従する。resume と同じ扱いの操作(続きの後に節へ戻る・末尾では
  別の操作に替える)も、表の行(returns・tail)だけで足せる。表から行を消すと、どの読み手からも操作でなくなる。
- 表のどの行も、行の終わり方(Ending)どおりに Raise / Absent / 普通の effect の節で照らされる。
- handle.hy と clause_endings.hy に書いた名のうち表に無い名は、どの読み手も節の操作として扱わない。doeff_hy のどの module も
  表の外に操作の名を書かない — 表を介さずに読み手へ名を足すと赤(設計検証 lt-BV79NHMNKA6E42N4TQ1M638MDR の盲検 A・B)。
"""

import ast
import contextlib
import re
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # 拡張を登録する(_READERS は置き場の __file__ を読む)
import hy
import pytest
from doeff_hy import clause_endings, clause_ops, expansion_names, handle
from doeff_hy._static_view import static_view
from doeff_hy.clause_ops import ClauseOp, Ending
from doeff_hy.expansion_names import alias, alias_symbol, continuation_symbol
from hy.models import Expression, Object, Sequence, String, Symbol

from doeff import run

_HEADER = """
(require doeff-hy.macros [defk <- handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects._effects [Raise Absent])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk guarded [n] {:pre [(: n int)] :post [(: % int)]}
  (try (do (<- y (Bump n)) y) (except [e ValueError] -1)))
(defk raises [] {:pre [] :post [(: % int)]}
  (try (do (<- (Raise "x")) 0) (except [e ValueError] 42)))
"""


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _eval_module(code: str) -> types.ModuleType:
    """Hy の source を新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = _module("handle_clause_ops")
    for form in hy.read_many(_HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _expand(code: str, *, static: bool = False) -> ast.Module:
    """Hy の source を展開して Python の AST にする(評価しない)。static なら型検査のための展開。"""
    mod = _module("handle_clause_ops_expand")
    view = static_view() if static else contextlib.nullcontext()
    with view:
        return hy.compiler.hy_compile(hy.read_many(_HEADER + code), mod, filename="<clause-ops>")


def _expansion_refusal(code: str) -> str:
    """展開した時の誤りの文(通れば空の文字列)。"""
    try:
        _expand(code)
    except hy.errors.HyMacroExpansionError as error:
        return str(error)
    return ""


def _doeff_imports(tree: ast.Module) -> frozenset[str]:
    """展開が doeff から import する名。"""
    return frozenset(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "doeff"
        for alias in node.names
    )


def _read_names(tree: ast.Module) -> frozenset[str]:
    """展開が読む名。"""
    return frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )


# ---------------------------------------------------------------------------
# 表に行を足すと、どの読み手も追従する
# ---------------------------------------------------------------------------


def _rewrite_throw(args: list[Object], recur: Callable[[Object], Object]) -> Object:
    """(throw 例外) → 続きへ例外を投げて handler を外す(末尾)— 盲検 A が足した操作と同じ形。"""
    return Expression(
        [
            Symbol("yield"),
            Expression([alias_symbol("TransferThrow"), continuation_symbol(), recur(args[0])]),
        ]
    )


_THROW = ClauseOp(
    name="throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=False,
    tail=None,
    nodes=("TransferThrow",),
    static_nodes=("TransferThrow",),
    rewrite=_rewrite_throw,
)


@pytest.fixture
def throw_op(monkeypatch: pytest.MonkeyPatch) -> ClauseOp:
    """表に行 throw を足す(表の行だけを足し、読み手は直さない)。"""
    monkeypatch.setattr(clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _THROW))
    # 展開の名の表は閉じている(表に無い名は home が断る) — 足した行の node の置き場も表に足す
    monkeypatch.setitem(expansion_names._HOMES, "TransferThrow", "doeff")
    return _THROW


def test_added_operation_ends_a_clause_and_runs(throw_op: ClauseOp) -> None:
    """節を終えるかの検め・書き換え・実行時の node の import が、表に足した操作に追従する(続きの try が例外を受ける)。"""
    mod = _eval_module(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
          r)
        """
    )
    assert run(mod.subject()) == -1


def test_added_resuming_operation_is_refused_in_a_raise_clause_at_expansion(
    throw_op: ClauseOp,
) -> None:
    """盲検 A の形: Raise の節の throw は展開の時に断られる(前は検めが黙って抜け、Raise の続きが走って 42 が返った)。"""
    refusal = _expansion_refusal(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (raises) (Raise [reason] (throw (ValueError "into-k")))))
          r)
        """
    )
    assert "Raise の節は throw できない" in refusal
    assert "Absent の節は throw できない" in _expansion_refusal(
        '(setv h (handle (raises) (Absent [why] (throw (ValueError "into-k")))))'
    )


def test_added_resuming_operation_is_refused_for_an_aliased_raise_when_handle_is_evaluated(
    throw_op: ClauseOp,
) -> None:
    """名前では Raise と分からない別名の節の throw は、handle を評価した時に effect の型の宣言(NEVER)で断られる。"""
    with pytest.raises(clause_endings.ClauseEndingError, match=r"Resumption\.NEVER.*throw"):
        _eval_module(
            """
            (import doeff_core_effects._effects [Raise :as Failed])
            (setv h (handle (raises) (Failed [reason] (throw (ValueError "into-k")))))
            """
        )


def test_added_operation_node_is_imported_in_both_expansions(throw_op: ClauseOp) -> None:
    """型検査のための展開も、表の static-nodes の node を import してから読む(前は TransferThrow を import せずに読んだ)。
    読む名は `_doeff_` の別名で、書き手の同じ名を隠さない(card ki-16180480129e)。"""
    source = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
      r)
    """
    for static in (False, True):
        tree = _expand(source, static=static)
        assert alias("TransferThrow") in _read_names(tree)
        assert "TransferThrow" not in _read_names(tree)
        assert "TransferThrow" in _doeff_imports(tree)


def _rewrite_resume_throw(args: list[Object], recur: Callable[[Object], Object]) -> Object:
    """(throw 例外) → 続きへ例外を投げ、続きが終わると節へ戻る(handler は残る)— resume と同じ扱い(盲検 A の要求)。"""
    return Expression(
        [
            Symbol("yield"),
            Expression([alias_symbol("ResumeThrow"), continuation_symbol(), recur(args[0])]),
        ]
    )


# resume と transfer の対と同じ形の対: 非末尾の throw は節へ戻り(returns)、末尾の throw は transfer-throw に替える(tail)
_RESUME_THROW = ClauseOp(
    name="throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=True,
    tail="transfer-throw",
    nodes=("ResumeThrow",),
    static_nodes=("ResumeThrow",),
    rewrite=_rewrite_resume_throw,
)
_TRANSFER_THROW = ClauseOp(
    name="transfer-throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=False,
    tail=None,
    nodes=("TransferThrow",),
    static_nodes=("TransferThrow",),
    rewrite=_rewrite_throw,
)


@pytest.fixture
def resume_throw_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    """表に行 throw(節へ戻る・末尾では transfer-throw)と transfer-throw を足す(読み手は直さない)。"""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _RESUME_THROW, _TRANSFER_THROW)
    )
    # 展開の名の表は閉じている(表に無い名は home が断る) — 足した行の node の置き場も表に足す
    monkeypatch.setitem(expansion_names._HOMES, "ResumeThrow", "doeff")
    monkeypatch.setitem(expansion_names._HOMES, "TransferThrow", "doeff")


def test_added_returning_operation_marks_the_clause_resumed(resume_throw_ops: None) -> None:
    """節へ戻る操作の後の「再開した」の印は、行の returns を読む handle.hy が立てる — 行の書き換えは印を知らなくてよい
    (前は印を立てる約束が resume の書き換えの中に在り、それを写さない行は、続きを再開したのに実行の時に誤りで止まった)。"""
    mod = _eval_module(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (guarded 1) (Bump [n] (setv answer (throw (ValueError "boom"))) answer)))
          r)
        """
    )
    assert run(mod.subject()) == -1


def test_added_operation_with_a_tail_is_replaced_at_the_tail(resume_throw_ops: None) -> None:
    """末尾の throw は行の tail(transfer-throw)に替わり handler の frame を残さず、非末尾の throw は ResumeThrow のまま
    (前は末尾の替えが handle.hy に resume の名で直に在り、表に足した操作の末尾は替わらなかった)。"""
    tail = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
      r)
    """
    inner = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (setv answer (throw (ValueError "boom"))) answer)))
      r)
    """
    for static in (False, True):
        tail_names = _read_names(_expand(tail, static=static))
        inner_names = _read_names(_expand(inner, static=static))
        assert alias("TransferThrow") in tail_names
        assert alias("ResumeThrow") not in tail_names
        assert alias("ResumeThrow") in inner_names
        assert alias("TransferThrow") not in inner_names
    assert run(_eval_module(tail).subject()) == -1
    assert "Raise の節は throw できない" in _expansion_refusal(
        '(setv h (handle (raises) (Raise [reason] (throw (ValueError "into-k")))))'
    )


def test_a_removed_operation_is_no_longer_an_operation_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表から行を消すと(pass の退役)、節を終えるかの検め・節が使う操作の集まり・書き換えのどれもその名を操作と扱わない。"""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", tuple(op for op in clause_ops.CLAUSE_OPS if op.name != "pass")
    )
    form = hy.read("(pass)")
    assert not handle._terminates(form)
    assert clause_endings.clause_ops([form]) == frozenset()
    assert handle._rewrite_ops(form) == form
    assert "missing resume" in _expansion_refusal("(setv h (handle (raises) (Bump [n] (pass))))")


# ---------------------------------------------------------------------------
# 表のどの行も、行の終わり方どおりに照らされる
# ---------------------------------------------------------------------------


def _written(op: ClauseOp) -> str:
    """表の行の操作を節に書いた形(書き換えの無い raise は例外 1 つ)。"""
    if op.arity is None:
        return f'({op.name} (ValueError "x"))'
    return "(" + " ".join([op.name, *["0"] * op.arity]) + ")"


_OPS = {op.name: op for op in clause_ops.CLAUSE_OPS}


@pytest.mark.parametrize("op", _OPS.values(), ids=_OPS.keys())
def test_every_operation_in_the_table_is_checked_by_its_ending(op: ClauseOp) -> None:
    form = hy.read(_written(op))
    assert handle._terminates(form)
    assert clause_endings.clause_ops([form]) == frozenset([op.name])
    raise_clause = _expansion_refusal(f"(setv h (handle (raises) (Raise [reason] {_written(op)})))")
    absent_clause = _expansion_refusal(f"(setv h (handle (raises) (Absent [why] {_written(op)})))")
    ordinary_clause = _expansion_refusal(f"(setv h (handle (raises) (Bump [n] {_written(op)})))")
    if op.ending is Ending.RESUMES:
        assert f"Raise の節は {op.name} できない" in raise_clause
        assert f"Absent の節は {op.name} できない" in absent_clause
    else:
        assert raise_clause == absent_clause == ""
    if op.ending is Ending.FINISHES:
        assert ":finish-reason" in ordinary_clause
    else:
        assert ordinary_clause == ""


def test_table_names_are_unique() -> None:
    names = [op.name for op in clause_ops.CLAUSE_OPS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("op", _OPS.values(), ids=_OPS.keys())
def test_table_rows_are_consistent(op: ClauseOp) -> None:
    """末尾で替える先は、同じ引数の数の、節へ戻らない行。書き換えの無い行は節へ戻らず、末尾でも替えない。"""
    if op.tail is not None:
        target = clause_ops.named_op(op.tail)
        assert target is not None
        assert target.arity == op.arity
        assert not target.returns
    if op.rewrite is None:
        assert not op.returns
        assert op.tail is None


# ---------------------------------------------------------------------------
# 読み手に書いた名のうち表に無い名は、節の操作として扱われない
# ---------------------------------------------------------------------------

_PACKAGE = Path(doeff_hy.__file__).parent
_READERS = tuple(_PACKAGE / name for name in ("handle.hy", "clause_endings.hy"))
_NAME = re.compile(r"[a-z][a-z0-9_!?-]*")


def _written_names(path: Path) -> frozenset[str]:
    """source に文字列か quote した記号で書いた、頭の名になりうる綴り — 表を介さずに操作の名を持つ所の候補。"""
    if path.suffix == ".py":
        return frozenset(
            node.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _NAME.fullmatch(node.value)
        )
    found: set[str] = set()

    def walk(form: object) -> None:
        match form:
            case String() if _NAME.fullmatch(str(form)):
                found.add(str(form))
            case Expression() if (
                len(form) == 2 and form[0] == Symbol("quote") and isinstance(form[1], Symbol)
            ):
                found.add(str(form[1]))
            case Sequence():
                for child in form:
                    walk(child)

    for form in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        walk(form)
    return frozenset(found)


_CANDIDATES = sorted(frozenset().union(*(_written_names(path) for path in _READERS)))

# 表の名と同じ綴りだが、節の操作と関係の無い所(module の名・綴り):
# - macros.hy の "raise": Python の文の頭の一覧(式の位置に置けない形の見分け — handler の節と関係が無い)
_UNRELATED_SPELLINGS = frozenset({("macros.hy", "raise")})


def test_no_module_writes_an_operation_name_outside_the_table() -> None:
    """doeff_hy のどの module も、表(clause_ops.hy)の外に操作の名を文字列か quote した記号で書かない。名の集まりを写した読み手
    (盲検 B の「続きを 2 度使う」検め・盲検 A の末尾の替えを名で直に書く形)は、表への追加・退役に追従しないまま他の検を通った。"""
    names = {op.name for op in clause_ops.CLAUSE_OPS}
    written = {
        (path.name, name)
        for path in sorted([*_PACKAGE.glob("*.hy"), *_PACKAGE.glob("*.py")])
        if path.name != "clause_ops.hy"
        for name in _written_names(path) & names
    }
    assert written - _UNRELATED_SPELLINGS == set()
    assert written >= _UNRELATED_SPELLINGS, "理由つきで外した綴りが、もう書かれていない — 行を消す"


def test_readers_name_operations_only_through_the_table() -> None:
    """handle.hy と clause_endings.hy に書いた綴りのうち、表に無い名はどの読み手も節の操作として扱わず、表の名は表の行どおりに
    扱う(盲検 A の差分のように `_terminates`・`_rewrite-ops` へ "throw" を直に書くと、ここで赤になる)。"""
    assert {"if", "cond", "quote"} <= set(_CANDIDATES), "綴りの拾い方が読み手の綴りを拾っていない"
    for name in _CANDIDATES:
        op = clause_ops.named_op(name)
        for count in (0, 1, 2):
            form = Expression([Symbol(name), *[Symbol("x")] * count])
            assert handle._terminates(form) == (op is not None), (name, count)
            assert clause_endings.clause_ops([form]) == (
                frozenset([name]) if op else frozenset()
            ), (name, count)
            rewrites = op is not None and op.rewrite is not None and op.arity == count
            for view in (contextlib.nullcontext(), static_view()):
                with view:
                    rewritten = handle._rewrite_ops(form)
                assert (rewritten != form) == rewrites, (name, count)
