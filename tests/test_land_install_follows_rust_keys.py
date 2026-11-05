"""Failure cases for: the rebuild of the land (merge) working tree ([follow] install in .agents/land-queue.toml) and make sync call the build entry point for the Rust component (doeff-vm)
only when a file in doeff-vm's tool.uv.cache-keys changes (ADR-DOE-BUILD-001).

uv calls the build entry point when a cache-keys file changes, and the entry point looks up the wheel store by a key from the same files' contents. So the flag
`--reinstall-package doeff-vm` is unnecessary, and if present it calls the entry point every time even when nothing changed. Here cargo and maturin are not fired; a real
uv is made to install a package whose build entry point has the same cache-keys as doeff-vm and only records how many times it is called, and the calls are counted.
"""

from __future__ import annotations

import glob
import importlib.util
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
VM = REPO / "packages" / "doeff-vm"
VM_PYPROJECT = VM / "pyproject.toml"
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
# Of the entry point's BUILD_ENV_NAMES, a name that does not affect building doeff-vm (a flag that does not bundle doeff-indexer's CLI binary).
NOT_FOR_VM = frozenset({"DOEFF_INDEXER_SKIP_CLI_BUILD"})

# A build entry point (no dependencies) that, each time it is called, appends one line to calls.log at the workspace root and returns a wheel with no contents.
STUB_BACKEND = '''
import base64, hashlib, zipfile
from pathlib import Path

CALLS = Path(__file__).resolve().parents[2] / "calls.log"
DIST = "doeff_vm-0.2.0"


def _wheel(directory):
    with CALLS.open("a") as calls:
        calls.write("build\\n")
    files = {
        f"{DIST}.dist-info/METADATA": b"Metadata-Version: 2.1\\nName: doeff-vm\\nVersion: 0.2.0\\n",
        f"{DIST}.dist-info/WHEEL": b"Wheel-Version: 1.0\\nGenerator: stub\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
    }
    rows = [
        f"{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}"
        for name, data in files.items()
    ] + [f"{DIST}.dist-info/RECORD,,"]
    name = f"{DIST}-py3-none-any.whl"
    with zipfile.ZipFile(Path(directory) / name, "w") as wheel:
        for member, data in files.items():
            wheel.writestr(member, data)
        wheel.writestr(f"{DIST}.dist-info/RECORD", "\\n".join(rows) + "\\n")
    return name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _wheel(wheel_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _wheel(wheel_directory)
'''


def _cache_keys() -> list[dict[str, str]]:
    """doeff-vm's real tool.uv.cache-keys (this list is copied as is into the fake package)."""
    return tomllib.loads(VM_PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]["cache-keys"]


def _file_keys() -> list[str]:
    """Globs of the cache-keys files."""
    return [key["file"] for key in _cache_keys() if "file" in key]


def _env_keys() -> list[str]:
    """Names of the cache-keys environment variables."""
    return [key["env"] for key in _cache_keys() if "env" in key]


def _backend() -> ModuleType:
    """Load the build entry point as a module (tools is not a package, so by path)."""
    spec = importlib.util.spec_from_file_location("doeff_cargo_backend_keys_under_test", BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _path_crates(crate: Path) -> list[Path]:
    """Dirs of the crates reached by transitively following path = in the crate's Cargo.toml (including itself; in name order)."""
    def deps(at: Path) -> list[Path]:
        manifest = tomllib.loads((at / "Cargo.toml").read_text(encoding="utf-8"))
        tables = [manifest.get(name, {}) for name in ("dependencies", "build-dependencies")]
        return [(at / spec["path"]).resolve() for table in tables for spec in table.values() if isinstance(spec, dict) and "path" in spec]

    def walk(pending: list[Path], seen: frozenset[Path]) -> frozenset[Path]:
        if not pending:
            return seen
        head, rest = pending[0], pending[1:]
        return walk(rest, seen) if head in seen else walk(rest + deps(head), seen | {head})

    return sorted(walk([crate.resolve()], frozenset()))


def _crate_inputs(crate: Path) -> list[Path]:
    """The files of one crate that affect building (only those that exist)."""
    named = [crate / name for name in ("Cargo.toml", "Cargo.lock", "pyproject.toml", "build.rs")]
    return [path for path in named if path.is_file()] + sorted((crate / "src").rglob("*.rs"))


def _land_install() -> list[str]:
    """Of the commands for rebuilding the land working tree, the uv command after the venv-run wrapper."""
    install = tomllib.loads((REPO / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["follow"]["install"]
    return shlex.split(install.split(" -- ", 1)[1])


def _make_sync() -> list[str]:
    """The command of the sync target in the Makefile."""
    found = re.search(r"^sync:\n\t(.+)$", (REPO / "Makefile").read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, "the Makefile has no sync target"
    return shlex.split(found.group(1))


# For each cache-keys glob, where a file matching that glob is placed (path relative to the fake package's dir).
KEY_FILES = {
    "pyproject.toml": "pyproject.toml",
    "Cargo.toml": "Cargo.toml",
    "Cargo.lock": "Cargo.lock",
    "build.rs": "build.rs",
    "src/**/*.rs": "src/lib.rs",
    "doeff_vm/**/*.py": "doeff_vm/__init__.py",
    "doeff_vm/**/*.pyi": "doeff_vm/__init__.pyi",
    "doeff_vm/py.typed": "doeff_vm/py.typed",
    "LICENSE": "LICENSE",
    "../doeff-vm-core/pyproject.toml": "../doeff-vm-core/pyproject.toml",
    "../doeff-vm-core/Cargo.toml": "../doeff-vm-core/Cargo.toml",
    "../doeff-vm-core/Cargo.lock": "../doeff-vm-core/Cargo.lock",
    "../doeff-vm-core/src/**/*.rs": "../doeff-vm-core/src/lib.rs",
    "doeff_cargo_backend.py": "doeff_cargo_backend.py",
}


class Workspace:
    """A workspace (root = root) into which one fake package with the same cache-keys as doeff-vm is installed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.vm = root / "packages" / "doeff-vm"
        # The child's environment is built without inheriting the caller's (do not bring in the caller's venv, uv settings, or wheel store).
        self.env = {
            "HOME": str(root.parent),
            "UV_OFFLINE": "1",
            "UV_PYTHON": sys.executable,
            "UV_CACHE_DIR": str(root.parent / "uv-cache"),
            "UV_NO_CONFIG": "1",
        }

    def build(self) -> None:
        keys = _file_keys()
        assert sorted(keys) == sorted(KEY_FILES), f"the cache-keys files of doeff-vm changed -- align KEY_FILES: {keys}"
        entries = "".join(
            f'    {{ {kind} = "{value}" }},\n' for key in _cache_keys() for kind, value in key.items()
        )
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "probe-root"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = ["doeff-vm"]\n'
            '[dependency-groups]\ndev = []\n'
            '[tool.uv.sources]\ndoeff-vm = { workspace = true }\n'
            '[tool.uv.workspace]\nmembers = ["packages/doeff-vm"]\n'
        )
        pyproject = (
            '[build-system]\nrequires = []\nbuild-backend = "stub_backend"\nbackend-path = ["."]\n'
            '[project]\nname = "doeff-vm"\nversion = "0.2.0"\nrequires-python = ">=3.10"\n'
            "[tool.uv]\ncache-keys = [\n" + entries + "]\n"
        )
        for rel in KEY_FILES.values():
            path = self.vm / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 0\n")
        (self.vm / "pyproject.toml").write_text(pyproject)
        (self.vm / "stub_backend.py").write_text(STUB_BACKEND)
        (self.vm / "README.md").write_text("outside the key\n")
        self.run(["uv", "lock"])
        self.run(_land_install())

    def run(self, argv: list[str], extra: dict[str, str] | None = None) -> None:
        uv = shutil.which(argv[0])
        assert uv is not None, f"{argv[0]} is not on the search path"
        done = subprocess.run([uv, *argv[1:]], cwd=self.root, env={**self.env, **(extra or {})}, capture_output=True, text=True,
                              timeout=25, check=False)
        assert done.returncode == 0, f"{argv} rc {done.returncode}: {done.stderr}"

    def calls(self) -> int:
        log = self.root / "calls.log"
        return len(log.read_text().splitlines()) if log.exists() else 0


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    made = Workspace(tmp_path_factory.mktemp("land-install") / "ws")
    made.root.mkdir()
    made.build()
    return made


@pytest.mark.parametrize("command", [_land_install, _make_sync], ids=["land-follow-install", "make-sync"])
def test_an_unchanged_sync_does_not_call_the_rust_build_entry(workspace: Workspace, command) -> None:
    before = workspace.calls()
    workspace.run(command())
    assert workspace.calls() == before, f"{command()} with no change called the build entry point {workspace.calls() - before} times"


@pytest.mark.parametrize("key", sorted(KEY_FILES))
def test_a_change_to_each_cache_key_file_calls_the_rust_build_entry_once(workspace: Workspace, key: str) -> None:
    path = workspace.vm / KEY_FILES[key]
    path.write_text(path.read_text() + f"# changed {key}\n")
    before = workspace.calls()
    workspace.run(_land_install())
    assert workspace.calls() == before + 1, f"the rebuild after changing {key} called the build entry point {workspace.calls() - before} times (should be once)"


def test_a_change_outside_the_cache_keys_does_not_call_the_rust_build_entry(workspace: Workspace) -> None:
    readme = workspace.vm / "README.md"
    readme.write_text(readme.read_text() + "changed\n")
    before = workspace.calls()
    workspace.run(_land_install())
    assert workspace.calls() == before


@pytest.mark.parametrize("name", _env_keys())
def test_a_change_to_each_cache_key_env_calls_the_rust_build_entry_once(workspace: Workspace, name: str) -> None:
    before = workspace.calls()
    workspace.run(_land_install(), {name: "changed"})
    assert workspace.calls() == before + 1, f"the rebuild after changing {name} called the build entry point {workspace.calls() - before} times (should be once)"
    workspace.run(_land_install())  # restore (the restore is also a change -- to align the start of counting for the next case)


def test_the_cache_keys_cover_every_path_crate_input() -> None:
    matched = {Path(hit).resolve() for pattern in _file_keys() for hit in glob.glob(str(VM / pattern), recursive=True)}
    missing = [str(path.relative_to(REPO)) for crate in _path_crates(VM) for path in _crate_inputs(crate) if path.resolve() not in matched]
    assert not missing, f"files that affect building the path-dependent crates but are not in doeff-vm's cache-keys: {missing}"


def test_the_cache_keys_cover_the_license_files() -> None:
    # license-files go into the stored wheel's dist-info -- if not in the key, the wheel with the earlier LICENSE is pulled even after only LICENSE changes.
    matched = {Path(hit).resolve() for pattern in _file_keys() for hit in glob.glob(str(VM / pattern), recursive=True)}
    license_files = tomllib.loads(VM_PYPROJECT.read_text(encoding="utf-8"))["project"]["license-files"]
    found = [Path(hit) for pattern in license_files for hit in glob.glob(str(VM / pattern), recursive=True)]
    assert found, f"no file matches doeff-vm's license-files: {license_files}"
    missing = [str(path.relative_to(REPO)) for path in found if path.resolve() not in matched]
    assert not missing, f"license-files not in doeff-vm's cache-keys: {missing}"


def test_the_cache_keys_cover_the_build_entry_file_and_its_build_env_names() -> None:
    backend = _backend()
    assert (VM / "doeff_cargo_backend.py").resolve() == BACKEND.resolve()
    assert "doeff_cargo_backend.py" in _file_keys(), "the build entry point's own file is not in cache-keys"
    missing = sorted(backend.BUILD_ENV_NAMES - NOT_FOR_VM - set(_env_keys()))
    assert not missing, f"environment variables that the entry point puts in the store key but that are not in cache-keys: {missing}"
    unprefixed = [name for name in _env_keys() if name not in backend.BUILD_ENV_NAMES and not name.startswith(backend.BUILD_ENV_PREFIXES)]
    assert not unprefixed, f"environment variables that the entry point does not put in the store key are in cache-keys (only uv rebuilds, and the entry point pulls the earlier wheel): {unprefixed}"
