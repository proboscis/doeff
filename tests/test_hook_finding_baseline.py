"""Tests for the tool (scripts/hook_finding_baseline.py) that compares doeff-linter and semgrep (Python) findings in the commit hook against a baseline.

Failure cases (decided in #2848): adding one new finding to a file that has pre-existing findings stops; adding none passes; fixing pre-existing findings
while the baseline is stale is red. The baseline key does not depend on line numbers.
doeff-linter (failure cases of the 2nd part of #2906): the linter on the search path is not called; the binary with the same build-input key as the baseline is looked up in the land-arm dev build, then
the snapshot location; the key does not move on linter-test-only changes but moves on the src of path dependencies (indexer); when absent from the location, it names "cannot measure" and passes;
lower writes the key of the build passed in; when the binary for HEAD's key is absent, it stops without writing.
semgrep (failure cases of #2906): the uv.lock version is called regardless of the search-path semgrep version; a lock version that differs from the baseline is red, by name; with no semgrep in the lock,
it stops without falling back to the search path.
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

import pytest

ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = ROOT / "scripts" / "hook_finding_baseline.py"
LOCKED: Path = ROOT / "scripts" / "semgrep_locked.py"


def _module(name: str, path: Path) -> ModuleType:
    """Load the script as a module to try its pure functions directly (a script that imports a module in the same dir reads that module first)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SEMGREP_LOCKED = _module("semgrep_locked", LOCKED)
LINTER_LOCKED = _module("doeff_linter_locked", ROOT / "scripts" / "doeff_linter_locked.py")
TOOL = _module("hook_finding_baseline", SCRIPT)
BASE: dict[str, dict[str, int]] = {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_a_new_finding_in_a_file_with_old_findings_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 3, "pkg/b.py": 1}}, None)
    assert [(f.path, f.baseline, f.current) for f in comparison.grown] == [("pkg/a.py", 2, 3)]
    assert comparison.stale == ()


def test_the_same_findings_on_moved_lines_pass() -> None:
    # The key is file x rule count -- if lines only shift (same count), it does not show up in the comparison.
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}, None)
    assert comparison.grown == () and comparison.stale == ()


def test_a_fix_without_lowering_the_baseline_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, None)
    assert comparison.grown == ()
    assert [(f.path, f.baseline, f.current) for f in comparison.stale] == [("pkg/b.py", 1, 0)]


def test_only_the_measured_files_are_compared() -> None:
    # The commit hook measures only files that go into the commit -- even if an unmeasured b.py looks like 0, it is not treated as a forgotten lowering.
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, frozenset({"pkg/a.py"}))
    assert comparison.grown == () and comparison.stale == ()


def test_lower_only_lowers() -> None:
    current = {"DOEFF016": {"pkg/a.py": 5, "pkg/b.py": 0}, "DOEFF021": {"pkg/c.py": 4}}
    assert TOOL.lowered(BASE, current) == {"DOEFF016": {"pkg/a.py": 2}}


def test_only_linter_errors_are_counted_and_paths_are_from_the_root(tmp_path: Path) -> None:
    report = [
        {"rule": "DOEFF016", "severity": "error",
         "violations": [{"file": str(tmp_path / "pkg" / "a.py")}, {"file": "pkg/a.py"}, {"file": "pkg/b.py"}]},
        {"rule": "DOEFF009", "severity": "warning", "violations": [{"file": "pkg/a.py"}]},
    ]
    assert TOOL.linter_error_counts(report, tmp_path) == {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_semgrep_results_are_counted_by_the_rule_name(tmp_path: Path) -> None:
    report = {"results": [{"check_id": "tmp.doeff-no-sleep-in-tests", "path": "tests/t.py"},
                          {"check_id": "doeff-no-sleep-in-tests", "path": "tests/t.py"}], "errors": []}
    assert TOOL.semgrep_counts(report, tmp_path) == {"doeff-no-sleep-in-tests": {"tests/t.py": 2}}


# --- From the entrypoint (a temp git repo, and a fake linter that reports the number of BADs in a file as errors) -----------------------------------
# doeff-linter looks up and calls the binary with the same build-input key as the baseline in the land-arm dev build, then the snapshot location (#2906). The rig commits
# a fake crate (linter and path-dependency indexer) into a temp repo, points HOME at a temp dir, and places the fake dev build and snapshot location. The search-path
# doeff-linter leaves a marker when called.

FAKE_LINTER = """\
import json, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print(Path(__file__).with_name("version").read_text().strip())
    raise SystemExit(0)
with open(Path(__file__).with_name("calls"), "a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
paths = [a for a in sys.argv[1:] if a.endswith(".py")]
violations = [{"file": p} for p in paths for line in Path(p).read_text().splitlines() if "BAD" in line]
print(json.dumps([{"rule": "DOEFF999", "severity": "error", "violations": violations}]))
raise SystemExit(1 if violations else 0)
"""

FAKE_PATH_LINTER = """\
from pathlib import Path
Path(__file__).with_name("called").write_text("the search-path doeff-linter was called\\n")
raise SystemExit("the search-path doeff-linter was called")
"""

CRATES: dict[str, str] = {
    "packages/doeff-linter/Cargo.toml": '[package]\nname = "doeff-linter"\n\n[dependencies]\ndoeff-indexer = { path = "../doeff-indexer" }\n',
    "packages/doeff-linter/src/main.rs": "fn main() {}\n",
    "packages/doeff-linter/tests/t.rs": "// test\n",
    "packages/doeff-indexer/Cargo.toml": '[package]\nname = "doeff-indexer"\n',
    "packages/doeff-indexer/src/lib.rs": "// indexer\n",
}


@dataclass(frozen=True)
class Rig:
    """Root of a temp git repo, the child environment (does not copy the caller's), and the fake land-arm dev build and snapshot location."""

    repo: Path
    environment: dict[str, str]
    dev: Path
    snapshots: Path


def _commit(rig: Rig, message: str) -> str:
    """Commit the current tree of the temp repo and return the sha."""
    subprocess.run(["git", "add", "-A"], cwd=rig.repo, env=rig.environment, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", message],
                   cwd=rig.repo, env=rig.environment, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig.repo, env=rig.environment,
                          capture_output=True, text=True, check=True).stdout.strip()


def _place_linter(directory: Path, commit: str) -> Path:
    """Place the fake linter in a directory and have it pose as built from a commit."""
    directory.mkdir(parents=True, exist_ok=True)
    linter = directory / "doeff-linter"
    linter.write_text(f"#!{sys.executable}\n{FAKE_LINTER}")
    linter.chmod(0o755)
    (directory / "version").write_text(f"doeff-linter 0.0.0 (doeff {commit})\n")
    return linter


def _install_dev(rig: Rig, commit: str) -> None:
    """Replace the fake land-arm dev build with the one for a commit (including the commit in the installed.json record)."""
    _place_linter(rig.dev, commit)
    (rig.dev / "installed.json").write_text(json.dumps({"commit": commit}))


def _change_linter_source(rig: Rig, text: str) -> str:
    """Change the linter's src and commit (the build-input key moves)."""
    (rig.repo / "packages" / "doeff-linter" / "src" / "main.rs").write_text(text)
    return _commit(rig, "change linter src")


def _rig(tmp_path: Path) -> Rig:
    """Create a temp git repo (fake crate and Python for pkg) and a fake dev build made from its HEAD."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2\n")
    (repo / "pkg" / "b.py").write_text("z = 3\n")
    for relative, text in CRATES.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    fake = tmp_path / "bin"
    fake.mkdir()
    path_linter = fake / "doeff-linter"
    path_linter.write_text(f"#!{sys.executable}\n{FAKE_PATH_LINTER}")
    path_linter.chmod(0o755)
    # The location is a fixed place under HOME (scripts/doeff_linter_locked.py) -- swap it by pointing HOME at a temp dir.
    # The child starts with its own environment, so it does not inherit the root conftest's pin (PYTHONDONTWRITEBYTECODE=1) -- pass it explicitly so no .pyc is written
    # into the checkout (next to the script / .venv site-packages) (if written, the session-end check conftest.py goes red -- the real daily run on a fresh .venv, 2026-10-10).
    environment = {"PATH": f"{fake}{os.pathsep}{os.defpath}", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(["git", "init", "-q"], cwd=repo, env=environment, check=True)
    rig = Rig(repo=repo, environment=environment, dev=tmp_path / ".local" / "share" / "doeff-linter-dev",
              snapshots=tmp_path / ".cache" / "doeff-linter-snapshots")
    _install_dev(rig, _commit(rig, "initial"))
    return rig


def _run(rig: Rig, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run the script at the temp repo root."""
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(rig.repo), *argv],
                          env=rig.environment, capture_output=True, text=True, check=False)


def _baseline_version(rig: Rig) -> str:
    """The version the temp repo's doeff-linter baseline names."""
    return json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").read_text())["version"]


def test_the_hook_stops_only_a_new_finding_and_a_stale_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    baseline = json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").read_text())
    assert baseline["counts"] == {"DOEFF999": {"pkg/a.py": 1}}

    # Only pre-existing findings (none added) -> passes
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0
    # Add one new finding to a file with pre-existing findings -> stops
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    added = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert added.returncode == 1
    assert "DOEFF999 pkg/a.py: baseline 1 → now 2" in added.stderr
    # Pre-existing findings fixed but baseline stale -> red. Passes after lower.
    (rig.repo / "pkg" / "a.py").write_text("x = 1\ny = 2\n")
    fixed = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert fixed.returncode == 1
    assert "the baseline was not lowered" in fixed.stderr
    assert _run(rig, "lower", "doeff-linter").returncode == 0
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0


def test_the_linter_on_the_path_is_never_called(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0
    assert not (tmp_path / "bin" / "called").exists()
    assert (rig.dev / "calls").exists()


def test_a_tests_only_change_keeps_the_key_and_an_indexer_change_moves_it(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    first = LINTER_LOCKED.input_key(rig.repo, "HEAD")
    assert first is not None
    (rig.repo / "packages" / "doeff-linter" / "tests" / "t.rs").write_text("// add a test\n")
    assert LINTER_LOCKED.input_key(rig.repo, _commit(rig, "linter tests only")) == first
    (rig.repo / "packages" / "doeff-indexer" / "src" / "lib.rs").write_text("// change indexer\n")
    assert LINTER_LOCKED.input_key(rig.repo, _commit(rig, "indexer src")) != first


def test_a_key_written_under_the_former_prefix_still_finds_its_binary(tmp_path: Path) -> None:
    # Stores built before 2026-11-06 wrote the same digest after a Japanese prefix; a store is matched by the digest, so
    # changing the prefix does not leave every machine's linter "unmeasurable" until its inputs change.
    rig = _rig(tmp_path)
    key = LINTER_LOCKED.input_key(rig.repo, "HEAD")
    assert key is not None and key.startswith(LINTER_LOCKED.KEY_PREFIX)
    former = "doeff-linter \u306e\u7d44\u307f\u7acb\u3066\u306e\u5165\u529b " + LINTER_LOCKED.key_digest(key)
    assert LINTER_LOCKED.same_key(former, key)
    assert not LINTER_LOCKED.same_key(former, LINTER_LOCKED.KEY_PREFIX + "0" * 40)
    assert not LINTER_LOCKED.same_key(None, key)
    _record_key(rig, former)
    found = subprocess.run([sys.executable, str(ROOT / "scripts" / "doeff_linter_locked.py"), "which"], cwd=rig.repo,
                           env=rig.environment, capture_output=True, text=True, check=False)
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == str(rig.dev / "doeff-linter")


def test_a_baseline_whose_linter_is_not_built_is_named_as_unmeasured_and_passes(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    built_for = _baseline_version(rig)
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* rules changed */ }\n"))
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    other = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert other.returncode == 0
    assert "not measurable" in other.stderr
    assert built_for in other.stderr
    # The dev build has a different key from the baseline -- add that merging main makes it measurable.
    assert "merging main in" in other.stderr


def test_the_snapshot_store_answers_when_the_dev_build_is_newer(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig.repo, env=rig.environment,
                           capture_output=True, text=True, check=True).stdout.strip()
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* rules changed */ }\n"))
    _place_linter(rig.snapshots / first, first)
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    added = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert added.returncode == 1
    assert "DOEFF999 pkg/a.py: baseline 1 → now 2" in added.stderr
    assert (rig.snapshots / first / "calls").exists()


def test_lower_writes_the_key_of_the_given_build(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    before = _baseline_version(rig)
    changed = _change_linter_source(rig, "fn main() { /* rules changed */ }\n")
    built = _place_linter(tmp_path / "built", changed)
    assert _run(rig, "--linter", str(built), "lower", "doeff-linter").returncode == 0
    assert _baseline_version(rig) == LINTER_LOCKED.input_key(rig.repo, changed)
    assert _baseline_version(rig) != before


def test_writing_without_a_built_linter_for_head_is_named_and_stops(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _change_linter_source(rig, "fn main() { /* rules changed */ }\n")
    missing = _run(rig, "init", "doeff-linter")
    assert missing.returncode != 0
    assert "--linter" in missing.stderr
    assert not (rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").exists()


def test_init_never_overwrites_a_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    again = _run(rig, "init", "doeff-linter")
    assert again.returncode == 2
    assert "init does not rewrite it" in again.stderr


# --- semgrep calls the uv.lock version (#2906) -- the fake uv answers `tool run --python <version> --from semgrep==<version> semgrep`, and the search-path semgrep is a different version ---

LOCK_TEMPLATE = (
    'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
    '\n[[package]]\nname = "other"\nversion = "0.1.0"\n'
)

FAKE_UV = """\
import json, sys
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
with open(here / "uv-calls.jsonl", "a") as out:
    out.write(json.dumps(args) + "\\n")
if args[:3] != ["tool", "run", "--python"] or args[4] != "--from" or args[6] != "semgrep":
    raise SystemExit(f"call unknown to the fake uv: {args}")
version, rest = args[5].split("==")[1], args[7:]
if "--version" in rest:
    print(version)
    raise SystemExit(0)
paths = [a for a in rest if a.endswith(".py")]
results = [{"check_id": "R1", "path": p} for p in paths for line in Path(p).read_text().splitlines() if "BAD" in line]
print(json.dumps({"results": results, "errors": []}))
"""

FAKE_PATH_SEMGREP = """\
import sys
if "--version" in sys.argv:
    print("9.9.9")
    raise SystemExit(0)
raise SystemExit("the search-path semgrep was called")
"""


def _semgrep_rig(tmp_path: Path) -> Rig:
    """Place a uv.lock (semgrep 1.2.3) in a temp repo, plus a fake uv and a search-path semgrep that poses as a different version."""
    rig = _rig(tmp_path)
    (rig.repo / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.2.3"))
    for name, body in (("uv", FAKE_UV), ("semgrep", FAKE_PATH_SEMGREP)):
        tool = tmp_path / "bin" / name
        tool.write_text(f"#!{sys.executable}\n{body}")
        tool.chmod(0o755)
    return rig


def test_the_locked_command_names_the_lock_version(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.169.0"))
    # The tool's python is the version of the python running the script (the version the hook config declares with `uv run --python`) -- do not let uv tool run pick the tree's .python-version
    # (3.14t).
    running: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert SEMGREP_LOCKED.locked_command(tmp_path) == [
        "uv", "tool", "run", "--python", running, "--from", "semgrep==1.169.0", "semgrep",
    ]


def test_semgrep_is_called_at_the_lock_version_whatever_the_path_has(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    baseline = json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "semgrep.json").read_text())
    assert baseline == {"version": "1.2.3", "counts": {"R1": {"pkg/a.py": 1}}}
    (rig.repo / "pkg" / "b.py").write_text("z = 3  # BAD\n")
    added = _run(rig, "check", "semgrep", "pkg/b.py")
    assert added.returncode == 1
    assert "R1 pkg/b.py: baseline 0 → now 1" in added.stderr
    calls = [json.loads(line) for line in (tmp_path / "bin" / "uv-calls.jsonl").read_text().splitlines()]
    assert calls
    assert all(call[:3] == ["tool", "run", "--python"] and call[4:7] == ["--from", "semgrep==1.2.3", "semgrep"] for call in calls)


def test_a_lock_version_other_than_the_baseline_is_named_and_red(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    (rig.repo / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.2.4"))
    other = _run(rig, "check", "semgrep", "pkg/a.py")
    assert other.returncode == 1
    assert "1.2.3" in other.stderr
    assert "1.2.4" in other.stderr
    assert "lower semgrep" in other.stderr


def test_a_lock_without_semgrep_stops_without_falling_back_to_the_path(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    calls = tmp_path / "bin" / "uv-calls.jsonl"
    before = calls.read_text()
    (rig.repo / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "other"\nversion = "0.1.0"\n')
    missing = _run(rig, "check", "semgrep", "pkg/a.py")
    assert missing.returncode != 0
    assert "is not a single one" in missing.stderr
    assert calls.read_text() == before


def test_a_lock_row_outside_the_index_stops_without_calling_the_tool(tmp_path: Path) -> None:
    """If the lock's semgrep line is not a version installed from an index (git etc.), `semgrep==<version>` would install something else from the index, so the reader
    stops by name and does not call the tool (a single check that reads the uv.lock version -- same as the test measured against the Hy version)."""
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    calls = tmp_path / "bin" / "uv-calls.jsonl"
    before = calls.read_text()
    (rig.repo / "uv.lock").write_text(
        LOCK_TEMPLATE.format(version="1.2.3").replace(
            'registry = "https://pypi.org/simple"', 'git = "https://github.com/semgrep/semgrep.git?rev=0123abc"'
        )
    )
    outside = _run(rig, "check", "semgrep", "pkg/a.py")
    assert outside.returncode != 0
    assert "is not a version installed from an index" in outside.stderr
    assert calls.read_text() == before


# --- daily stage (4th part of #2906) -- in a tree without git (a copy of remote_check), check against the key written at build time and make "cannot measure" red ---------


def _copy_without_git(rig: Rig, tmp_path: Path) -> Path:
    """Copy the temp repo without .git (the daily stage's tree = only the files in the git ls-files list)."""
    copy = tmp_path / "copy"
    shutil.copytree(rig.repo, copy, ignore=shutil.ignore_patterns(".git"))
    return copy


def _record_key(rig: Rig, key: str) -> None:
    """Write the build-time key into the fake land-arm dev build's record, and make the record's commit one git does not know."""
    record = rig.dev / "installed.json"
    record.write_text(json.dumps({"commit": "0" * 40, "input_key": key}))


def test_a_tree_without_git_walks_its_files_and_skips_made_dirs(tmp_path: Path) -> None:
    for relative in ("a.py", "sub/b.pyi", "sub/c.txt", ".venv/x.py", "__pycache__/y.py", "node_modules/z.py"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("x = 1\n")
    assert TOOL.population(tmp_path, "doeff-linter") == ["a.py", "sub/b.pyi"]


def test_strict_turns_unmeasured_red(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* rules changed */ }\n"))
    strict = _run(rig, "--strict", "check", "doeff-linter")
    assert strict.returncode == TOOL.UNMEASURED_RC
    assert "not measurable" in strict.stderr
    # The hook (without --strict) passes on the same tree
    assert _run(rig, "check", "doeff-linter").returncode == 0


def test_strict_names_a_linter_changed_past_the_baseline_and_turns_it_red(tmp_path: Path) -> None:
    # Failure case (blind A of the design review): even in a tree where the linter's src changed and the baseline was not recounted, if the binary for the
    # baseline key existed, the daily run measured silently with a linter whose rules were stale and did not name the key difference (actual: 10-03 to 10-11 had 28 commits that changed linter inputs and
    # 0 commits that wrote the baseline).
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    built_for = _baseline_version(rig)
    changed = _change_linter_source(rig, "fn main() { /* rules changed */ }\n")
    moved = _run(rig, "--strict", "check", "doeff-linter")
    assert moved.returncode == 1
    assert built_for in moved.stderr
    assert str(LINTER_LOCKED.input_key(rig.repo, changed)) in moved.stderr
    assert "lower doeff-linter" in moved.stderr
    # The hook (without --strict) measures with the linter at the baseline key and passes -- another person's linter change does not stop every later commit.
    assert _run(rig, "check", "doeff-linter").returncode == 0
    # If the change that altered the linter recounts with its own build, the daily run passes too.
    built = _place_linter(tmp_path / "built", changed)
    assert _run(rig, "--linter", str(built), "lower", "doeff-linter").returncode == 0
    _install_dev(rig, changed)
    assert _run(rig, "--strict", "check", "doeff-linter").returncode == 0


def test_a_recorded_key_measures_a_tree_without_git(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _record_key(rig, _baseline_version(rig))
    copy = _copy_without_git(rig, tmp_path)
    run = [sys.executable, str(SCRIPT), "--root", str(copy), "--strict", "check", "doeff-linter"]
    same = subprocess.run(run, env=rig.environment, capture_output=True, text=True, check=False)
    assert same.returncode == 0, same.stderr
    assert "walking the dir" in same.stderr
    (copy / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    grown = subprocess.run(run, env=rig.environment, capture_output=True, text=True, check=False)
    assert grown.returncode == 1
    assert "DOEFF999 pkg/a.py: baseline 1 → now 2" in grown.stderr


def test_a_snapshot_with_a_recorded_key_is_found_without_git(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    snapshot = rig.snapshots / ("f" * 40)
    _place_linter(snapshot, "f" * 40)
    (snapshot / "input_key").write_text(_baseline_version(rig) + "\n")
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* rules changed */ }\n"))
    copy = _copy_without_git(rig, tmp_path)
    found = subprocess.run([sys.executable, str(SCRIPT), "--root", str(copy), "--strict", "check", "doeff-linter"],
                           env=rig.environment, capture_output=True, text=True, check=False)
    assert found.returncode == 0, found.stderr
    assert (snapshot / "calls").exists()


def test_the_key_entry_prints_the_key_of_a_commit(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    printed = subprocess.run([sys.executable, str(ROOT / "scripts" / "doeff_linter_locked.py"), "key", "HEAD"],
                             cwd=rig.repo, env=rig.environment, capture_output=True, text=True, check=False)
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == LINTER_LOCKED.input_key(rig.repo, "HEAD")


def test_the_script_reads_its_sibling_modules_under_a_safe_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under PYTHONSAFEPATH=1 (present in the worker's shell) the script's dir does not go on sys.path. It still reads the neighboring modules
    # (doeff_linter_locked, semgrep_locked) and reaches the entrypoint -- with no arguments it prints usage and exits 2 (1 if it fails on import).
    # The child inherits the test process's environment and adds only PYTHONSAFEPATH (monkeypatch restores it after the test).
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    ran = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert "ModuleNotFoundError" not in ran.stderr, ran.stderr
    assert ran.returncode == 2, ran.stderr
