"""準備できたの報告の型の宣言(shared/intent/readiness_model.pyi・shared/protocol/readiness_handlers.pyi)の失敗ケース(#2777)。

readiness_model.hy と readiness_handlers.hy は Hy の module で型の宣言が無かったので、ReportReady を出す使い手と報告を積む fake の
答え手を土台の組に並べる使い手の strict に、書き手に直せない Unknown の赤(Type of "ReportReady" is unknown・当時の fake
readiness_memory の Type is unknown)が出ていた。→ 2 つの .pyi で宣言する。宣言を外すと赤になる。fake は #3028 で readiness-claims
(入れ物 ReadinessLog)に替えた。.pyi は doeff_hy.static_stub が .hy から作り(#2826)、
実装との一致は tests/test_generated_stubs.py(作り直した物 == commit された物)が検める。
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
(import doeff [Program EffectBase with-handlers])
(import doeff_cluster.shared.intent.readiness_model [ReportReady ROLE-STANDBY])
(import doeff_cluster.shared.protocol.readiness_handlers [ReadinessLog readiness-claims])

(defk probe-report []
  {:pre [] :post [(: % None)] :tags {:context "probe" :role "program"}}
  "準備できたの報告の effect の型が読める(欄を名で渡す)。"
  (<- (ReportReady :ready True :reason "probe" :role ROLE-STANDBY))
  None)

(defk probe-recorded [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % int)] :tags {:context "probe" :role "foundation"}}
  "報告を積む答え手を並べた下で本文を走らせる(handler の型が読める)。"
  (<- answer int (with-handlers [(readiness-claims (ReadinessLog))] body))
  answer)
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "doeff_hy.static_check",
            "--root",
            str(root),
            "--json",
            "--strict",
            "--no-cache",
            str(root / "probe.hy"),
        ],
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
def test_users_of_the_readiness_report_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors

