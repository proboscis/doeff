"""焼く道具(_foundation/bytecode_pool.hy)の 1 回り目(焼かずに保存先から書くだけ)の process の数の検。

失敗ケース(2026-11-15 の本番): 保存先から遠い region の新しい spot の機体は、焼く並列数 8 のまま 1 回り目を回し、約 3,350 本の取り寄せに
271 秒かかった(1 本 0.65 秒の往復を process ごとに順に待った)。1 回り目は CPU でなく往復を待つので、焼く並列数より多く並べる。
  * 1 回り目の数は焼く並列数の FETCH-JOBS-PER-JOB 倍で、FETCH-JOBS-CEILING を越えない — 焼く並列数のままに戻すと赤。
  * 焼く並列数が 1(memory の空きが子 1 本も入れない)なら 1 回り目も並べない。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import hy  # noqa: F401 — the .hy loader must be registered before the tool is loaded by path

TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "_foundation" / "bytecode_pool.hy"


def _tool():
    """The tool module, loaded by its file path as the worker starts it (it is not imported by package name)."""
    spec = importlib.util.spec_from_file_location("bytecode_pool_fetch_jobs_probe", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_first_pass_runs_more_processes_than_the_bake_but_not_past_the_ceiling() -> None:
    tool = _tool()
    assert tool.FETCH_JOBS_PER_JOB > 1
    assert tool.fetch_jobs(8) == min(tool.FETCH_JOBS_CEILING, 8 * tool.FETCH_JOBS_PER_JOB)
    assert tool.fetch_jobs(2) == 2 * tool.FETCH_JOBS_PER_JOB
    assert tool.fetch_jobs(64) == tool.FETCH_JOBS_CEILING


def test_the_first_pass_does_not_fan_out_when_the_bake_runs_alone() -> None:
    assert _tool().fetch_jobs(1) == 1
