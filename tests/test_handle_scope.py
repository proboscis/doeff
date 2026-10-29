"""関数の本体の中に書いた handle・defhandler が、外の関数に局所の名を作らない事の検(card ki-907f779b3290)。

handle と defhandler の実行時の展開は、`_doeff_do`・`_doeff_Resume`・`_doeff_Transfer`・`_doeff_Pass`・`_doeff_WithHandlerType` と
節の終わり方の検めを書いた場所で import する(node の名は書き手の同じ名の effect を隠さない別名 — card ki-16180480129e・
tests/test_handle_writer_names.py)。defk の本体(関数)の中でそれを import すると、名が関数全体の局所の名になり、同じ関数で先に
読む traverse / for/do の `_doeff_do` が UnboundLocalError で落ちていた(fnk・do! の ki-c67428538aec、入れ子の defk の
ki-964823cd7feb と同じ形)。

handle は、import・検め・handler の組み立てをすぐ呼ぶ関数に入れ、本文(第 1 引数)だけを書いた場所で評価する — 本文は
外の do の文脈に属する(`!` の yield は外の生成器の物)。defhandler は defk と同じく `_scoped-definition` で名だけを
外へ束ねる。module の段は今までどおり並べる。

class の本体に直に書いた handle・defhandler(card ki-edab7c8fbb55)は、import が class の属性になり、節の関数から
見えなかった。handle は関数の中と同じくすぐ呼ぶ関数に入れ、defhandler は import を handler を組む関数の中へ移す
(名の定義は class の段に残す)。
"""

import builtins
import dis
import sys
import types
from collections.abc import Callable

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, state, try_handler
from doeff_traverse import fail_handler, sequential
from hy.compiler import hy_compile

from doeff import Program, run

# handle・defhandler の展開が書いた場所で import する名(handle.hy の `_do-import`・`_vm-imports`・`_clause-endings-import`、
# 節の外の効果を素通しする宣言の `_doeff-declare-passes`、defhandler の宣言の `doeff_hy`)
_HANDLE_IMPORT_NAMES = frozenset(
    {
        "_doeff_do",
        "_doeff_Resume",
        "_doeff_Transfer",
        "_doeff_Pass",
        "_doeff_WithHandlerType",
        "_doeff_check_clause_endings",
        "_doeff_check_clause_endings_once",
        "_doeff_declare_passes",
        "doeff_hy",
    }
)

# module の段の defk が在る(書き手の module の普通の形)— 外の関数が先に読む `_doeff_do` は module の globals に在る
_HEADER = """
(require doeff-hy.macros [defk do! <- traverse for/do handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects._effects [Resumption])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk bump-n [n] {:pre [(: n int)] :post [(: % int)]} (<- y (Bump n)) y)
"""


def _eval_module(code: str, header: str = _HEADER) -> types.ModuleType:
    """Hy の source を新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = types.ModuleType("handle_scope")
    sys.modules["handle_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: Program) -> object:
    """handle を含む Program を、traverse / for/do の handler の下で走らせる。"""
    return run(scheduled(try_handler(fail_handler(sequential()(program)))))


_CASES = {
    # 依頼の形: traverse の `_doeff_do` の読みの後に handle が来る
    "handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (sum coll)) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        112,
    ),
    # 本文の `!` は外の defk の do の文脈で走る(すぐ呼ぶ関数に本文を入れると、その関数が生成器になる)
    "handle-body-bang-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (! (do! (len coll)))) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # 走らない枝の handle の後に traverse が来る(import は走らなくても名を局所にする)
    "handle-in-untaken-branch-then-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (when (> (len items) 100) (handle (bump-n 1) (Bump [n] (resume n))))
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (sum coll))
        """,
        12,
    ),
    # 節は let の名と、handle の後で代え直した外の局所の名を、走った時の値で読む
    "handle-clause-reads-outer-names": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (setv step 7)
          (let [base 1000]
            (setv prog (handle (bump-n (sum coll)) (Bump [n] (resume (+ n base step)))))
            (setv step 70)
            (<- r prog)
            r))
        """,
        1082,
    ),
    "nested-handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (handle (bump-n (len coll)) (Bump [n] :when (> n 5) (resume n)))
                  (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # 素の defn の for/do の `_doeff_do` の読みの後に handle が来る
    "handle-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (handle (bump-n (len items)) (Bump [n] (resume (+ n 100)))))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r (make items))
          r)
        """,
        103,
    ),
    # 入れ子の defhandler(defk と同じ `_scoped-definition` の形)
    "defhandler-in-defk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defhandler bump-h (Bump [n] (resume (+ n 100))))
          (<- r (bump-h (bump-n (sum coll))))
          r)
        """,
        112,
    ),
    "defhandler-with-params-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defhandler bump-h [base] (Bump [n] (resume (+ n base))))
          (bump-h 100))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r ((make items) (bump-n (len items))))
          r)
        """,
        103,
    ),
}


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_runs(source: str, expected: object) -> None:
    assert _run(_eval_module(source).subject([1, 2, 3])) == expected


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_outer_function_binds_no_handle_import(source: str, expected: object) -> None:
    """handle・defhandler が import する名は、外の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    mod = _eval_module(source)
    for outer in (mod.subject, getattr(mod, "make", mod.subject)):
        code = getattr(outer, "__doeff_generator_function__", outer).__code__
        assert _HANDLE_IMPORT_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """module の globals の名ごとの (値の id, 入れ物なら要素の数) — 外の関数を呼んで globals に名や記録が増えたかを比べるため。"""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_leaves_module_globals_unchanged(
    source: str, expected: object
) -> None:
    """関数の中の handle・defhandler は、外の関数を何度呼んでも module の globals に名も記録も足さない — 包んだ文の並びは
    呼ぶたびに評価される(`_scoped-definition` の呼び手の負う事)。"""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _module_globals_view(mod) == before


_HOSTS = {
    "handle": ("(defn host [] (handle (bump-n 1) (Bump [n] (resume (+ n 1)))))", []),
    "defhandler": ("(defn host [] (defhandler h (Bump [n] (resume n))) h)", ["h"]),
    "defhandler-with-params": ("(defn host [] (defhandler h [b] (Bump [n] (resume b))) h)", ["h"]),
}


@pytest.mark.parametrize(("source", "names"), _HOSTS.values(), ids=_HOSTS.keys())
def test_host_function_gets_only_its_own_names(source: str, names: list[str]) -> None:
    """handle・defhandler だけを書いた関数は、Hy が作る一意の名(`_hy_` の頭 — 展開の中の関数の名)と定義した名の外に、
    局所の名も閉包の cell も持たない(import する名の一覧に依らない検)。"""
    code = _eval_module(source).host.__code__
    assert [
        name for name in code.co_varnames + code.co_cellvars if not name.startswith("_hy_")
    ] == names


_HALT = """
(defclass [(dataclass :frozen True)] Halt [EffectBase]
  (setv __doeff_resumption__ Resumption.NEVER))
(defn body-evaluated [] (raise (AssertionError "本文が検めより先に評価された")))
"""

# 検めの ClauseEndingError は TypeError の子(clause_endings.hy は .hy なので型検査から引けず、基の型と文言で受ける)
_HALT_RESUMED = r"Halt は再開しない effect\(Resumption\.NEVER\)"

# 本文の形: 素の式と、Hy が本文の文の部分を本文の式より前の文へ出す形(`do` の前の文・let の束ね)
_BODIES = {
    "expression": "(body-evaluated)",
    "statement-before-the-program": "(do (body-evaluated) (bump-n 1))",
    "let-binding": "(let [x (body-evaluated)] (bump-n x))",
}


@pytest.mark.parametrize("body", _BODIES.values(), ids=_BODIES.keys())
def test_handle_checks_clause_endings_before_the_body(body: str) -> None:
    """関数の中の handle も、節の終わり方の検めを本文より前に済ませる — 検めが落ちたら本文は評価されない。本文を先に
    評価すると `body-evaluated` の AssertionError になる(設計検証 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の盲検 A — 被せる関数を
    本文と同じ式の中で作ると、Hy が前へ出した本文の文の部分が検めより先に走った)。"""
    mod = _eval_module(_HALT + f"(defn subject [] (abs 1) (handle {body} (Halt [] (resume 0))))")
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        mod.subject()


def test_handle_checks_clause_endings_before_the_body_performs_an_effect() -> None:
    """defk の本体の handle の本文の `!` は、検めが落ちる時に外の effect を出さない — 出すと Bump の handler が無いので
    UnhandledEffect になる。"""
    mod = _eval_module(
        _HALT
        + """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (let [x (! (bump-n 5))] (bump-n x)) (Halt [] (resume 0))))
          r)
        """
    )
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        _run(mod.subject())


def test_handle_checks_clause_endings_at_every_evaluation() -> None:
    """節の effect の型は評価のたびに閉包から読むので、同じ handle を評価するたびに検める — 前の評価で通った型の記憶で
    後の評価の検めを飛ばすと、再開しない effect を resume する handler が黙って作られる(設計検証
    lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の盲検 B — 検めを書いた場所ごとに 1 回にする変更)。"""
    mod = _eval_module(
        _HALT
        + """
        (defclass [(dataclass :frozen True)] Ping [EffectBase])
        (defn answer-with [etype value program] (handle program (etype [] (resume value))))
        (defk uses-ping [] {:pre [] :post [(: % int)]} (<- x (Ping)) (+ x 1))
        """
    )
    assert run(mod.answer_with(mod.Ping, 41, mod.uses_ping())) == 42
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        mod.answer_with(mod.Halt, 0, mod.uses_ping())


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """module の段の handle・defhandler と、class の本体の defhandler の定義は包まない — 包むと `__qualname__`(pickle の
    名の引き)が変わる。class の本体の defhandler は import だけを handler を組む関数の中へ移す(card ki-edab7c8fbb55)。"""
    mod = _eval_module(
        """
        (defhandler top-h [base] (Bump [n] (resume (+ n base))))
        (defclass Holder [] (defhandler inner-h [base] (Bump [n] (resume (+ n base)))))
        (setv program (handle (bump-n 1) (Bump [n] (resume (+ n 100)))))
        """
    )
    assert mod.top_h.__qualname__ == "top_h"
    assert mod.Holder.inner_h.__qualname__ == "Holder.inner_h"
    assert run(mod.top_h(5)(mod.bump_n(1))) == 6
    assert run(mod.Holder.inner_h(5)(mod.bump_n(1))) == 6
    assert run(mod.program) == 101
    # module の段の handle の import は今までどおり module の globals に置く。handle だけの module で確かめる — 同じ module の
    # defhandler の import も WithHandler を置くので、上の module では handle を包んでも通っていた(設計検証
    # lt-CZC8SGS3C6DFDGSGG64YF49DH9 の S3 の失敗例)
    handle_only = _eval_module("(setv program (handle (bump-n 1) (Bump [n] (resume (+ n 100)))))")
    assert run(handle_only.program) == 101
    assert "_doeff_WithHandlerType" in vars(handle_only)


# class の本体に直に書いた handle・defhandler(card ki-edab7c8fbb55)。class の本体の import は class の属性になり、展開の
# 中で作る関数(節の関数・handler を組む関数・本文に被せる関数)は名を引く時に class の段を飛ばすので見えない — 直す前は
# handle の節の Transfer・Resume・Pass、defhandler の _doeff_do・_doeff_check_clause_endings_once が NameError だった。
# module の段に defk・handle・defhandler が在ると、その import が module の globals に名を置いて穴を隠すので、class 1 つ
# だけの module で読む(本文は effect そのもの)。
_CLASS_ONLY_HEADER = """
(require doeff-hy.handle [handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defclass [(dataclass :frozen True)] Scale [EffectBase] #^ int n)
"""

_CLASS_BODY_CASES = {
    # 末尾の resume は Transfer になる(_tail-resume-to-transfer)
    "handle-transfer": (
        "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (resume (+ n 100))))))",
        lambda mod: mod.C.program,
    ),
    # try の中の resume は Resume のまま
    "handle-resume": (
        """
        (defclass C []
          (setv program
            (handle (Bump 1) (Bump [n] (try (resume (+ n 100)) (except [ValueError] (transfer 0)))))))
        """,
        lambda mod: mod.C.program,
    ),
    # :when の外れた節は Pass で外の handler へ渡す
    "handle-when-guard-pass": (
        """
        (defclass C []
          (setv program
            (handle (handle (Bump 1) (Bump [n] :when (> n 5) (resume n))) (Bump [n] (resume (+ n 100))))))
        """,
        lambda mod: mod.C.program,
    ),
    "defhandler": (
        "(defclass C [] (defhandler h (Bump [n] (resume (+ n 100)))))",
        lambda mod: mod.C.h(mod.Bump(1)),
    ),
    "defhandler-with-params": (
        "(defclass C [] (defhandler h [base] (Bump [n] (resume (+ n base)))))",
        lambda mod: mod.C.h(100)(mod.Bump(1)),
    ),
    # session var の節は Get・Put・Some と session の鍵を読む
    "defhandler-session-var": (
        """
        (defclass C []
          (defhandler h (session var calls 99) (Bump [n] (:= calls (+ calls 1)) (resume (+ n calls)))))
        """,
        lambda mod: mod.C.h(mod.Bump(1)),
    ),
    # 節の `<-` は外の handler(同じ class の outer)へ effect を出して答えを束ねる
    "defhandler-bind": (
        """
        (defclass C []
          (defhandler h (Bump [n] (<- r (Scale n)) (resume (+ r 100))))
          (defhandler outer (Scale [n] (resume n))))
        """,
        lambda mod: mod.C.outer(mod.C.h(mod.Bump(1))),
    ),
}


def _run_with_state(program: Program) -> object:
    """class の本体の handle・defhandler の Program を走らせる — session var の Get・Put を state の handler が受ける。"""
    return run(scheduled(state(initial={})(program)))


@pytest.mark.parametrize(
    ("source", "program"), _CLASS_BODY_CASES.values(), ids=_CLASS_BODY_CASES.keys()
)
def test_class_body_handle_clauses_read_their_operations(
    source: str, program: Callable[[types.ModuleType], Program]
) -> None:
    """class の本体に直に書いた handle・defhandler の節が Transfer・Resume・Pass を読んで走る — 直す前は NameError。"""
    mod = _eval_module(source, header=_CLASS_ONLY_HEADER)
    assert _run_with_state(program(mod)) == 101


# 書き手の module が handle の展開と同じ名を別の物に束ねている形(例えば振込の effect の Transfer)
_MODULE_OWN_NAMES = """
(setv Transfer "書き手の Transfer" Resume "書き手の Resume" Pass "書き手の Pass")
"""


@pytest.mark.parametrize(
    ("source", "program"), _CLASS_BODY_CASES.values(), ids=_CLASS_BODY_CASES.keys()
)
def test_class_body_handle_leaves_module_globals_alone(
    source: str, program: Callable[[types.ModuleType], Program]
) -> None:
    """class の本体の handle・defhandler は、節が読む名を module の globals に置かず、そこから読まない — 書き手の module の
    同じ名(Transfer など)を変えず、節もそれに左右されない。補助を module の globals へ setdefault で入れる直し(defk の
    `_install-guard-globals` の形)だと、書き手の Transfer が残って節がそれを読み、import で入れる直しだと書き手の名を
    上書きする。"""
    mod = _eval_module(_MODULE_OWN_NAMES + source, header=_CLASS_ONLY_HEADER)
    bare = _eval_module(_MODULE_OWN_NAMES + "(defclass C [])", header=_CLASS_ONLY_HEADER)
    assert _run_with_state(program(mod)) == 101
    assert sorted(vars(mod)) == sorted(vars(bare))
    assert (mod.Transfer, mod.Resume, mod.Pass) == (
        "書き手の Transfer",
        "書き手の Resume",
        "書き手の Pass",
    )


# class の本体の展開の中で作る関数が global で引いてよい名: builtins と、session の鍵が読む module の `__name__`
# (_build-lazy-init-forms)。この外は、書き手が handle・defhandler に書いた名(effect の型・節の本文の名)だけ。
_GLOBALS_ALWAYS_ALLOWED = frozenset(dir(builtins)) | {"__name__"}

# 構造の検だけで読む形も足す(節の操作・束ね・宣言・session val の展開を通す — 走らせない)
_CLASS_BODY_SHAPES = {
    **{name: source for name, (source, _) in _CLASS_BODY_CASES.items()},
    "handle-reperform": "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (reperform effect)))))",
    "defhandler-finish": '(defclass C [] (defhandler h (Bump [n] :finish-reason "期限" (finish n))))',
    "defhandler-declared-session-val": """
        (defclass C []
          (defhandler h {:effects [Scale]} (session val base (abs 1))
            (Bump [n] (<- r (Scale n)) (resume (+ r base)))))
        """,
}


def _written_names(source: str) -> frozenset[str]:
    """source に書き手が書いた名(Python の綴り)。defhandler の定義の名は除く — 展開の中の関数がそれを名で読むと、
    class の本体では class の属性を飛ばして module の globals を引く。"""
    names: set[str] = set()

    def walk(form: object) -> None:
        if isinstance(form, hy.models.Symbol):
            names.add(hy.mangle(str(form)))
        elif isinstance(form, hy.models.Sequence):
            items = list(form)
            if isinstance(form, hy.models.Expression) and items and str(items[0]) == "defhandler":
                items = items[:1] + items[2:]
            for item in items:
                walk(item)

    for form in hy.read_many(source):
        walk(form)
    return frozenset(names)


def _class_body_code(source: str) -> types.CodeType:
    """class 1 つだけの module を compile し、class C の本体の code を返す(走らせない)。"""
    mod = types.ModuleType("handle_scope")
    sys.modules["handle_scope"] = mod
    tree = hy_compile(hy.read_many(_CLASS_ONLY_HEADER + source), mod)
    module_code = compile(tree, "<handle_scope>", "exec")
    return next(
        const
        for const in module_code.co_consts
        if isinstance(const, types.CodeType) and const.co_name == "C"
    )


def _nested_global_loads(code: types.CodeType) -> set[str]:
    """code の中で作る関数(入れ子の code)が global で引く名。class の本体そのものの名の引きは数えない。"""
    names: set[str] = set()
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= {
                instruction.argval
                for instruction in dis.get_instructions(const)
                if instruction.opname in ("LOAD_GLOBAL", "LOAD_FROM_DICT_OR_GLOBALS")
            }
            names |= _nested_global_loads(const)
    return names


@pytest.mark.parametrize("source", _CLASS_BODY_SHAPES.values(), ids=_CLASS_BODY_SHAPES.keys())
def test_class_body_handle_functions_load_only_written_globals(source: str) -> None:
    """class の本体の handle・defhandler の展開の中で作る関数(節の関数・handler を組む関数・本文に被せる関数)は、builtins・
    `__name__`・書き手が書いた名の外を global で引かない。展開が名で読む物は組む関数の中で import して閉包から読む —
    class の本体で並べた import や定義の名は class の属性になり、関数からは class の段を飛ばして module の globals を
    引く。import の名の一覧に依らない検で、走らせる検が通らない節の形も捉える(設計検証
    lt-CZC8SGS3C6DFDGSGG64YF49DH9 の盲検 B: 節が定義の名 `h` を名で読む変更が、走らせる検を通った)。"""
    loaded = _nested_global_loads(_class_body_code(source))
    assert loaded - _GLOBALS_ALWAYS_ALLOWED - _written_names(source) == set()
