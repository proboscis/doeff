"""boot.sh の job の cwd の根(WORKER_JOBS_ROOT): 世代の dir の外へ出し、世代と一緒に消し、残りを掃く。

boot.sh の全部は走らせず(worker を起こす)、3 つの関数の本文を script から切り出して本物の sh で走らせる。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BOOT = Path(__file__).resolve().parents[1] / "deploy" / "boot.sh"
FUNCTIONS = ("jobs_root_link", "generation_dir_drop", "jobs_root_sweep")


def _functions() -> str:
    text = BOOT.read_text(encoding="utf8")
    bodies = [re.search(rf"^{name}\(\) \{{.*?^\}}$", text, re.M | re.S) for name in FUNCTIONS]
    assert all(bodies), "boot.sh に関数が無い"
    return "\n".join(body.group(0) for body in bodies if body)


def _run(call: str, jobs_root: Path | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"]}
    if jobs_root is not None:
        env["WORKER_JOBS_ROOT"] = str(jobs_root)
    done = subprocess.run(["sh", "-c", f"{_functions()}\n{call}"], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return done


def test_the_jobs_of_a_generation_live_under_the_jobs_root(tmp_path: Path) -> None:
    root, state = tmp_path / "hdd", tmp_path / "gen" / "g1" / "state"
    _run(f'jobs_root_link g1 "{state}"', root)
    assert (state / "jobs").is_symlink()
    assert (state / "jobs").resolve() == (root / "g1").resolve()
    # 作業 dir の退けは jobs の下の名の付け替え — symlink の先の同じ file system の中で済む。
    (state / "jobs" / "task_1" / "home").mkdir(parents=True)
    (state / "jobs" / ".set-aside").mkdir()
    (state / "jobs" / "task_1").rename(state / "jobs" / ".set-aside" / "task_1.1")
    assert (root / "g1" / ".set-aside" / "task_1.1" / "home").is_dir()


def test_without_the_jobs_root_nothing_changes(tmp_path: Path) -> None:
    state = tmp_path / "gen" / "g1" / "state"
    _run(f'jobs_root_link g1 "{state}"', None)
    assert not (state / "jobs").exists()
    gen_dir = tmp_path / "gen" / "g0"
    gen_dir.mkdir(parents=True)
    _run(f'generation_dir_drop "{gen_dir}"', None)
    assert not gen_dir.exists()
    keep = tmp_path / "gen" / "g2"
    keep.mkdir()
    _run(f'jobs_root_sweep "{tmp_path / "gen"}"', None)
    assert keep.is_dir()


def test_dropping_a_generation_drops_its_jobs_and_only_those(tmp_path: Path) -> None:
    root = tmp_path / "hdd"
    for name in ("old", "new"):
        (root / name / "task_1").mkdir(parents=True)
    gen_dir = tmp_path / "gen" / "old"
    gen_dir.mkdir(parents=True)
    _run(f'generation_dir_drop "{gen_dir}"', root)
    assert not gen_dir.exists()
    assert not (root / "old").exists()
    assert (root / "new" / "task_1").is_dir()


def test_the_sweep_removes_only_places_without_a_generation_dir(tmp_path: Path) -> None:
    root, gen_root = tmp_path / "hdd", tmp_path / "gen"
    (root / "orphan" / "task_1").mkdir(parents=True)
    (root / "running" / "task_2").mkdir(parents=True)
    (gen_root / "running").mkdir(parents=True)
    done = _run(f'jobs_root_sweep "{gen_root}"', root)
    assert not (root / "orphan").exists()
    assert (root / "running" / "task_2").is_dir()
    assert "orphan" in done.stderr


def test_the_sweep_of_an_empty_root_is_quiet(tmp_path: Path) -> None:
    root, gen_root = tmp_path / "hdd", tmp_path / "gen"
    root.mkdir()
    gen_root.mkdir()
    assert _run(f'jobs_root_sweep "{gen_root}"', root).stderr == ""
