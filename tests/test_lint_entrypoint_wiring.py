"""Verify, by running them, that the existing lint entrypoint rejects missing measurements and that pre-commit selects the right hooks for Python/Hy diffs."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from doeff_core_effects import EnvEntry, EnvMode, ProcessOutcome, RunProcess, subprocess_handler

from doeff import run, with_handlers

ROOT: Path = Path(__file__).resolve().parents[1]


def _linter_locked() -> ModuleType:
    """Load the module with which the hook's doeff-linter entry computes its key, so a fake crate's key is produced by the same computation."""
    spec = importlib.util.spec_from_file_location("doeff_linter_locked", ROOT / "scripts" / "doeff_linter_locked.py")
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass looks the module up in sys.modules
    spec.loader.exec_module(module)
    return module


LINTER_LOCKED: ModuleType = _linter_locked()


# The hook's Python declaration (the `uv run --python` version in .pre-commit-config.yaml).
HOOK_PYTHON: str = "3.12"
FAKE_VERSIONS: dict[str, str] = {"semgrep": "0.0.0-fake", "doeff-linter": "doeff-linter 0.0.0 (fake)"}
# Source of the fake lock line -- the tool-version reader (scripts/semgrep_locked.py) reads only lines for versions installed from an index.
FAKE_SOURCE: str = 'source = { registry = "https://pypi.org/simple" }\n'
# The JSON answer read by the baseline-comparison path (scripts/hook_finding_baseline.py) -- a report with no findings.
FAKE_REPORTS: dict[str, str] = {"semgrep": '{"results": [], "errors": []}', "doeff-linter": "[]"}


def _tool(directory: Path, name: str, exit_code: int, *, report: str | None = None) -> None:
    """A fake tool that records its arguments, answers --version with a fake version, and answers calls asking for JSON with a fake report (a report, or a report with no findings)."""
    answer: str = report if report is not None else FAKE_REPORTS.get(name, "")
    path: Path = directory / name
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['LINT_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
        "if '--version' in sys.argv:\n"
        f"    print({FAKE_VERSIONS.get(name, '0')!r})\n"
        "    raise SystemExit(0)\n"
        "if '--json' in sys.argv or 'json' in sys.argv:\n"
        f"    print({answer!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


# The hook's 3 semgrep entries call the uv.lock version via `uv tool run --python <version> --from semgrep==<version> semgrep` (#2906). The fake uv
# records that call and routes it to the fake semgrep in the same dir; other calls (`uv run --no-project --python ... python ...`) are passed to the real uv.
# The plain hook (scripts/git-hooks/pre-commit) calls pre-commit in the same form too -- the fake uv records it and routes it to the test environment's pre-commit.
FAKE_UV: str = (
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "if args[:3] == ['tool', 'run', '--python'] and args[4] == '--from' and args[6] in ('semgrep', 'pre-commit'):\n"
    "    with open(os.environ['LINT_CALLS'], 'a') as output:\n"
    "        output.write(json.dumps(['uv', *args[:7]]) + '\\n')\n"
    "    if args[6] == 'pre-commit':\n"
    "        os.execv(sys.executable, [sys.executable, '-m', 'pre_commit', *args[7:]])\n"
    "    semgrep = os.path.join(os.path.dirname(sys.argv[0]), 'semgrep')\n"
    "    os.execv(semgrep, [semgrep, *args[7:]])\n"
    "os.execv(os.environ['REAL_UV'], [os.environ['REAL_UV'], *args])\n"
)


def _uv_dir(uv: str, kind: str) -> str:
    """Locations of the real uv (`uv cache dir` / `uv python dir`) -- so a child with a changed HOME gets the same locations."""
    return subprocess.run([uv, kind, "dir"], capture_output=True, text=True, check=True).stdout.strip()


def _environment(directory: Path, path: str | None = None) -> tuple[EnvEntry, ...]:
    """The part layered over the current environment for the child (path = the child's PATH; default is the fake tools' dir plus /usr/bin and /bin). Inheriting the current environment and
    dropping names (SKIP etc.) is done by the doeff handler that starts the child (_run_child)."""
    real_uv: str | None = shutil.which("uv")
    assert real_uv is not None, "uv is required (to run the hook script)"
    fake_uv: Path = directory / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    fake_uv.chmod(0o755)
    return (
        EnvEntry(name="PATH", value=path if path is not None else f"{directory}:/usr/bin:/bin"),
        EnvEntry(name="REAL_UV", value=real_uv),
        EnvEntry(name="LINT_CALLS", value=str(directory / "calls.jsonl")),
        EnvEntry(name="PRE_COMMIT_HOME", value=str(directory / "pre-commit-cache")),
        EnvEntry(name="PRE_COMMIT_ALLOW_NO_CONFIG", value="0"),
        # The hook's doeff-linter entry looks for the baseline key's binary in the land-arm dev build under HOME, then in the snapshot location (#2906) --
        # point HOME at a temp dir (the dev build is placed by _repository; the snapshot location is empty = the machine's files are not read), and leave the uv cache and Python
        # locations pointing at the real ones (so uv does not refetch even though HOME changed).
        EnvEntry(name="HOME", value=str(directory)),
        EnvEntry(name="UV_CACHE_DIR", value=_uv_dir(real_uv, "cache")),
        EnvEntry(name="UV_PYTHON_INSTALL_DIR", value=_uv_dir(real_uv, "python")),
    )


def _run_child(
    argv: tuple[str, ...], cwd: Path, overlay: tuple[EnvEntry, ...], drop: tuple[str, ...] = ("SKIP",),
) -> ProcessOutcome:
    """Start the child via doeff's child-process handler (subprocess_handler) -- the child's environment is the current one minus the dropped names, with the overlay
    layered on (inheritance is done inside the handler)."""
    return run(with_handlers(
        [subprocess_handler],
        RunProcess(argv=argv, cwd=str(cwd), env=overlay, env_mode=EnvMode.EXTEND, env_drop=drop),
    ))


@pytest.mark.parametrize("tool_status", [None, 0, 1, 2])
def test_make_lint_doeff_propagates_missing_and_tool_failure(
    tmp_path: Path, tool_status: int | None,
) -> None:
    if tool_status is not None:
        _tool(tmp_path, "doeff-linter", tool_status)
    # The packages/doeff-cluster stage (#2031, #2683) compares warnings with a baseline, so give it an empty baseline matching the fake linter (which reports nothing),
    # and add to the search path the uv that runs the comparison script (the real doeff-linter is not put on the search path).
    baseline: Path = tmp_path / "empty-baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv is required (to run the comparison script)"
    environment: tuple[EnvEntry, ...] = (
        *_environment(tmp_path, f"{tmp_path}:{Path(uv).parent}:/usr/bin:/bin"),
        EnvEntry(name="DOEFF_CLUSTER_WARNING_BASELINE", value=str(baseline)),
    )
    result: ProcessOutcome = _run_child(
        ("/usr/bin/make", "-f", str(ROOT / "Makefile"), "lint-doeff"), tmp_path, environment,
    )
    assert (result.exit_code == 0) == (tool_status == 0), result.stdout + result.stderr
    if tool_status is None:
        assert "doeff-linter" in result.stderr
        assert not (tmp_path / "calls.jsonl").exists()
    else:
        calls: list[list[str]] = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
        assert calls[0] == ["doeff-linter", "--no-log", "doeff/", "packages/"]
        assert calls[1] == ["doeff-linter", "--no-log"]  # the whole package, in the packages/doeff-cluster dir
        if tool_status == 0:
            assert calls[2] == ["doeff-linter", "--no-log", "--output-format", "json"]  # comparison with the warning baseline


def _commit_fake_crate(repository: Path, directory: Path) -> str:
    """Commit a fake linter crate and, under HOME (= directory), place a fake land-arm dev build as if built from that commit
    (a copy of the fake linter on the search path plus the installed.json record), and return the build-input key."""
    crate: Path = repository / "packages" / "doeff-linter"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", "packages"], cwd=repository, check=True)
    subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "crate"],
                   cwd=repository, check=True)
    commit: str = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=repository,
                                 capture_output=True, text=True, check=True).stdout.strip()
    dev: Path = directory / ".local" / "share" / "doeff-linter-dev"
    dev.mkdir(parents=True)
    if (directory / "doeff-linter").is_file():
        shutil.copy2(directory / "doeff-linter", dev / "doeff-linter")
    (dev / "installed.json").write_text(json.dumps({"commit": commit}), encoding="utf-8")
    key: str | None = LINTER_LOCKED.input_key(repository, commit)
    assert key is not None
    return key


def _repository(directory: Path, changed: str, source: str) -> Path:
    repository: Path = directory / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    shutil.copyfile(ROOT / ".pre-commit-config.yaml", repository / ".pre-commit-config.yaml")
    # Copy the tree's Python declaration (3.14t) too -- the hook's Python is decided by the config declaration, not the tree default.
    shutil.copyfile(ROOT / ".python-version", repository / ".python-version")
    # The Python semgrep and doeff-linter entries go through the baseline-comparison script (#2848) -- place the script and an empty baseline matching the fake tools' versions.
    (repository / "scripts" / "hook_finding_baseline").mkdir(parents=True)
    # The entry that quotes the owner's chat words (no-chat-quotes) applies to any text file -- place that script too.
    for script in ("hook_finding_baseline.py", "semgrep_locked.py", "doeff_linter_locked.py", "check_no_operator_quotes.py"):
        shutil.copyfile(ROOT / "scripts" / script, repository / "scripts" / script)
    # The semgrep version is decided by uv.lock (#2906) -- match the fake tools' version (the line is a version installed from an index; the reader stops at lines outside an index).
    (repository / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "{FAKE_VERSIONS["semgrep"]}"\n{FAKE_SOURCE}', encoding="utf-8",
    )
    # The doeff-linter version is the build-input key (#2906) -- commit a fake crate, write its key into the baseline, and record the fake dev build at that commit.
    linter_key: str = _commit_fake_crate(repository, directory)
    for tool, version in (("semgrep", FAKE_VERSIONS["semgrep"]), ("doeff-linter", linter_key)):
        (repository / "scripts" / "hook_finding_baseline" / f"{tool}.json").write_text(
            json.dumps({"version": version, "counts": {}}), encoding="utf-8",
        )
    changed_path: Path = repository / changed
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text(source)
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
    return repository


@dataclass(frozen=True)
class _HookRun:
    """The result of one pre-commit run (result) and the sequence of calls the fake tools received (calls -- one call is a list of arguments)."""

    result: ProcessOutcome
    calls: list[list[str]]


def _pre_commit(
    directory: Path, changed: str, *, semgrep_status: int | None = 0, linter_report: str | None = None,
) -> _HookRun:
    """Run pre-commit with only the changed file in a sample repo with the fake tools at the head of the path."""
    _tool(directory, "doeff-linter", 0, report=linter_report)
    if semgrep_status is not None:
        _tool(directory, "semgrep", semgrep_status)
    repository: Path = _repository(directory, changed, "value = 1\n")
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv is required (to run the baseline-comparison script)"
    # Do not let the child inherit the outer `uv run pytest` venv (VIRTUAL_ENV) -- if inherited, the hook's `uv run` puts that venv's bin at the head of the search path,
    # and the real semgrep hides the fake tools.
    result: ProcessOutcome = _run_child(
        (sys.executable, "-m", "pre_commit", "run", "--files", changed),
        repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"),
        ("SKIP", "VIRTUAL_ENV"),
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()]
        if calls_path.exists() else []
    )
    return _HookRun(result=result, calls=calls)


@pytest.mark.parametrize("changed", [
    "doeff/example.py", "packages/example.hy", "packages/example.hyk", "packages/example.hyp",
])
def test_pre_commit_runs_matching_linter_on_only_the_changed_file(
    tmp_path: Path, changed: str,
) -> None:
    hook = _pre_commit(tmp_path, changed)
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    semgrep_calls: list[list[str]] = [call for call in hook.calls if call[0] == "semgrep" and "--version" not in call]
    assert len(semgrep_calls) == 1, hook.result.stdout + hook.result.stderr
    # Every entry calls the uv.lock version from uv's tool location with the python the config declares (never calling the search-path semgrep directly --
    # ruamel-yaml-clib cannot be built under the tree's .python-version 3.14t).
    assert [call for call in hook.calls if call[0] == "uv"] == [
        ["uv", "tool", "run", "--python", HOOK_PYTHON, "--from", "semgrep==0.0.0-fake", "semgrep"],
    ]
    assert semgrep_calls[0][-1] == changed
    # Python is counted as JSON by the baseline-comparison script (#2848); Hy stops at one semgrep finding (--error).
    assert ("--json" if changed.endswith(".py") else "--error") in semgrep_calls[0]
    assert "doeff/" not in semgrep_calls[0]
    assert "packages/" not in semgrep_calls[0]
    python_calls: list[list[str]] = [call for call in hook.calls if call[0] == "doeff-linter" and "--version" not in call]
    assert len(python_calls) == int(changed.endswith(".py"))


def test_python_change_with_a_linter_error_not_in_the_baseline_is_stopped(tmp_path: Path) -> None:
    # One error absent from the (empty) baseline is in the changed file -> stops (the failure case of #2848 -- passing when there are only pre-existing findings is checked in the script's test).
    report: str = json.dumps([{"rule": "DOEFF016", "severity": "error", "violations": [{"file": "doeff/example.py"}]}])
    result = _pre_commit(tmp_path, "doeff/example.py", linter_report=report).result
    assert result.exit_code != 0, result.stdout + result.stderr
    assert "DOEFF016 doeff/example.py: 基点 0 → 今 1" in result.stdout + result.stderr


@pytest.mark.parametrize("tool_status", [None, 1, 2])
def test_hy_only_change_rejects_missing_or_failing_semgrep(
    tmp_path: Path, tool_status: int | None,
) -> None:
    hook = _pre_commit(tmp_path, "packages/example.hy", semgrep_status=tool_status)
    assert hook.result.exit_code != 0, hook.result.stdout + hook.result.stderr
    assert not any(call[0] == "doeff-linter" for call in hook.calls)


def test_unrelated_document_does_not_require_python_or_hy_linters(tmp_path: Path) -> None:
    hook = _pre_commit(tmp_path, "notes.md", semgrep_status=None)
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    assert hook.calls == []


@pytest.mark.parametrize("nested", [False, True])
def test_hy_only_hook_runs_real_semgrep_handler_boundary_rule(
    tmp_path: Path, nested: bool,
) -> None:
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "the real-sample check needs the dev-dependency semgrep"
    (tmp_path / "semgrep").symlink_to(executable)
    changed: str = "packages/example/src/handlers/example.hy"
    source: str = (
        "(defn factory []\n  (defhandler nested [] (object [] (resume None))))\n"
        if nested else "(defhandler top-level [] (object [] (resume None)))\n"
    )
    repository: Path = _repository(tmp_path, changed, source)
    rule_id: str = "doeff-hy-defhandler-must-be-top-level"
    rules = yaml.safe_load((ROOT / ".semgrep.yaml").read_text())["rules"]
    selected = [rule for rule in rules if rule["id"] == rule_id]
    assert len(selected) == 1
    (repository / ".semgrep.yaml").write_text(yaml.safe_dump({"rules": selected}))
    result: ProcessOutcome = _run_child(
        (sys.executable, "-m", "pre_commit", "run", "semgrep-hy", "--files", changed),
        repository, _environment(tmp_path),
    )
    assert result.exit_code == int(nested), result.stdout + result.stderr
    if nested:
        assert rule_id in result.stdout + result.stderr


# The plain commit hook (scripts/git-hooks/pre-commit) -- installed by make hooks-install and the agora worker Pod's preparation (agora's
# deploy/agent-worker/worker-prepare.sh). Even on a machine without the pre-commit framework installed, a commit passes through the semgrep rules
# (the 5 red daily runs of 10-11 were application vocabulary that a Pod's commit introduced without passing the framework's semgrep entry).
PLAIN_HOOK: Path = ROOT / "scripts" / "git-hooks" / "pre-commit"
# Alias of the pre-commit entries the plain hook fires -- every semgrep entry in .pre-commit-config.yaml carries this alias.
PLAIN_HOOK_ALIAS: str = "semgrep-rules"
VOCABULARY_RULE: str = "doeff-packages-have-no-application-vocabulary"
# uv.lock of the plain hook's sample repo -- tool versions are decided by the tree's uv.lock (#2906). The fake uv routes without looking at versions.
FAKE_LOCKED: dict[str, str] = {"semgrep": FAKE_VERSIONS["semgrep"], "pre-commit": "0.0.0-fake"}


def _plain_hook_repository(
    directory: Path, changed: str, source: str, rule_packages: tuple[str, ...] = (), *, fake_linter: bool = False,
) -> Path:
    """Copy what the plain hook reads (tracked files under scripts/, the hook config, the tree's Python declaration) and a .semgrep.yaml with only the application-vocabulary rule
    into a committed repo, and stage only `changed` (the ledger cross-check fires only for commits touching docs/adr and .semgrep.yaml).
    rule_packages = packages added only to the sample repo rule's paths.include (entries stay as copied -- the shape of a change that adds a package to the rule).
    fake_linter = commit a fake linter crate and write its key into the (empty) baseline (the fake linter placed by _tool is found as the dev build)."""
    repository: Path = directory / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    tracked: list[str] = subprocess.run(
        ["/usr/bin/git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    for name in (*tracked, ".pre-commit-config.yaml", ".python-version"):
        (repository / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, repository / name)
    rules = yaml.safe_load((ROOT / ".semgrep.yaml").read_text())["rules"]
    vocabulary = [rule for rule in rules if rule["id"] == VOCABULARY_RULE]
    vocabulary[0]["paths"]["include"].extend(f"/packages/{package}/**" for package in rule_packages)
    (repository / ".semgrep.yaml").write_text(yaml.safe_dump({"rules": vocabulary}), encoding="utf-8")
    (repository / "uv.lock").write_text(
        "version = 1\n"
        + "".join(
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n{FAKE_SOURCE}' for name, version in FAKE_LOCKED.items()
        ),
        encoding="utf-8",
    )
    # The Python semgrep entry compares with a baseline (#2848) -- use an empty baseline for the fake version (do not carry mainline baseline findings into the sample).
    (repository / "scripts" / "hook_finding_baseline" / "semgrep.json").write_text(
        json.dumps({"version": FAKE_VERSIONS["semgrep"], "counts": {}}), encoding="utf-8",
    )
    if fake_linter:
        (repository / "scripts" / "hook_finding_baseline" / "doeff-linter.json").write_text(
            json.dumps({"version": _commit_fake_crate(repository, directory), "counts": {}}), encoding="utf-8",
        )
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
    subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base"],
                   cwd=repository, check=True)
    changed_path: Path = repository / changed
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text(source, encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", changed], cwd=repository, check=True)
    return repository


def _plain_hook_with_a_linter(directory: Path, changed: str, report: str) -> _HookRun:
    """Run the plain hook in a sample repo with a fake doeff-linter (answering a report) and an empty baseline naming its key, and also return the calls the fake tools received
    (checks whether the plain hook compares linter findings against the baseline at commit time)."""
    _tool(directory, "doeff-linter", 0, report=report)
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "the real-sample check needs the dev-dependency semgrep"
    (directory / "semgrep").symlink_to(executable)
    repository: Path = _plain_hook_repository(directory, changed, "value = 1\n", fake_linter=True)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv is required (to run the hook script)"
    result: ProcessOutcome = _run_child(
        ("/bin/sh", str(PLAIN_HOOK)), repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"), ("SKIP", "VIRTUAL_ENV"),
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
    )
    return _HookRun(result=result, calls=calls)


def _plain_hook(directory: Path, changed: str, source: str, rule_packages: tuple[str, ...] = ()) -> ProcessOutcome:
    """Run the plain hook at the repo root, as git fires it, in a sample repo with the real semgrep placed next to the fake uv."""
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "the real-sample check needs the dev-dependency semgrep"
    (directory / "semgrep").symlink_to(executable)
    repository: Path = _plain_hook_repository(directory, changed, source, rule_packages)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv is required (to run the hook script)"
    return _run_child(
        ("/bin/sh", str(PLAIN_HOOK)), repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"), ("SKIP", "VIRTUAL_ENV"),
    )


# The shape of the 10-11 daily red -- a generic package's Hy comment (5431b983e, f3f569e9d, 7438f793f), a Python docstring (abae18074,
# 0e39b2cbd), and a document the framework passes from outside Python and Hy -- 3 cases. A document whose name is outside ASCII is stopped too (git by default prints such a name
# quoted -- a hook that reads and selects staged names itself would silently pass it there).
@pytest.mark.parametrize(("changed", "source"), [
    pytest.param("packages/doeff-cluster/src/doeff_cluster/example.hy",
                 ";; restart lease (card ki-0 of agora-controllers)\n(setv value 1)\n", id="hy-comment"),
    pytest.param("packages/doeff-records/tests/test_example.py",
                 '"""Daily red of the consumer repo (agora-controllers)."""\n\nvalue = 1\n', id="python-docstring"),
    pytest.param("packages/doeff-records/README.md", "declaration field (kanban row of agora)\n", id="document"),
    pytest.param("packages/doeff-records/résumé.md", "declaration field (kanban row of agora)\n", id="document-non-ascii-name"),
])
def test_plain_hook_stops_staged_application_vocabulary(tmp_path: Path, changed: str, source: str) -> None:
    result: ProcessOutcome = _plain_hook(tmp_path, changed, source)
    output: str = result.stdout + result.stderr
    assert result.exit_code != 0, f"the plain hook let through a stage that adds application vocabulary:\n{output}"
    assert VOCABULARY_RULE in output, output


@pytest.mark.parametrize(("changed", "source"), [
    pytest.param("packages/doeff-cluster/src/doeff_cluster/example.hy",
                 ";; restart lease (card ki-0)\n(setv value 1)\n", id="hy-comment"),
    pytest.param("packages/doeff-records/README.md", "declaration field (#2830)\n", id="document"),
])
def test_plain_hook_passes_staged_file_without_findings(tmp_path: Path, changed: str, source: str) -> None:
    result: ProcessOutcome = _plain_hook(tmp_path, changed, source)
    assert result.exit_code == 0, result.stdout + result.stderr


# Which packages the application-vocabulary rule applies to is decided in one place, the rule's paths.include -- the entry holds no package names. When it did,
# a change that added a package to the rule (on 10-10 a package was added to the rule and to the entry in separate commits) and forgot to fix the entry made the plain hook
# silently pass that package's documents (blind A of the design review).
PACKAGE_ADDED_TO_THE_RULE: str = "doeff-example-generic"


@pytest.mark.parametrize(("rule_packages", "stops"), [
    pytest.param((PACKAGE_ADDED_TO_THE_RULE,), True, id="package-added-to-the-rule"),
    pytest.param((), False, id="package-outside-the-rule"),
])
def test_plain_hook_follows_the_rule_paths_for_documents(
    tmp_path: Path, rule_packages: tuple[str, ...], stops: bool,
) -> None:
    result: ProcessOutcome = _plain_hook(
        tmp_path, f"packages/{PACKAGE_ADDED_TO_THE_RULE}/README.md", "declaration field (kanban row of agora)\n", rule_packages,
    )
    output: str = result.stdout + result.stderr
    assert (result.exit_code != 0) == stops, output
    assert (VOCABULARY_RULE in output) == stops, output


def test_plain_hook_leaves_file_selection_to_the_config(tmp_path: Path) -> None:
    # The plain hook does not choose staged files itself and starts pre-commit even for a stage that matches no entry -- file selection is in one place, the entry's
    # files / types (blind B of the design review: adding a "do not start when no file matches" shortcut to the hook
    # silently passed documents with non-ASCII names and file kinds added to an entry).
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    names: list[str] = [hook["name"] for hook in hooks if hook.get("alias") == PLAIN_HOOK_ALIAS]
    result: ProcessOutcome = _plain_hook(tmp_path, "notes/plain.txt", "declaration field\n")
    output: str = result.stdout + result.stderr
    assert names
    assert result.exit_code == 0, output
    assert [name for name in names if name not in output] == [], output


def test_plain_hook_runs_every_semgrep_hook_of_the_config() -> None:
    # The plain hook fires semgrep entries by alias, not by listing names -- if a change that adds a semgrep entry forgets the alias, that entry is silently dropped
    # on machines with only the plain hook (agora worker Pods).
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    semgrep_hooks: list[str] = sorted(hook["id"] for hook in hooks if "semgrep" in hook["entry"])
    aliased: list[str] = sorted(hook["id"] for hook in hooks if hook.get("alias") == PLAIN_HOOK_ALIAS)
    assert semgrep_hooks
    assert aliased == semgrep_hooks
    assert f"run {PLAIN_HOOK_ALIAS}" in PLAIN_HOOK.read_text(encoding="utf-8")


# The plain hook also fires the doeff-linter baseline-comparison entry -- the 3 commits of 10-18 came in from a Pod (plain hook only) and
# added 9 DOEFF004 findings to 2 sample files, but the plain hook did not fire the linter entry, and it was first found in the daily run (10-19 04:05 JST).
# The landing gate sees only conflict markers (law land-gate-runs-no-tests), so the first point that can stop findings above the baseline is commit time.
PLAIN_HOOK_LINTER_ID: str = "doeff-linter"


def test_plain_hook_runs_the_doeff_linter_baseline_hook_of_the_config() -> None:
    # Exactly one entry has the baseline comparison (hook_finding_baseline.py check doeff-linter), and the plain hook fires it by id. If a change that renames the entry's id
    # forgets to fix the plain hook's call, the linter entry is silently dropped on machines with only the plain hook (agora worker Pods).
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    linter_hooks: list[str] = [hook["id"] for hook in hooks if "hook_finding_baseline.py check doeff-linter" in hook["entry"]]
    assert linter_hooks == [PLAIN_HOOK_LINTER_ID]
    assert f"run {PLAIN_HOOK_LINTER_ID}" in PLAIN_HOOK.read_text(encoding="utf-8")


def test_plain_hook_stops_a_staged_python_file_with_a_linter_finding_not_in_the_baseline(tmp_path: Path) -> None:
    # One error absent from the (empty) baseline is in the staged file -> the plain hook stops (failure case -- it passes if the plain hook does not fire the linter entry).
    report: str = json.dumps([{"rule": "DOEFF016", "severity": "error", "violations": [{"file": "doeff/example.py"}]}])
    result: ProcessOutcome = _plain_hook_with_a_linter(tmp_path, "doeff/example.py", report).result
    output: str = result.stdout + result.stderr
    assert result.exit_code != 0, f"the plain hook let through linter findings above the baseline:\n{output}"
    assert "DOEFF016 doeff/example.py: 基点 0 → 今 1" in output, output


def test_plain_hook_runs_the_linter_once_on_only_the_staged_python_file(tmp_path: Path) -> None:
    hook: _HookRun = _plain_hook_with_a_linter(tmp_path, "doeff/example.py", "[]")
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    runs: list[list[str]] = [call for call in hook.calls if call[0] == "doeff-linter" and "--version" not in call]
    assert len(runs) == 1, hook.calls
    assert runs[0][-1] == "doeff/example.py"
    assert "doeff/" not in runs[0]


def test_every_uv_hook_declares_the_hook_python() -> None:
    # Every entry that runs through uv declares the hook's python -- without the declaration uv picks the tree's .python-version (3.14t) and semgrep's dependency
    # ruamel-yaml-clib cannot be built and it fails (Pod 2026-10-10).
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    uv_entries: list[str] = [hook["entry"] for hook in hooks if hook["entry"].startswith("uv ")]
    assert uv_entries
    assert [entry for entry in uv_entries if f"--python {HOOK_PYTHON}" not in entry] == []
