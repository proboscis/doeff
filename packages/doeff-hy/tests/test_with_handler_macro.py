from __future__ import annotations

import pytest


def test_with_handler_macro_applies_stack_left_to_right() -> None:
    import doeff_hy  # noqa: F401 - register Hy import hooks
    import hy

    code = """
(require doeff-hy.macros [defp defhandler with-handler <-])
(import doeff [do :as _doeff-do EffectBase run :as doeff-run])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase]
  #^ str value)

(defhandler outer
  (Ping [value]
    (resume (+ value ":outer"))))

(defhandler inner
  (Ping [value]
    (resume (+ value ":inner"))))

(defp body {:post [(: % str)]}
  (<- value (Ping :value "start"))
  value)

(setv _result (doeff-run (with-handler [outer inner] body)))
(assert (= _result "start:inner"))
"""
    hy.eval(hy.read_many(code), module=__name__)


def test_with_handler_macro_rejects_empty_stack() -> None:
    import doeff_hy  # noqa: F401 - register Hy import hooks
    import hy
    from hy.errors import HyMacroExpansionError

    code = """
(require doeff-hy.macros [defp with-handler])
(import doeff [do :as _doeff-do])

(defp body {:post [(: % int)]} 1)
(with-handler [] body)
"""
    with pytest.raises(HyMacroExpansionError, match="requires a non-empty handler vector"):
        hy.eval(hy.read_many(code), module=__name__)
