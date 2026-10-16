"""doeff-indexer の wheel を組む CI(build-indexer.yml)が、配置の間違った wheel を upload しない事の検(取り組み doeff-publish・
card ki-d20475e94d87 の見つけた不具合)。

PyPI の 0.1.3 の wheel は、命令の binary を命令の入口が探す `doeff_indexer/bin/` でなく `python/doeff_indexer/bin/` に持った。
maturin 1.12 は pyproject.toml の `include` の file を project の根からの path のまま入れ、1.13 から python-source の下の path にする。
0.1.3 は maturin-action が当日の最新 1.12 を引いた CI で組まれたが、build-indexer.yml は版を固定していなかった
(経緯と道具は tests/test_indexer_wheel_layout.py・tools/verify_indexer_wheel.py)。

この検は workflow の側の守り 2 つを読む:

1. maturin-action の step は全部 maturin-version を 1.13 以上に固定する(その日の「最新」で配置が変わらない)。
2. 全部の wheel の build の後に、tools/verify_indexer_wheel.py で配置を見て Linux x86_64 の wheel を入れて命令を撃つ job が走り、
   upload の job はその job を待つ(間違った wheel は upload の前に止まる)。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.test_indexer_wheel_layout import MATURIN_FLOOR, _versions

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "build-indexer.yml"
MATURIN_ACTION = "PyO3/maturin-action"
TOOL_PATH = "tools/verify_indexer_wheel.py"


def _jobs() -> dict[str, dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _steps_using(job: dict, action: str) -> list[dict]:
    return [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith(action)]


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def test_every_maturin_action_step_pins_maturin_from_the_layout_fix() -> None:
    build_steps = [
        (name, step) for name, job in _jobs().items() for step in _steps_using(job, MATURIN_ACTION)
    ]
    assert build_steps, "build-indexer.yml に maturin-action の step が無い"
    unpinned = [name for name, step in build_steps if "maturin-version" not in step.get("with", {})]
    assert unpinned == [], f"maturin-version の固定が無い job: {unpinned}"
    too_old = [
        name
        for name, step in build_steps
        if _versions(step["with"]["maturin-version"]) < MATURIN_FLOOR
    ]
    assert too_old == [], f"maturin 1.13 より古い版を固定した job: {too_old}"


def test_wheels_are_checked_and_run_before_the_upload() -> None:
    jobs = _jobs()
    # wheel を組む job(sdist の job は同じ action で tar.gz を組む — 配置を見る相手でない)。
    builders = {
        name
        for name, job in jobs.items()
        if any(
            step.get("with", {}).get("command") != "sdist"
            for step in _steps_using(job, MATURIN_ACTION)
        )
    }
    verify_jobs = {
        name
        for name, job in jobs.items()
        if any(TOOL_PATH in step.get("run", "") for step in job.get("steps", []))
    }
    assert len(verify_jobs) == 1, f"配置を見る job が 1 つでない: {sorted(verify_jobs)}"
    (verify,) = verify_jobs
    assert builders <= _needs(jobs[verify]), "全部の build の後に走らない"
    command = next(
        step["run"] for step in jobs[verify]["steps"] if TOOL_PATH in step.get("run", "")
    )
    # 入れて撃つのは、この job の runner(ubuntu-latest・x86_64)で動く Linux x86_64 の wheel。
    assert "--smoke" in command
    assert "manylinux" in command
    assert "x86_64" in command
    assert verify in _needs(jobs["publish"]), "upload が配置の検を待たない"
