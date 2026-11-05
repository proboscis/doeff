"""runtime_type_form -- the single public point that maps a type expression to the second argument of runtime isinstance (root 2 of #3366).

The contract and `<-` binding in macros.hy and the external tool (the Hy projection of the quality check) call the same function. This checks that the mapped form can actually be eval'd and
passed to isinstance, and that forms that are not mapped are returned as the same object.
"""

import hy
import pytest
from hy.models import Expression, Object, Symbol

from doeff_hy._type_forms import runtime_type_form


def mapped(source: str) -> Object:
    """The form obtained by mapping the source of a type expression (Hy models are compared by content -- hy.repr collapses `(. None __class__)` in spelling, so
    they are not compared by spelling)."""
    return runtime_type_form(hy.read(source))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("None", "(. None __class__)"),
        ("#(int None)", "#(int (. None __class__))"),
        ("(get tuple #(int int))", "tuple"),
        ("(of tuple str ...)", "tuple"),
        ("(of dict str int)", "dict"),
        ("(get dict #(str int))", "dict"),
        ("(| (get tuple #(str ...)) None)", "#(tuple (. None __class__))"),
        ("(| int None)", "#(int (. None __class__))"),
        ("#((get tuple #(int int)) None)", "#(tuple (. None __class__))"),
    ],
)
def test_a_type_form_is_mapped_to_what_isinstance_accepts(source: str, expected: str) -> None:
    assert mapped(source) == hy.read(expected)


@pytest.mark.parametrize(
    ("source", "value", "holds"),
    [
        ("None", None, True),
        ("None", 0, False),
        ("(get tuple #(int int))", (1, 2), True),
        ("(get tuple #(int int))", [1, 2], False),
        ("(| (get dict #(str int)) None)", None, True),
        ("#(int None)", None, True),
    ],
)
def test_the_mapped_form_is_accepted_by_isinstance(source: str, value: object, holds: bool) -> None:
    # The unmapped (get tuple #(int int)) is rejected by isinstance (parameterized generic) -- the mapped form is accepted. As in the expansion,
    # it is evaluated in Hy on each isinstance call.
    check: Expression = Expression([Symbol("isinstance"), Symbol("value"), runtime_type_form(hy.read(source))])
    assert bool(hy.eval(check, {"value": value})) is holds


@pytest.mark.parametrize("source", ["int", "pd.DataFrame", "(| int str)", "\"str\""])
def test_a_form_that_needs_no_mapping_is_returned_as_the_same_object(source: str) -> None:
    form: Object = hy.read(source)
    if source == "(| int str)":
        assert mapped(source) == hy.read("#(int str)")
    else:
        assert runtime_type_form(form) is form
