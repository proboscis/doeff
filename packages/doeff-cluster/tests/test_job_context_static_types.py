"""子の文脈(RunContext)の型の宣言を、使い手の strict が読めることの失敗ケース。

もとは根の job_context.hy(新しい置き場を import し直すだけの旧い入口)の型の宣言 job_context.pyi の検だった。job_context.hy は Hy の
module で型の宣言が無かったので、宿の run-context(RunContext)を模擬の土台で組む使い手の strict に、書き手に直せない Unknown の赤
(Type of "RunContext" is unknown・Argument type is unknown ほか)が出ていた。

旧い入口は 2026-10-03 に消した(利用者の決め・#2167)。型と読みの置き場は shared/intent/run_context と
shared/core/run_context_rules で、どちらの .pyi も doeff_hy.static_stub が作り、作り直しとの食い違いは test_generated_stubs.py が
赤にする。ここに残すのは、使い手が新しい置き場から読んだ時に Unknown の赤が出ないことの 1 本(宣言を外すと赤になる)。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [context-of-environ])

(defk probe-context [root]
  {:pre [(: root str)] :post [(: % RunContext)] :tags {:context "probe" :role "judgment"}}
  "模擬の宿の run-context を組む(模擬の土台が答える形)。"
  (RunContext "http://coordinator.invalid" "sim" root "web" :instance "i-1"))

(defk probe-read [environ]
  {:pre [(: environ (get dict #(str str)))] :post [(: % str)] :tags {:context "probe" :role "judgment"}}
  "環境変数から読み、欄を引く(defk の答えの型が読める)。"
  (<- ctx RunContext (context-of-environ environ))
  (+ ctx.worker ctx.revision))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
def test_users_of_run_context_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors
