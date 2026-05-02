"""Tests for defhandler docstring support."""


def _hy_eval_in_module(code, mod_name):
    import sys
    import types

    import doeff_hy  # noqa: F401
    import hy

    mod = types.ModuleType(mod_name)
    mod.__file__ = "<test>"
    sys.modules[mod_name] = mod
    hy.eval(hy.read_many(code), module=mod)
    return mod


def test_defhandler_docstrings_are_metadata_not_clauses():
    import sys

    code = """
(require doeff-hy.macros [defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [state])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase])

(defhandler no-param-handler
  "No params handler docs."
  (Ping []
    (resume "plain")))

(defhandler param-handler [prefix]
  "Param handler docs."
  (Ping []
    (resume (+ prefix ":plain"))))

(defhandler lazy-doc-handler
  "Lazy handler docs."
  (lazy-val msg "lazy")
  (Ping []
    (resume msg)))

(defp body {:post [(: % str)]}
  (<- value (Ping))
  value)

(setv no-param-result
  (doeff-run (scheduled (no-param-handler body))))

(setv param-instance (param-handler "x"))
(setv param-result
  (doeff-run (scheduled (param-instance body))))

(setv lazy-result
  (doeff-run (scheduled (WithHandler (state) (lazy-doc-handler body)))))
"""
    mod = _hy_eval_in_module(code, "_test_defhandler_docstring")
    try:
        assert mod.no_param_handler.__doc__ == "No params handler docs."
        assert mod.param_handler.__doc__ == "Param handler docs."
        assert mod.param_instance.__doc__ == "Param handler docs."
        assert mod.lazy_doc_handler.__doc__ == "Lazy handler docs."
        assert mod.no_param_result == "plain"
        assert mod.param_result == "x:plain"
        assert mod.lazy_result == "lazy"

        body = str(mod.param_handler.__doeff_body__)
        assert "Param handler docs." not in body
        assert "Ping" in body
    finally:
        del sys.modules["_test_defhandler_docstring"]
