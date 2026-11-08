"""The .pyi of Hy modules are made by each package's build, not committed (tools/doeff_hy_stub_hook.py).

A committed .pyi reached main stale more than 10 times: a commit changed a .hy without regenerating it (no commit hook ran, another .hy's macro
changed, the tool changed), and users' strict type checks read the new names as Unknown. With the .pyi made at every install, a stale one has
nowhere to live — as long as no tool .pyi is tracked again and every package with tool .pyi runs the hook on the files it depends on.

- No tracked .pyi carries the tool's mark (a tracked one would be the stale copy again; the build leaves files it did not make alone only if hand-written).
- Every package dir holding a tool .pyi is under the stub-dirs of a package's build hook.
- Each such package links the hook, lists its .hy and the doeff-hy tool and macros in uv's cache-keys (uv runs the build again only when a key
  changes), requires the third-party modules the expansion imports, puts the git-ignored .pyi into the wheel (artifacts), and ignores them in git.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
#: Line 1 of a .pyi the tool makes (doeff_hy._static_stub.MARK_HEAD — kept as text so this check does not import Hy).
MARK_HEAD = "# doeff_hy._static_stub "
HOOK = ROOT / "tools" / "doeff_hy_stub_hook.py"
BOOT = ROOT / "packages" / "doeff-cluster" / "deploy" / "boot.sh"


@dataclass(frozen=True)
class StubPackage:
    """What one package's pyproject says about making its .pyi (the hook's settings, uv's cache-keys, the build requirements, the artifacts)."""

    directory: Path
    stub_root: str
    stub_dirs: tuple[str, ...]
    cache_keys: frozenset[str]
    requires: tuple[str, ...]
    artifacts: tuple[str, ...]


def _table(value: object, *path: str) -> dict[str, object]:
    """The TOML table at path ({} when absent — a package without the hook has no such table)."""
    for name in path:
        value = value.get(name, {}) if isinstance(value, dict) else {}
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _stub_packages() -> dict[Path, StubPackage]:
    """The packages whose build runs the stub hook (the ones that name stub-dirs under [tool.hatch.build.hooks.custom])."""
    found: dict[Path, StubPackage] = {}
    for pyproject in sorted(ROOT.glob("packages/*/pyproject.toml")):
        loaded = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        custom = _table(loaded, "tool", "hatch", "build", "hooks", "custom")
        if "stub-dirs" not in custom:
            continue
        keys = _table(loaded, "tool", "uv").get("cache-keys")
        found[pyproject.parent] = StubPackage(
            pyproject.parent,
            str(custom.get("stub-root", "")),
            _strings(custom["stub-dirs"]),
            frozenset(str(entry.get("file")) for entry in keys if isinstance(entry, dict)) if isinstance(keys, list) else frozenset(),
            _strings(_table(loaded, "build-system").get("requires")),
            _strings(_table(loaded, "tool", "hatch", "build").get("artifacts")),
        )
    return found


def test_no_tool_made_stub_is_committed() -> None:
    listed = subprocess.run(["git", "ls-files", "-z", "*.pyi"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    tracked = [name for name in listed if name and (ROOT / name).is_file()]
    assert [name for name in tracked if (ROOT / name).read_text(encoding="utf-8").startswith(MARK_HEAD)] == []


def test_every_tool_stub_is_made_by_a_package_build() -> None:
    covered = [package / d for package, made in _stub_packages().items() for d in made.stub_dirs]
    stubs = [p for p in ROOT.glob("packages/*/**/*.pyi") if ".venv" not in p.parts and p.read_text(encoding="utf-8").startswith(MARK_HEAD)]
    assert [str(p.relative_to(ROOT)) for p in stubs if not any(p.is_relative_to(d) for d in covered)] == []


@pytest.mark.parametrize("package", sorted(_stub_packages()), ids=lambda p: p.name)
def test_the_package_build_runs_the_hook_on_what_the_stubs_depend_on(package: Path) -> None:
    made = _stub_packages()[package]
    assert (package / "hatch_build.py").resolve() == HOOK.resolve()
    wanted = {"pyproject.toml", "hatch_build.py"}
    wanted |= {"src/**/*.hy", "src/**/*.py"} if package.name == "doeff-hy" else {"../doeff-hy/src/**/*.hy", "../doeff-hy/src/**/*.py"}
    assert wanted <= made.cache_keys
    hy_keys = [Path(k.removesuffix("**/*.hy")) for k in made.cache_keys if k.endswith("**/*.hy") and not k.startswith("..")]
    assert all(any(Path(d).is_relative_to(k) for k in hy_keys) for d in made.stub_dirs), made.cache_keys
    assert all((package / d).is_relative_to(package / made.stub_root) for d in made.stub_dirs)
    requires = " ".join(made.requires)
    assert "hy>=" in requires
    assert "frozendict" in requires
    assert "doeff-vm" in requires
    assert "*.pyi" in made.artifacts
    probe = [str((package / d / "probe.pyi").relative_to(ROOT)) for d in made.stub_dirs]
    ignored = subprocess.run(["git", "check-ignore", "--no-index", *probe], cwd=ROOT, capture_output=True, text=True, check=False)
    assert ignored.stdout.split() == probe


def test_the_worker_turns_the_hook_off_for_the_roots_it_prepares() -> None:
    # The roots a doeff-cluster worker prepares run programs and are never type-checked; with the hook on, each uv sync there expanded every .hy
    # (about 100 seconds and four expansion processes for doeff-cluster from a cold cache).
    from doeff_cluster._shared.core import native_wheel

    hook = (HOOK.read_text(encoding="utf-8").split('STUBS_ENV = "', 1)[1]).split('"', 1)[0]
    assert hook == native_wheel.STUBS_ENV
    assert (hook, "off") in {(v.name, v.value) for v in native_wheel.uv_environment("/state", "/cache")}


class _StartedError(Exception):
    """Raised by the recorded subprocess.run in place of starting the expansion process (carries the command)."""

    def __init__(self, command: list[str]) -> None:
        super().__init__(command)
        self.command = tuple(command)


def _started(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
    raise _StartedError(command)


def _hook_command(monkeypatch: pytest.MonkeyPatch, package: Path) -> tuple[str, ...] | None:
    """The command the hook's initialize starts for package (None when it starts none). subprocess.run is replaced and never runs; hatchling,
    which the hook subclasses, is only in the build environment, so a stand-in module takes its place."""
    import importlib.util
    import sys
    import types

    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, interface.__name__, interface)
    spec = importlib.util.spec_from_file_location("doeff_hy_stub_hook_probe", HOOK)
    assert spec is not None
    assert spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(hook, "subprocess", types.SimpleNamespace(run=_started))
    made = _stub_packages()[package]
    probe = types.SimpleNamespace(root=str(package), config={"stub-root": made.stub_root, "stub-dirs": list(made.stub_dirs)})
    try:
        hook.HyStubHook.initialize(probe, "editable", {})
    except _StartedError as started:
        return started.command
    return None


def test_the_hook_starts_no_expansion_when_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # With STUBS_ENV=off the hook returns before it starts doeff_hy._static_stub --build, so no expansion process is started; without it the
    # same call does start one (the probe sees the command — only the hook module's subprocess is replaced).
    from doeff_cluster._shared.core import native_wheel

    package = ROOT / "packages" / "doeff-cluster"
    monkeypatch.delenv(native_wheel.STUBS_ENV, raising=False)
    command = _hook_command(monkeypatch, package)
    assert command is not None
    assert command[2:4] == ("doeff_hy._static_stub", "--build")
    monkeypatch.setenv(native_wheel.STUBS_ENV, "off")
    assert _hook_command(monkeypatch, package) is None


def test_the_boot_script_turns_the_hook_off_for_the_root_it_prepares() -> None:
    # The boot script's own uv sync of the worker root is the other path that builds a root: with the hook on there, the prepare layer's
    # expansion processes went over its memory limit and the build died with BrokenProcessPool (k3s-0, 2026-11-07).
    from doeff_cluster._shared.core import native_wheel

    syncs = [line for line in BOOT.read_text(encoding="utf-8").splitlines() if " uv sync " in line and not line.lstrip().startswith("#")]
    roots = [line for line in syncs if "--package doeff-cluster" in line]
    assert len(roots) == 1, syncs
    assert f" {native_wheel.STUBS_ENV}=off " in roots[0].split(" uv sync ", 1)[0] + " "
