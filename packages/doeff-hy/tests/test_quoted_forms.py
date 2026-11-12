"""The bodies of defk / defhandler / defp (__doeff_body__) are carried as hy.repr strings, and the compiled code does not contain model construction for the body.

Origin = measurement on 2026-09-28 (check speed of agora-controllers): when the body is embedded with quote, Hy compiles that quote into "Python code that builds the model tree one node at a time on every import", and half of a compile without .pyc (44 seconds on the simulated-turn import tree) was spent there.
"""


import ast
import types

import hy
import hy.compiler
import hy.models

from doeff_hy._quoted_forms import QuotedForms, packed_text
from doeff_hy._sexpr import args_of, body_of

SOURCE = """
(require doeff-hy.macros [defk defp defhandler <-])
(import doeff [EffectBase])
(import dataclasses [dataclass])
(defclass [(dataclass :frozen True)] Ask1 [EffectBase] #^ str key)
(defk long-body [x]
  {:pre [(: x int)] :post [(: % int)]}
  (<- a (Ask1 "alpha"))
  (setv b (+ x 1) c (* b 2) d [b c {"k" (str c)}])
  (+ b c (len d)))
(defp answer {:post [(: % int)]} (+ 40 2))
(defhandler ask-handler
  (Ask1 [key] (resume (len key))))
"""


def _module_namespace() -> dict[str, object]:
    namespace: dict[str, object] = {"__name__": "quoted_forms_probe"}
    hy.eval(hy.read_many(SOURCE), namespace)
    return namespace


def test_the_body_is_carried_as_quoted_forms_equal_to_the_written_models() -> None:
    namespace = _module_namespace()
    body = namespace["long_body"].__doeff_body__
    assert isinstance(body, QuotedForms)
    written = hy.read_many(SOURCE)
    defk_form = next(form for form in written if isinstance(form, hy.models.Expression) and str(form[0]) == "defk")
    # Body = the expressions after the name, arguments and contract (as written).
    assert list(body) == list(defk_form[4:])
    assert len(body) == 3
    assert body_of(namespace["long_body"]) == hy.models.List(defk_form[4:])
    assert len(namespace["ask_handler"].__doeff_body__) == 1
    assert str(namespace["ask_handler"].__doeff_body__[0][0]) == "Ask1"
    assert len(namespace["answer"].__doeff_body__) == 1


def test_the_args_are_carried_as_quoted_forms_equal_to_the_written_params() -> None:
    # The arguments (__doeff_args__) are also carried as a string like the body, and the value read back equals the argument list as written.
    # The body does not re-read the source file, so body and arguments can be read even in an eval with no file (here).
    namespace = _module_namespace()
    written = hy.read_many(SOURCE)
    defk_form = next(form for form in written if isinstance(form, hy.models.Expression) and str(form[0]) == "defk")
    assert isinstance(namespace["long_body"].__doeff_args__, QuotedForms)
    assert args_of(namespace["long_body"]) == defk_form[2]


def test_the_compiled_module_does_not_rebuild_the_body_models() -> None:
    module = types.ModuleType("quoted_forms_probe")
    compiled = hy.compiler.hy_compile(hy.read_many(SOURCE), module)
    # There is no call that builds a model (such as hy.models.Expression(...)) -- the model text appearing as a string constant in the contract-failure message
    # is fine (it is not a call).
    model_calls = [
        ast.unparse(node.func)
        for node in ast.walk(compiled)
        if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("hy.models.")
    ]
    assert model_calls == []
    assert "QuotedForms" in ast.unparse(compiled)


def test_the_body_text_is_carried_packed_and_reads_back_whole() -> None:
    # The text is placed packed (zlib of its UTF-8): a body with one non-ASCII character would otherwise be held at 2 bytes per character for life.
    namespace: dict[str, object] = {"__name__": "quoted_forms_packed_probe"}
    note = "本文の文字列は畳んで持つ。" * 40
    hy.eval(hy.read_many(SOURCE + f'\n(defk noted [x] {{:pre [(: x int)] :post [(: % int)]}} (setv message "{note}") (+ x (len message)))\n'), namespace)
    body = namespace["noted"].__doeff_body__
    assert isinstance(body, QuotedForms)
    assert isinstance(body.packed, bytes)
    assert note in body.text
    assert len(body.packed) < len(body.text.encode("utf-8")) // 4
    assert str(body[0][2]) == note  # (setv message "…") — the string reads back whole


def test_a_text_compiled_before_packing_is_still_read() -> None:
    # Code compiled before the text was packed passes the string itself; it reads, compares and hashes the same as the packed form.
    text = hy.repr(hy.models.List([hy.models.Expression([hy.models.Symbol("+"), hy.models.Integer(1), hy.models.Integer(2)])]))
    old = QuotedForms(text)
    new = QuotedForms(packed_text(text))
    assert old.text == new.text == text
    assert old == new
    assert hash(old) == hash(new)
    assert list(old) == list(new)
    assert QuotedForms(packed_text(text)) == new
