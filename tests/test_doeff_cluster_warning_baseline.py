"""Tests for the packages/doeff-cluster warning-baseline comparison (scripts/doeff_cluster_warning_baseline.py).

Failure cases: one more new warning is red; fixing without lowering the baseline is red; lower only lowers and never raises.
#2906: the commit hook path (when files are passed to scripts/lint-doeff-cluster.sh) measures with the binary for HEAD's build-input key and does not call the search-path
linter; when absent from the location, it names "cannot measure" and passes; the comparison script uses the --linter it is given.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = ROOT / "scripts" / "doeff_cluster_warning_baseline.py"
# The child starts with its own environment, so it does not inherit the root conftest's pin (PYTHONDONTWRITEBYTECODE=1) -- pass it explicitly in each environment so no .pyc is written into the checkout
# (next to the script / .venv site-packages) (if written, the session-end check conftest.py goes red -- the real daily run on a fresh .venv,
# 2026-10-10).
# The child imports doeff and Hy inside the script. The child writes no .pyc and does not add to the unnamed Hy-expansion location (the user's cache), so
# if the caller names the location with DOEFF_HY_CODE_STORE (daily task, worker), pass that name on to the child -- otherwise the child pays a cold Hy expansion every time
# (about 14 s in atlas) and the check that starts it 5 times exceeded pytest-timeout's 60 s (real daily run, 2026-10-11 00:05 JST).
HY_CODE_STORE_ENV: str = "DOEFF_HY_CODE_STORE"


def _environment_value(name: str) -> str | None:
    """The raw value of one environment variable (None if absent) -- ask Ask under the foundation handler env_var_ask (the same shape as doeff_hy._env_places reads).
    Reading os.environ directly hits doeff-linter's DOEFF004 (red in the daily lint stage, 2026-10-11 08:05 JST)."""
    from doeff import run, with_handlers
    from doeff_core_effects import Ask
    from doeff_core_effects import env_var_ask, reader

    value = run(with_handlers([reader({name: None}), env_var_ask(prefix="")], Ask(name)))
    return value if isinstance(value, str) else None


def _child_environment(**names: str) -> dict[str, str]:
    """Environment of a child process started with its own environment: the values of the names given, a flag not to write .pyc, and (if present) the caller's Hy-expansion location."""
    store: str | None = _environment_value(HY_CODE_STORE_ENV)
    named_store: dict[str, str] = {HY_CODE_STORE_ENV: store} if store is not None else {}
    return {**names, "PYTHONDONTWRITEBYTECODE": "1", **named_store}


def _module() -> ModuleType:
    """Load the script as a module to try the pure comparison functions directly."""
    spec = importlib.util.spec_from_file_location("doeff_cluster_warning_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASELINE = _module()
BASE: dict[str, dict[str, int]] = {"DOEFF172": {"src/a.hy": 2, "src/b.hy": 1}}


def test_a_new_warning_is_red() -> None:
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 3, "src/b.hy": 1}}, None)
    assert [(f.path, f.baseline, f.current) for f in comparison.grown] == [("src/a.hy", 2, 3)]
    assert comparison.stale == ()


def test_a_new_rule_in_a_new_file_is_red() -> None:
    current = {"DOEFF172": {"src/a.hy": 2, "src/b.hy": 1}, "DOEFF113": {"src/c.py": 1}}
    comparison = BASELINE.compare(BASE, current, None)
    assert [(f.rule, f.path) for f in comparison.grown] == [("DOEFF113", "src/c.py")]


def test_a_fix_without_lowering_the_baseline_is_red() -> None:
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 2}}, None)
    assert comparison.grown == ()
    assert [(f.path, f.baseline, f.current) for f in comparison.stale] == [("src/b.hy", 1, 0)]


def test_only_the_measured_files_are_compared() -> None:
    # The commit hook measures only changed files -- even if an unmeasured b.hy looks like 0, it is not treated as a forgotten lowering.
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 2}}, frozenset({"src/a.hy", "architecture.hy"}))
    assert comparison.grown == () and comparison.stale == ()


def test_lower_only_lowers() -> None:
    current = {"DOEFF172": {"src/a.hy": 5, "src/b.hy": 0}, "DOEFF113": {"src/c.py": 4}}
    assert BASELINE.lowered(BASE, current) == {"DOEFF172": {"src/a.hy": 2}}


def test_warning_counts_reads_only_warnings() -> None:
    report = [
        {"rule": "DOEFF172", "severity": "warning", "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}]},
        {"rule": "DOEFF014", "severity": "info", "violations": [{"file": "src/a.hy"}]},
        {"rule": "DOEFF104", "severity": "error", "violations": [{"file": "src/b.hy"}]},
    ]
    assert BASELINE.warning_counts(report, Path("/nowhere/packages/doeff-cluster")) == {"DOEFF172": {"src/a.hy": 2}}


def test_the_check_entry_is_red_on_a_new_warning(tmp_path: Path) -> None:
    # End to end: when the fake doeff-linter emits one more warning than the baseline, the check entrypoint names the increased set with rc 1.
    repo: Path = tmp_path / "repo"
    package: Path = repo / "packages" / "doeff-cluster"
    package.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(BASE), encoding="utf-8")
    report = [{"rule": "DOEFF172", "severity": "warning",
               "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]
    tools: Path = tmp_path / "bin"
    tools.mkdir()
    linter: Path = tools / "doeff-linter"
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    linter.chmod(0o755)
    environment = _child_environment(PATH=f"{tools}:/usr/bin:/bin")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), "check"], cwd=repo, env=environment,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in result.stderr
    # Green when it matches the baseline.
    report_ok = [{"rule": "DOEFF172", "severity": "warning",
                  "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report_ok)}\nEOF\n", encoding="utf-8")
    passed = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), "check"], cwd=repo, env=environment,
                            capture_output=True, text=True, check=False)
    assert passed.returncode == 0, passed.stderr


def _fake_repo(root: Path, report: list[dict[str, object]], baseline: dict[str, dict[str, int]]) -> dict[str, str]:
    """Build a sample tree (with a fake linter) whose location is an arbitrary dir (root) -- to try, with trees in different places, that the baseline key does not depend on the location.
    The answer is the child process's environment, which resolves the fake linter first."""
    package: Path = root / "packages" / "doeff-cluster"
    package.mkdir(parents=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    tools: Path = root / "bin"
    tools.mkdir()
    linter: Path = tools / "doeff-linter"
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    linter.chmod(0o755)
    return _child_environment(PATH=f"{tools}:/usr/bin:/bin")


def _absolute_report(root: Path, files: list[str]) -> list[dict[str, object]]:
    """Build a report that emits warnings with the tree's absolute path, like the real linter."""
    package: Path = root / "packages" / "doeff-cluster"
    return [{"rule": "DOEFF172", "severity": "warning", "violations": [{"file": str(package / f)} for f in files]}]


def test_absolute_paths_from_another_tree_are_keyed_from_the_package(tmp_path: Path) -> None:
    # Counterexample (measured in ddc164310 / cc1-w38): the baseline keys are relative to the package dir. Even if the linter reports absolute paths in a tree in a different place,
    # check is green when it matches the baseline, a check that is passed paths also compares (red if you add one), and lower does not empty the baseline.
    for place in ("tree-a", "elsewhere/tree-b"):
        root: Path = tmp_path / place
        environment = _fake_repo(root, _absolute_report(root, ["src/a.hy", "src/a.hy", "src/b.hy"]), BASE)
        ok = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "check"], env=environment,
                            capture_output=True, text=True, check=False)
        assert ok.returncode == 0, ok.stderr
        lower = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "lower"], env=environment,
                               capture_output=True, text=True, check=False)
        assert lower.returncode == 0, lower.stderr
        kept = json.loads((root / "packages" / "doeff-cluster" / "lint-warning-baseline.json").read_text(encoding="utf-8"))
        assert kept == BASE, kept
    grown_root: Path = tmp_path / "tree-c"
    environment = _fake_repo(grown_root, _absolute_report(grown_root, ["src/a.hy"] * 3 + ["src/b.hy"]), BASE)
    staged = subprocess.run([sys.executable, str(SCRIPT), "--root", str(grown_root), "check", "packages/doeff-cluster/src/a.hy"],
                            env=environment, capture_output=True, text=True, check=False)
    assert staged.returncode == 1, staged.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in staged.stderr


def test_the_linter_given_by_the_hook_is_used_instead_of_the_path_one(tmp_path: Path) -> None:
    # #2906: the commit hook passes the binary for HEAD's build-input key via --linter -- it does not call the search-path linter (a different version).
    root: Path = tmp_path / "tree"
    report: list[dict[str, object]] = _absolute_report(root, ["src/a.hy"] * 3 + ["src/b.hy"])
    environment: dict[str, str] = _fake_repo(root, report, BASE)
    (root / "bin" / "doeff-linter").write_text("#!/bin/sh\necho the search-path linter was called >&2\nexit 9\n", encoding="utf-8")
    given: Path = tmp_path / "given-linter"
    given.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    given.chmod(0o755)
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--linter", str(given), "check"],
                            env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in result.stderr
    assert "the search-path linter" not in result.stderr


# --- the commit hook path of scripts/lint-doeff-cluster.sh (#2906) -- look up HEAD's key binary from the location under HOME ----------------

FAKE_DEV_LINTER = """\
import json, sys
from pathlib import Path
with open(Path(__file__).with_name("calls"), "a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
if "--output-format" in sys.argv:
    print(json.dumps([{"rule": "DOEFF172", "severity": "warning",
                       "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]))
"""


def _uv_dir(uv: str, kind: str) -> str:
    """Locations of the real uv (`uv cache dir` / `uv python dir`) -- so a child with a changed HOME gets the same locations."""
    return subprocess.run([uv, kind, "dir"], capture_output=True, text=True, check=True).stdout.strip()


@dataclass(frozen=True)
class ClusterRig:
    """A temp repo, a child environment with HOME pointed at a temp dir, and the location of the fake land-arm dev build under HOME."""

    repo: Path
    environment: dict[str, str]
    dev: Path


def _cluster_repo(tmp_path: Path) -> ClusterRig:
    """Build a temp repo with 3 scripts, a fake linter crate, and the package baseline committed, a child environment with HOME pointed at a temp dir, and
    the location of the fake land-arm dev build under HOME (the search-path linter leaves a marker and fails when called)."""
    repo: Path = tmp_path / "repo"
    package: Path = repo / "packages" / "doeff-cluster"
    (package / "src").mkdir(parents=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(BASE), encoding="utf-8")
    (package / "architecture.hy").write_text(";; declaration\n", encoding="utf-8")
    (package / "src" / "a.hy").write_text(";; a\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    for script in ("lint-doeff-cluster.sh", "doeff_cluster_warning_baseline.py", "doeff_linter_locked.py"):
        (repo / "scripts" / script).write_text((ROOT / "scripts" / script).read_text(encoding="utf-8"), encoding="utf-8")
    crate: Path = repo / "packages" / "doeff-linter"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "initial"],
                   cwd=repo, check=True)
    # Put .venv at the root as in the real repo: the hook starts the script with `uv run --no-project python`, and uv uses the current dir's .venv.
    # The baseline script reads the environment through doeff effects (05a2a3f94), so a venv with doeff installed is needed -- link the venv running this test
    # (#1201 -- in a rig that did not link it, the script could not import doeff, and the test was red from a rig defect, not from the hook's behavior).
    venv: Path = Path(sys.prefix)
    assert (venv / "pyvenv.cfg").is_file(), f"the test must run inside a venv (link it to the rig's .venv): {venv}"
    (repo / ".venv").symlink_to(venv, target_is_directory=True)
    tools: Path = tmp_path / "bin"
    tools.mkdir()
    (tools / "doeff-linter").write_text(f"#!/bin/sh\ntouch {tmp_path / 'path-linter-called'}\nexit 9\n", encoding="utf-8")
    (tools / "doeff-linter").chmod(0o755)
    real_uv: str | None = shutil.which("uv")
    assert real_uv is not None, "uv is required (to run the script's Python)"
    environment: dict[str, str] = _child_environment(
        PATH=f"{tools}:{Path(real_uv).parent}:/usr/bin:/bin", HOME=str(tmp_path / "home"),
        UV_CACHE_DIR=_uv_dir(real_uv, "cache"), UV_PYTHON_INSTALL_DIR=_uv_dir(real_uv, "python"),
    )
    return ClusterRig(repo=repo, environment=environment, dev=tmp_path / "home" / ".local" / "share" / "doeff-linter-dev")


def _install_dev(dev: Path, repo: Path) -> None:
    """Place the fake land-arm dev build as if built from the repo's HEAD."""
    dev.mkdir(parents=True)
    linter: Path = dev / "doeff-linter"
    linter.write_text(f"#!{sys.executable}\n{FAKE_DEV_LINTER}", encoding="utf-8")
    linter.chmod(0o755)
    head: str = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    (dev / "installed.json").write_text(json.dumps({"commit": head}), encoding="utf-8")


def test_the_cluster_hook_measures_with_the_binary_of_head_and_never_the_path_one(tmp_path: Path) -> None:
    rig: ClusterRig = _cluster_repo(tmp_path)
    _install_dev(rig.dev, rig.repo)
    result = subprocess.run(["sh", "scripts/lint-doeff-cluster.sh", "packages/doeff-cluster/src/a.hy"], cwd=rig.repo,
                            env=rig.environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    calls: list[str] = (rig.dev / "calls").read_text(encoding="utf-8").splitlines()
    assert any("--output-format" in call for call in calls)  # comparison with the warning baseline
    assert any(call.endswith("src/a.hy architecture.hy") for call in calls)  # error judgement
    assert not (tmp_path / "path-linter-called").exists()


def test_the_cluster_hook_names_unmeasured_and_passes_without_a_binary(tmp_path: Path) -> None:
    rig: ClusterRig = _cluster_repo(tmp_path)
    result = subprocess.run(["sh", "scripts/lint-doeff-cluster.sh", "packages/doeff-cluster/src/a.hy"], cwd=rig.repo,
                            env=rig.environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "測れない" in result.stderr
    assert not (tmp_path / "path-linter-called").exists()
