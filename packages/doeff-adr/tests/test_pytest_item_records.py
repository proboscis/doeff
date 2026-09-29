"""defadr・defsemgrep と defadr の :enforcement の中の deftest / defsemgrep が、展開の時に pytest の item の記録を書く
(記録の形の定義元 = doeff_hy/pytest_items.py・agora-redesign #1222 / #1211)。

記録は Hy の importer が pyc の隣に書くので、pyc を書く新しい interpreter で import してから読む
(この pytest の process は bytecode を書かない設定で走る — conftest.py)。
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import textwrap
from pathlib import Path

from doeff_hy.pytest_items import FunctionItem, RecordedModule

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


def _python(root: Path, code: str) -> str:
    """pyc を tmp の下の置き場へ書く新しい interpreter で ``code`` を走らせ、標準出力を返す(記録は pyc と一緒にだけ書かれる)。"""
    # pyc の置き場は tmp の下(checkout の中に pyc を書かない — conftest.py の _bytecode_settings_pinned)。
    # 書く process と読む process が同じ置き場を見るので、記録も同じ所から読まれる。
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "",
        "PYTHONPYCACHEPREFIX": str(root / ".pyc"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_adr_macros_record_the_items_they_define(tmp_path: Path) -> None:
    """defadr が作る検の関数と、:enforcement の中と module の直下の deftest / defsemgrep の関数が、全部記録される。"""
    source = tmp_path / "defadr_sample.hy"
    source.write_text(ADR)
    _python(tmp_path, "import doeff_hy, defadr_sample")
    out = _python(
        tmp_path,
        f"""
        import pickle, sys
        import doeff_hy
        from hy.importer import read_valid_records
        from doeff_hy.pytest_items import read_module
        records = read_valid_records({str(source)!r})
        assert "defadr_sample" not in sys.modules
        sys.stdout.write(pickle.dumps(read_module(records)).hex())
        """,
    )
    recorded: RecordedModule = pickle.loads(bytes.fromhex(out))
    functions = sorted(
        (record for record in recorded.records if isinstance(record, FunctionItem)),
        key=lambda record: record.name,
    )
    assert [record.name for record in functions] == [
        "test_ADR_SAMPLE_001_adr_contract",
        "test_inline_contract",
        "test_no_string_options_defsemgrep",
        "test_top_level_rule_defsemgrep",
    ]
    assert len(functions) == len(recorded.records)
    assert {f.name: f.argnames for f in functions}["test_inline_contract"] == ("doeff_interpreter",)
