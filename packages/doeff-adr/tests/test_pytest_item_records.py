"""defadr・defsemgrep と defadr の :enforcement の中の deftest / defsemgrep が、展開の式で pytest の item の記録を module に積む
(記録の形の定義元 = doeff_hy/pytest_items.py・agora-redesign #1291)。記録は module を import すれば読める。
"""

import importlib
import sys
from pathlib import Path

from doeff_hy.pytest_items import FunctionItem, decode_records, module_record_texts

ADR = """
(require doeff-adr.macros [defadr deftest defsemgrep rule law])
(import doeff-adr.macros [fact counterexample])

(defadr ADR-SAMPLE-001
  :title "visible issue ownership"
  :status "accepted"
  :scope ["hypha.review"]
  :problem [(fact "open issue was invisible")]
  :decision [(rule R1 "open issue must have a holder")]
  :laws [(law visible-owner
           :statement "nonterminal(issue) => visible_holder(issue)"
           :counterexamples [(counterexample "open issue with no work")])]
  :enforcement
    [(deftest test-inline-contract {:marks ["slow"]}
       (assert (= (+ 1 1) 2)))
     (defsemgrep no-string-options
       :languages ["generic"]
       :message "string options are not typed"
       :pattern "x"
       :bad ["x"]
       :good ["y"])])

(defsemgrep top-level-rule
  "near-no-print"
  [{"relative-path" "pkg/bad.py" "source" "print('x')\\n"}]
  [{"relative-path" "pkg/clean.py" "source" "value = 1\\n"}])
"""


def test_adr_macros_record_the_items_they_define(tmp_path: Path) -> None:
    """defadr が作る検の関数と、:enforcement の中と module の直下の deftest / defsemgrep の関数が、全部記録される。"""
    (tmp_path / "defadr_sample_records.hy").write_text(ADR)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        records = decode_records(module_record_texts(importlib.import_module("defadr_sample_records")))
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("defadr_sample_records", None)
    functions = sorted((r for r in records if isinstance(r, FunctionItem)), key=lambda r: r.name)
    assert [record.name for record in functions] == [
        "test_ADR_SAMPLE_001_adr_contract",
        "test_inline_contract",
        "test_no_string_options_defsemgrep",
        "test_top_level_rule_defsemgrep",
    ]
    assert len(functions) == len(records)
    assert {f.name: f.argnames for f in functions}["test_inline_contract"] == ("doeff_interpreter",)
