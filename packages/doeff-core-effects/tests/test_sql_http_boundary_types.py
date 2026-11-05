"""Failure cases at the type boundary of sql_effects and http_effects (the boundary decided when the hand-written .pyi was replaced with tool output).

- SqlTransaction becomes generic through defeffect's type parameter :tp [T], and the program's answer T passes through to the answer of the user's `<-` (the type declaration .pyi is also
  `class SqlTransaction[T](EffectBase[T | SqlFailed | SqlUnreachable])`). If it did not pass, a mistaken answer type would not turn red.
- HttpRequest's params and body are arbitrary values passed to an external HTTP, and the boundary type is `Mapping[str, object]` (covariant in the value -- the user's
  `dict[str, str]` goes in as is. `dict[str, object]` is invariant and does not go in, and `Any` is a type escape hatch). A mapping that is not a dict is turned into a dict on the spot,
  and values that are neither dict nor mapping (such as the sequence of pairs httpx accepts) are kept as they are, as before.
- The type of a driver value is checked at run time, and the result of the check is carried to a static type by a type predicate (TypeGuard) (no cast is used).
"""

import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import doeff_hy  # noqa: F401  # Enable the Hy import hook
import pytest

from doeff_core_effects import HttpRequest
from doeff_core_effects._sql_effects import is_driver_value, is_plain_rows
from doeff_hy._static_stub import strict_errors

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")


def test_http_request_keeps_a_dict_and_turns_another_mapping_into_one() -> None:
    fields = {"a": 1}
    kept = HttpRequest("GET", "http://x", params=fields, body=fields)
    assert kept.params is fields and kept.body is fields
    turned = HttpRequest("POST", "http://x", params=MappingProxyType(fields), body=MappingProxyType(fields))
    assert type(turned.params) is dict and type(turned.body) is dict
    assert turned.params == fields and turned.body == fields


def test_http_request_leaves_values_that_are_not_mappings_as_they_are() -> None:
    # Params as a sequence of pairs (a query repeating the same name) lose the repeats if turned into a dict -- only a mapping is turned into a dict.
    repeated = [("a", "1"), ("a", "2")]
    untyped_caller: Callable[..., HttpRequest] = HttpRequest  # a way of calling from outside the types (ignoring argument types), checking that the calls made so far still pass
    request = untyped_caller("GET", "http://x", params=repeated, body=b"raw")
    assert request.params == repeated and request.body == b"raw"


def test_the_predicates_carry_the_runtime_check_to_the_type() -> None:
    assert is_plain_rows([(1, "a", None, 1.5, b"x", True)]) is True
    assert is_plain_rows(((1, Decimal("2.5")),)) is False
    assert is_driver_value(Decimal("2.5")) is True
    assert is_driver_value(object()) is False


HTTP_FLOW = """\
(import doeff_core_effects._http_effects [HttpRequest])

(val #^ (get dict #(str str)) strings {"a" "b"})
(val #^ (get dict #(str int)) numbers {"a" 1})
(val request (HttpRequest "POST" "http://x" :params strings :body numbers))
(val other (HttpRequest "POST" "http://x" :params numbers :body strings))
"""


@needs_pyright
def test_http_request_accepts_a_callers_dict_with_any_value_type(tmp_path: Path) -> None:
    assert strict_errors(tmp_path, "(require doeff-hy.macros [val])\n" + HTTP_FLOW) == ()


@needs_pyright
def test_http_request_refuses_a_params_that_is_not_a_mapping(tmp_path: Path) -> None:
    # Failure case: if the boundary type stops being Mapping (becomes object or Any), this call does not turn red.
    bad = "(require doeff-hy.macros [val])\n(import doeff_core_effects._http_effects [HttpRequest])\n(val request (HttpRequest \"GET\" \"http://x\" :params 3))\n"
    assert [e for e in strict_errors(tmp_path, bad) if "reportArgumentType" in e]


SQL_FLOW = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure])
(import doeff_core_effects._sql_effects [SqlTransaction SqlFailed SqlUnreachable])

(defk answer-of []
  {:pre [] :post [(: % ANSWER)]}
  "To receive the program's answer with the caller's type."
  (<- answer ANSWER (SqlTransaction :database "db" :program (Pure 1)))
  answer)
"""


@needs_pyright
def test_the_program_answer_type_reaches_the_caller_of_a_sql_transaction(tmp_path: Path) -> None:
    assert strict_errors(tmp_path, SQL_FLOW.replace("ANSWER", "(| int SqlFailed SqlUnreachable)")) == ()
    # Failure case: receiving the program's answer as str when it is int turns red (T is fixed by the user).
    assert [e for e in strict_errors(tmp_path, SQL_FLOW.replace("ANSWER", "(| str SqlFailed SqlUnreachable)")) if "reportAssignmentType" in e]
