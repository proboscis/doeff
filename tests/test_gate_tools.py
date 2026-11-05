"""Declaration and preparation of the tools at the place where the daily whole-verification runs.

The daily whole-verification (gate.full in .agents/land-queue.toml) has run directly since 2026-10-10 at the place where the daily task runs (the doeff-worker Pod on atlas)
(ADR-DOE-ENFORCE-001 R10 -- until then it sent the tree to zeus with remote_check and ran there). The real-Redis delivery laws
(packages/doeff-events/tests/test_notice_laws_redis.py) start the redis-server on PATH, so if the place where it runs has no redis-server, not a single one
runs -- the 2026-10-08 8:00 run skipped all 14 (the tests ran on zeus, and zeus's PATH has no redis-server). The daily task
places the declared version of redis-server in the task's HOME .local/bin (the head of PATH).

The single point declaring tool dependencies = scripts/gate_tools.sh (name, version, source). The processing stage packages runs
`sh scripts/gate_tools.sh path` before the tests and puts the dir it answers at the front of PATH. The processing stage lint runs
`sh scripts/gate_tools.sh linter` before the baseline comparison and prepares the doeff-linter with the key named by the baseline file in the snapshot location the comparison tool looks in
(the zeus land-arm dev build and snapshot location are absent from the task's HOME, which is empty on every run -- without preparing them, --strict is red with "cannot measure").

- (a) Declaration: the packages processing stage runs the tool preparation before make test-packages, and if preparation fails it does not run make test-packages
  (&&).
- (b) On a machine that cannot satisfy the dependency (not on PATH, not in the cache, and no docker to fetch the tool), preparation exits nonzero and lists the tool name,
  version, image, and the PATH searched -- the processing stage is red and never silently becomes a skip.
- (c) If PATH has a redis-server of the declared version, nothing is added (the daily task's .local/bin). If it is in the cache dir, answer that dir.
- (d) A redis-server of a different version on PATH is not counted as the declared version (with no cache or fetch, the same red as (b)).
- (e) Fetch: extract one executable from the declared image (with digest), place it in the cache dir, and answer that dir.
- (f) Declaration: the lint processing stage runs the linter preparation (&&) before the doeff-linter baseline comparison (--strict).
- (g) Linter preparation: link the caller's DOEFF_LINTER_SNAPSHOT_DIR (a location that persists across runs) to the snapshot location ($HOME/.cache/doeff-linter-snapshots
  -- where scripts/doeff_linter_locked.py looks), and prepare in that location the linter of the commit that last wrote the baseline file, using
  packages/doeff-linter/scripts/linter_snapshot.py (do not build if present; copy if a linter with the same key is present).
- (h) On a machine where the snapshot location already exists, or for a caller without DOEFF_LINTER_SNAPSHOT_DIR, do not relink the location.
- (i) If the commit that wrote the baseline is not in git or cannot be built, preparation exits nonzero and names it (the processing stage is red).
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from doeff_core_effects import subprocess_handler
from doeff_core_effects import EnvEntry, EnvMode, ProcessOutcome, RunProcess

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_TOOLS = REPO_ROOT / "scripts" / "gate_tools.sh"
LAND_QUEUE = REPO_ROOT / ".agents" / "land-queue.toml"

REDIS_VERSION = "7.4.11"
# The same value as REDIS_IMAGE in agora's deploy/verify-worker-image/Dockerfile (the same version as redis:7.4.11 in production deploy/agora-events).
REDIS_IMAGE = (
    "redis:7.4.11-bookworm@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85"
)
# Tools the preparation script uses (link this machine's into the stand-in PATH dir -- docker, redis-server, git, and uv are not included).
SCRIPT_TOOLS = ("chmod", "dirname", "grep", "ln", "mkdir", "mktemp", "mv", "rm", "rmdir")
# Stand-in for the commit that last wrote the baseline file (the answer of git log).
BASELINE_COMMIT = "1" * 40


def _version_answer(version: str) -> str:
    """A stand-in that answers `redis-server --version` (the same one-line shape as the real one)."""
    return (
        "#!/bin/sh\n"
        f'echo "Redis server v={version} sha=00000000:0 malloc=libc bits=64 build=0000000000000000"\n'
    )


def _stand_in_bin(directory: Path, *, redis_version: str | None = None, docker: bool = False) -> Path:
    """A PATH dir holding only the tools the preparation script needs (redis-server and docker get a stand-in only when requested)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_TOOLS:
        found = shutil.which(name)
        assert found is not None, f"this machine has no {name} (a precondition of the preparation script)"
        (directory / name).symlink_to(found)
    if redis_version is not None:
        _write_tool(directory / "redis-server", _version_answer(redis_version))
    if docker:
        _write_tool(directory / "docker", _docker_stand_in(directory.parent / "docker-calls"))
    return directory


def _docker_stand_in(record: Path) -> str:
    """A docker stand-in answering create, cp, and rm: write create's image to the record, and cp places a stand-in of the declared version."""
    answer = _version_answer(REDIS_VERSION).replace("\n", "\\n").replace('"', '\\"')
    return (
        "#!/bin/sh\n"
        f'echo "$*" >> "{record}"\n'
        'case "$1" in\n'
        '  create) echo stand-in-container ;;\n'
        f'  cp) printf "{answer}" > "$3" ;;\n'
        "  rm) : ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )


def _write_tool(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _prepare(
    path_dir: Path, home: Path, verb: str = "path", env: tuple[EnvEntry, ...] = ()
) -> ProcessOutcome:
    """Run `sh scripts/gate_tools.sh <verb>` in a child process that sees only the stand-in PATH, HOME (and env)."""
    home.mkdir(parents=True, exist_ok=True)
    done = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=("/bin/sh", str(GATE_TOOLS), verb),
                env=(
                    EnvEntry(name="PATH", value=str(path_dir)),
                    EnvEntry(name="HOME", value=str(home)),
                    *env,
                ),
                env_mode=EnvMode.REPLACE,
                timeout=30.0,
            ),
        )
    )
    assert not done.timed_out, f"the preparation script did not finish in 30 seconds\n{done.stdout}\n{done.stderr}"
    return done


def _cache(home: Path) -> Path:
    return home / ".cache" / f"redis-server-{REDIS_VERSION}"


def test_a_the_packages_stage_prepares_the_declared_tools_before_its_tests() -> None:
    stages = {stage["name"]: stage["run"] for stage in tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]}
    packages = stages["packages"]
    prepared = "gate_path=$(sh scripts/gate_tools.sh path) && PATH=$gate_path$PATH "
    assert prepared in packages, packages
    assert packages.index(prepared) < packages.index("make test-packages"), packages


def test_b_a_machine_that_cannot_provide_the_tool_fails_naming_it(tmp_path: Path) -> None:
    path_dir = _stand_in_bin(tmp_path / "bin")
    done = _prepare(path_dir, tmp_path / "home")
    assert done.exit_code == 1, done
    assert done.stdout == "", done
    for named in ("redis-server", REDIS_VERSION, REDIS_IMAGE, str(path_dir)):
        assert named in done.stderr, (named, done.stderr)


def test_c_the_declared_version_on_path_adds_nothing(tmp_path: Path) -> None:
    done = _prepare(_stand_in_bin(tmp_path / "bin", redis_version=REDIS_VERSION), tmp_path / "home")
    assert (done.exit_code, done.stdout) == (0, ""), done


def test_c_the_cached_tool_is_answered_as_a_path_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _cache(home).mkdir(parents=True)
    _write_tool(_cache(home) / "redis-server", _version_answer(REDIS_VERSION))
    done = _prepare(_stand_in_bin(tmp_path / "bin"), home)
    assert (done.exit_code, done.stdout) == (0, f"{_cache(home)}:"), done


def test_d_another_version_on_path_is_not_the_declared_tool(tmp_path: Path) -> None:
    done = _prepare(_stand_in_bin(tmp_path / "bin", redis_version="7.0.15"), tmp_path / "home")
    assert done.exit_code == 1, done
    assert REDIS_IMAGE in done.stderr, done.stderr


def test_e_the_tool_is_fetched_from_the_declared_image_into_the_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    done = _prepare(_stand_in_bin(tmp_path / "bin", docker=True), home)
    assert (done.exit_code, done.stdout) == (0, f"{_cache(home)}:"), done
    calls = (tmp_path / "docker-calls").read_text("utf-8").splitlines()
    assert calls[0] == f"create {REDIS_IMAGE}", calls
    assert calls[1].startswith("cp stand-in-container:/usr/local/bin/redis-server "), calls
    assert calls[2] == "rm stand-in-container", calls
    assert f"v={REDIS_VERSION} " in (_cache(home) / "redis-server").read_text("utf-8")


def _linter_stand_ins(directory: Path, *, commit: str, uv_rc: int = 0) -> Path:
    """A PATH dir with stand-ins for the git and uv that the linter preparation calls: git answers `log` with a commit (answers nothing if empty), and uv
    writes the arguments it was called with and DOEFF_LINTER_SNAPSHOT_DIR to the record and exits with uv_rc."""
    path_dir = _stand_in_bin(directory)
    record = directory.parent / "calls"
    _write_tool(
        path_dir / "git",
        f'#!/bin/sh\necho "git $*" >> "{record}"\ncase "$*" in *" log "*) [ -n "{commit}" ] && echo "{commit}" ;; esac\nexit 0\n',
    )
    _write_tool(
        path_dir / "uv",
        f'#!/bin/sh\necho "uv store=$DOEFF_LINTER_SNAPSHOT_DIR $*" >> "{record}"\nexit {uv_rc}\n',
    )
    return path_dir


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls").read_text("utf-8").splitlines()


def _linter_store(home: Path) -> Path:
    """The snapshot location scripts/doeff_linter_locked.py looks in (a fixed place under HOME)."""
    return home / ".cache" / "doeff-linter-snapshots"


def test_f_the_lint_stage_prepares_the_baseline_linter_before_the_strict_check() -> None:
    stages = {stage["name"]: stage["run"] for stage in tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]}
    lint = stages["lint"]
    prepared = "sh scripts/gate_tools.sh linter && "
    assert prepared in lint, lint
    assert lint.index(prepared) < lint.index("hook_finding_baseline.py --strict check doeff-linter"), lint


def test_g_the_linter_is_prepared_from_the_baseline_commit_into_the_kept_store(tmp_path: Path) -> None:
    home = tmp_path / "home"
    kept = tmp_path / "work" / "daily-verify-cache" / "doeff-linter-snapshots"
    path_dir = _linter_stand_ins(tmp_path / "bin", commit=BASELINE_COMMIT)
    done = _prepare(path_dir, home, "linter", (EnvEntry(name="DOEFF_LINTER_SNAPSHOT_DIR", value=str(kept)),))
    assert (done.exit_code, done.stdout) == (0, ""), done
    # Link the location that persists across runs to the location the comparison tool looks in (the task's HOME is empty on every run).
    assert _linter_store(home).is_symlink(), done
    assert _linter_store(home).resolve() == kept.resolve()
    calls = _calls(tmp_path)
    assert f"git -C {REPO_ROOT} log -1 --format=%H -- scripts/hook_finding_baseline/doeff-linter.json" in calls, calls
    snapshot = REPO_ROOT / "packages" / "doeff-linter" / "scripts" / "linter_snapshot.py"
    assert f"uv store={_linter_store(home)} run --script {snapshot} {REPO_ROOT} {BASELINE_COMMIT}" in calls, calls


def test_h_an_existing_store_and_a_caller_without_a_kept_store_are_not_relinked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _linter_store(home).mkdir(parents=True)
    kept = tmp_path / "kept"
    path_dir = _linter_stand_ins(tmp_path / "bin", commit=BASELINE_COMMIT)
    done = _prepare(path_dir, home, "linter", (EnvEntry(name="DOEFF_LINTER_SNAPSHOT_DIR", value=str(kept)),))
    assert done.exit_code == 0, done
    assert not _linter_store(home).is_symlink() and not kept.exists()

    other_home = tmp_path / "other-home"
    done = _prepare(path_dir, other_home, "linter")
    assert done.exit_code == 0, done
    assert not _linter_store(other_home).is_symlink()
    assert f"uv store={_linter_store(other_home)} run --script" in "\n".join(_calls(tmp_path)), _calls(tmp_path)


def test_i_a_missing_baseline_commit_or_a_failed_build_is_named_and_red(tmp_path: Path) -> None:
    no_commit = _prepare(_linter_stand_ins(tmp_path / "a" / "bin", commit=""), tmp_path / "a" / "home", "linter")
    assert no_commit.exit_code == 1, no_commit
    assert "scripts/hook_finding_baseline/doeff-linter.json" in no_commit.stderr, no_commit.stderr
    assert not any(call.startswith("uv ") for call in _calls(tmp_path / "a")), _calls(tmp_path / "a")

    failed = _prepare(
        _linter_stand_ins(tmp_path / "b" / "bin", commit=BASELINE_COMMIT, uv_rc=1), tmp_path / "b" / "home", "linter"
    )
    assert failed.exit_code == 1, failed
    assert BASELINE_COMMIT in failed.stderr and "doeff-linter" in failed.stderr, failed.stderr
