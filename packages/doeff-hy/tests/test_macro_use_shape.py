"""The code shape of a macro closure (``macro_use._code_facts``) does not change with two things that do not affect meaning -- function type annotations and the numbering of Hy temporary variable names.
A closure that reads annotations as values includes the annotations in the digest."""

import sys
import types
from collections.abc import Callable
from types import CodeType

from doeff_hy_bytecode_guard import _macro_use as macro_use
from doeff_hy_bytecode_guard._macro_use import UsedMacroKey, _code_facts


def _module_code(source: str) -> CodeType:
    return compile(source, "<shape-test>", "exec")


def _shape(source: str, strict: bool = False) -> str:
    return _code_facts(_module_code(source), strict).shape


PLAIN = """
def outer(a, b):
    def walk(x, y="q"):
        return a
    return walk(b)
"""

ANNOTATED = """
def outer(a: int, b: str) -> bool:
    def walk(x: int, y: str = "q") -> bool:
        return a
    return walk(b)
"""


def test_a_function_annotation_does_not_change_the_shape() -> None:
    assert _shape(PLAIN) == _shape(ANNOTATED)


def test_a_changed_annotation_does_not_change_the_shape() -> None:
    assert _shape(ANNOTATED) == _shape(ANNOTATED.replace("int", "float"))


def test_the_strict_shape_includes_the_annotation() -> None:
    assert _shape(PLAIN, strict=True) != _shape(ANNOTATED, strict=True)
    assert _shape(ANNOTATED, strict=True) != _shape(ANNOTATED.replace("int", "float"), strict=True)


def test_an_annotation_of_a_method_does_not_change_the_shape() -> None:
    plain = "class C:\n    def m(self, q):\n        return q\n"
    annotated = "class C:\n    def m(self, q: 'C') -> None:\n        return q\n"
    # The part where a class body has a ``__classdict__`` cell may change (it does not lead to judging an old expansion as matching). A method body is the same.
    assert _code_facts(_inner(_module_code(plain), "C"), False).names == _code_facts(
        _inner(_module_code(annotated), "C"), False
    ).names


def test_an_annotation_of_a_class_body_still_changes_the_shape() -> None:
    plain = "class C:\n    x = 1\n"
    annotated = "class C:\n    x: int = 1\n"
    other = "class C:\n    x: str = 1\n"
    assert _shape(plain) != _shape(annotated)
    assert _shape(annotated) != _shape(other)


def test_a_changed_body_changes_the_shape() -> None:
    assert _shape(PLAIN) != _shape(PLAIN.replace("return a", "return b"))
    assert _shape(ANNOTATED) != _shape(ANNOTATED.replace("return walk(b)", "return walk(a)"))


def test_a_name_used_only_by_an_annotation_is_not_read() -> None:
    facts = _code_facts(_module_code(ANNOTATED), False)
    assert "int" not in facts.names
    assert "int" in _code_facts(_module_code(ANNOTATED), True).names


ANON = """
def f(x):
    {a} = x + 1
    {b} = {a} * 2
    return {b}
"""


def _anon(a: int, b: int) -> str:
    return ANON.format(a=f"_hy_anon_{a}", b=f"_hy_anon_{b}")


def test_a_shifted_hy_temporary_number_does_not_change_the_shape() -> None:
    assert _shape(_anon(46, 47)) == _shape(_anon(48, 49))


def test_different_temporaries_stay_different() -> None:
    same = ANON.format(a="_hy_anon_1", b="_hy_anon_1")
    assert _shape(_anon(1, 2)) != _shape(same)


def test_a_temporary_function_name_number_does_not_change_the_shape() -> None:
    source = "def outer():\n    def {n}(x):\n        return x\n    return {n}\n"
    assert _shape(source.format(n="_hy_anon_fn_3")) == _shape(source.format(n="_hy_anon_fn_9"))


def _inner(module: CodeType, name: str) -> CodeType:
    return next(c for c in module.co_consts if isinstance(c, CodeType) and c.co_name == name)


def _digest(source: str) -> str:
    """Digest of a macro closure that uses ``read`` of the source module (compared between a module where ``read`` reads annotations and one where it does not)."""
    name = "_shape_test_module"  # the module name goes into the digest, so make it the same in the two being compared
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, f"{name}.py", "exec"), module.__dict__)  # noqa: S102
        macro: Callable[..., object] = module.macro
        closure = macro_use.macro_digest(
            UsedMacroKey(table="_hy_macros", module=name, name="macro"), macro, lambda _: None
        )
        assert closure.digest is not None
        return closure.digest
    finally:
        del sys.modules[name]


MACRO = """
def macro(x):
    def inner(y{annotation}):
        return y
    return inner{read}
"""


def test_a_closure_that_does_not_read_annotations_ignores_them() -> None:
    plain = MACRO.format(annotation="", read="")
    assert _digest(plain) == _digest(MACRO.format(annotation=": int", read=""))
    assert _digest(MACRO.format(annotation=": int", read="")) == _digest(
        MACRO.format(annotation=": str", read="")
    )


def test_a_closure_that_reads_annotations_digests_them() -> None:
    read = ".__annotations__"
    assert _digest(MACRO.format(annotation=": int", read=read)) != _digest(
        MACRO.format(annotation=": str", read=read)
    )
    assert _digest(MACRO.format(annotation="", read=read)) != _digest(
        MACRO.format(annotation=": int", read=read)
    )


def test_a_changed_docstring_changes_the_shape() -> None:
    """A docstring is not referenced by any instruction and sits only at the head of the constants table -- if the constants order is discarded, a swap becomes invisible (blind test)."""
    source = 'def helper():\n    "{doc}"\n    return ["A", "B"]\n'
    assert _shape(source.format(doc="A")) != _shape(source.format(doc="B"))


def test_a_string_constant_that_looks_like_a_temporary_name_is_a_value() -> None:
    """String constants are not names -- do not renumber them (blind test)."""
    source = 'def f():\n    return ("{a}", "{b}")\n'
    assert _shape(source.format(a="_hy_anon_1", b="_hy_anon_2")) != _shape(
        source.format(a="_hy_anon_2", b="_hy_anon_1")
    )


DISPATCH = """
from functools import singledispatch
def macro(form):
    @singledispatch
    def emit(x):
        return "generic"
    @emit.register
    def _(x{annotation}):
        return "typed"
    return emit(1)
"""


def test_a_closure_that_registers_by_annotation_digests_it() -> None:
    """The standard library's ``register`` reads types from annotations -- it is not walked into, so when met by name it gets a digest that includes the annotations (blind test)."""
    assert _digest(DISPATCH.format(annotation=": int")) != _digest(DISPATCH.format(annotation=": str"))


ALIASED = """
from inspect import signature as sig
def macro(form):
    def helper(x{annotation}):
        return x
    return str(sig(helper))
"""


def test_a_closure_that_reads_annotations_through_an_alias_digests_them() -> None:
    assert _digest(ALIASED.format(annotation=": int")) != _digest(ALIASED.format(annotation=": str"))
