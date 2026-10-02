"""doeff-records の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2826)。

main・admission・laws・wire は .pyi が無く、使い手の repo がこれらの名を使う行を足すと、strict の型の門が書き手に直せない
Unknown の赤で止まった(使い手が import する doeff の module のうち .pyi の無い 63 個の 4 個)。.pyi は道具が .hy から作る
(http_client は手で書いた .pyi — #2810 — のまま。probe は同じ使い手の名として一緒に束ねる)。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy.static_stub --write <.hy>` で作り直す)。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy.static_stub import stale_in

SOURCE = Path(__file__).resolve().parents[1] / "src"

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 使い手の repo の main が import する名(module ごと・2026-10-02 の数え)。
USED = {
    "http_client": ("RecordsEndpoint", "http-records-handler", "http-table-records-handler", "RecordsUnauthorized"),
    "main": ("RecordsSettings", "MaintenancePlan", "records-settings", "records-connected", "pg-handlers-of", "records-serving", "PG-STORE"),
    "admission": ("key-text", "row-matches?", "retention-group-of", "key-from-text"),
    "laws": (
        "LAW-SCHEMA",
        "LawHarness",
        "law-committed-changes-appear-once-in-order",
        "law-stale-put-conflicts",
        "law-undeclared-writes-are-refused",
        "law-put-rows-is-all-or-nothing",
    ),
    "wire": (
        "PATH-PREFIX",
        "ANSWER-KINDS",
        "OPERATIONS",
        "STATUS-OF-ERROR",
        "WireAnswer",
        "WireRefusal",
        "encode-request",
        "answer-from",
        "refusal-from",
    ),
}


def _probe() -> str:
    """名を 1 つずつ別の名に束ねる検の module(束ねた名の型が Unknown なら、その行に strict の赤が出る)。"""
    imports = [f"(import doeff_records.{module} [{' '.join(names)}])" for module, names in USED.items()]
    bindings = [
        f"(setv used-{index} {name})" for index, name in enumerate(name for names in USED.values() for name in names)
    ]
    return "\n".join([*imports, "", *bindings, ""])


def _errors(root: Path) -> list[str]:
    """検の module を、使い手の型の門と同じ strict で検めた赤の文。"""
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    diagnostics = json.loads(done.stdout) if done.stdout.strip() else []
    return [f"{d['line']}: {d['rule']}: {d['message']}" for d in diagnostics if d["severity"] == "error"]


@needs_pyright
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(_probe(), encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if "hy-compile" in e], errors
    assert not [e for e in errors if re.search(r"is unknown|could not be resolved|unknown import symbol", e)], errors


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
