"""defk / defhandler / defp の本体(__doeff_body__)は hy.repr の文字列で運び、compile した code に本体の model の組み立てを入れない。

出自 = 2026-09-28 の実測(agora-controllers の検の速さ): 本体を quote で埋め込むと、Hy はその quote を「import のたびに model の木を
1 節ずつ組み立てる Python code」へ compile し、.pyc の無い compile(手番の模擬の import の木で 44 秒)の半分がそこに消えていた。
"""


import ast
import types

import hy
import hy.compiler
import hy.models

from doeff_hy.quoted_forms import QuotedForms
from doeff_hy.sexpr import args_of, body_of

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
    # 本体 = 名前・引数・契約の後ろの式(書いたまま)。
    assert list(body) == list(defk_form[4:])
    assert len(body) == 3
    assert body_of(namespace["long_body"]) == hy.models.List(defk_form[4:])
    assert len(namespace["ask_handler"].__doeff_body__) == 1
    assert str(namespace["ask_handler"].__doeff_body__[0][0]) == "Ask1"
    assert len(namespace["answer"].__doeff_body__) == 1


def test_the_args_are_carried_as_quoted_forms_equal_to_the_written_params() -> None:
    # 引数(__doeff_args__)も本体と同じく文字列で運び、読んだ値は書いたままの引数の列と等しい。
    # 本体は source の file を読み直さないので、file の無い eval(ここ)でも本体と引数が読める。
    namespace = _module_namespace()
    written = hy.read_many(SOURCE)
    defk_form = next(form for form in written if isinstance(form, hy.models.Expression) and str(form[0]) == "defk")
    assert isinstance(namespace["long_body"].__doeff_args__, QuotedForms)
    assert args_of(namespace["long_body"]) == defk_form[2]


def test_the_compiled_module_does_not_rebuild_the_body_models() -> None:
    module = types.ModuleType("quoted_forms_probe")
    compiled = hy.compiler.hy_compile(hy.read_many(SOURCE), module)
    # model を組み立てる呼び出し(hy.models.Expression(...) など)が 1 つも無い — 契約の失敗の文に model の字面が文字列の定数として
    # 入るのは構わない(呼び出しではない)。
    model_calls = [
        ast.unparse(node.func)
        for node in ast.walk(compiled)
        if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("hy.models.")
    ]
    assert model_calls == []
    assert "QuotedForms" in ast.unparse(compiled)
