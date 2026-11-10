"""Failure case: a natively built Rust part (the native extension module) installed in editable mode must still be importable after `make sync` following a copy that prunes files absent from the working tree's git listing
(step 3 of dotfiles' remote_check -- the daily full-verification path).

2026-10-07 04:00 daily run: remote_check copies the pod's tree to node-h at each step, and each copy deletes files absent from the listing (only .venv is kept).
The build backend placed the .so inside the source tree for an editable install, so the next step's copy removed it; uv's install record (inside .venv) and
cache-keys were unchanged, so the next `make sync` built nothing, the conftest import of doeff_vm.doeff_vm failed, and not a single pytest test ran.

Uses the real uv and the real build backend (tools/doeff_cargo_backend.py), and installs a small package whose only fake part is the build step (it returns a wheel
carrying a stand-in file for the built artifact). Checks whether the stand-in is found by the import search (find_spec); contents are not loaded.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
EXT = sysconfig.get_config_var("EXT_SUFFIX")

# A wrapper that loads the real build backend and swaps only the build step for a fake, passing the hooks through (placed in the package dir; backend-path = ["."]).
WRAPPER = f'''
import base64, hashlib, importlib.util, sys, zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("doeff_cargo_backend", {str(BACKEND)!r})
real = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = real
spec.loader.exec_module(real)
DIST = "probe_native-0.1.0"


def _compile(target, wheel_directory, config_settings):
    files = {{
        "probe_native/__init__.py": b"",
        "probe_native/_native{EXT}": b"not a real extension",
        f"{{DIST}}.dist-info/METADATA": b"Metadata-Version: 2.1\\nName: probe-native\\nVersion: 0.1.0\\n",
        f"{{DIST}}.dist-info/WHEEL": b"Wheel-Version: 1.0\\nGenerator: probe\\nRoot-Is-Purelib: false\\nTag: py3-none-any\\n",
    }}
    rows = [f"{{n}},sha256={{base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b'=').decode()}},{{len(d)}}" for n, d in files.items()]
    name = f"{{DIST}}-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / name, "w") as wheel:
        for member, data in files.items():
            wheel.writestr(member, data)
        wheel.writestr(f"{{DIST}}.dist-info/RECORD", "\\n".join(rows + [f"{{DIST}}.dist-info/RECORD,,"]) + "\\n")
    return name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return real.wheel_from_store(wheel_directory, config_settings, _compile)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return real.editable_from_store(wheel_directory, config_settings, _compile)
'''


def _tool_path() -> str:
    """Child PATH: the dirs of rustc and git that cargo calls when the backend builds, plus the base dirs (the slot key does not read rustc -- addendum to ADR-DOE-BUILD-001 R4)."""
    found = [shutil.which(name) for name in ("rustc", "git")]
    assert all(found), f"rustc and git must be on the search path: {found}"
    return ":".join(dict.fromkeys([*(str(Path(path).parent) for path in found if path), "/usr/bin", "/bin"]))


def _uv_cache_dir() -> str:
    """The caller's uv cache dir (`uv cache dir` -- uv itself resolves the order UV_CACHE_DIR, XDG_CACHE_HOME, HOME). The child runs with UV_OFFLINE and
    pulls build dependencies (maturin) from this cache, so pass the same dir as the caller instead of the HOME default -- the daily task has an empty per-run HOME dir, and
    the cache lives in UV_CACHE_DIR (the worker's shared cache) (first seen 2026-10-10 20:05 on the node-a Pod, where 2 tests went red)."""
    uv = shutil.which("uv")
    assert uv is not None, "uv is not on the search path"
    return subprocess.run([uv, "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()


def _make_sync() -> list[str]:
    """The command of the Makefile sync target (same as the per-step sync of the daily run)."""
    found = re.search(r"^sync:\n\t(.+)$", (REPO / "Makefile").read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, "Makefile has no sync target"
    return shlex.split(found.group(1))


class Tree:
    """A git working-tree workspace (root = root) holding one package probe-native that goes through the real build backend."""

    def __init__(self, root: Path, store: Path) -> None:
        self.root = root
        self.env = {"HOME": str(Path.home()), "UV_OFFLINE": "1", "UV_CACHE_DIR": _uv_cache_dir(), "UV_PYTHON": sys.executable,
                    "UV_NO_CONFIG": "1", "DOEFF_WHEEL_CACHE": str(store), "DOEFF_WHEEL_REMOTE": "", "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": _tool_path()}
        package = root / "packages" / "probe-native"
        (package / "probe_native").mkdir(parents=True)
        (package / "src").mkdir()
        (package / "src" / "lib.rs").write_text("// 0\n")
        (package / "probe_native" / "__init__.py").write_text("")
        (package / "probe_backend.py").write_text(WRAPPER)
        (package / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["maturin>=1.5,<2"]\nbuild-backend = "probe_backend"\nbackend-path = ["."]\n'
            '[project]\nname = "probe-native"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
            '[tool.maturin]\npython-source = "."\nmodule-name = "probe_native._native"\n'
            '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "src/**/*.rs" }]\n'
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "probe-root"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = ["probe-native"]\n'
            '[dependency-groups]\ndev = []\n'
            '[tool.uv.sources]\nprobe-native = { workspace = true }\n'
            '[tool.uv.workspace]\nmembers = ["packages/probe-native"]\n'
        )
        (root / ".gitignore").write_text(".venv/\n*.so\n__pycache__/\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        self.run(["uv", "lock"])

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True).stdout

    def run(self, argv: list[str]) -> str:
        uv = shutil.which(argv[0])
        assert uv is not None, f"{argv[0]} is not on the search path"
        done = subprocess.run([uv, *argv[1:]], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=25, check=False)
        assert done.returncode == 0, f"{argv} exited with rc {done.returncode}: {done.stderr}"
        return done.stderr

    def prune_like_remote_check(self) -> list[str]:
        """Delete files not in git's listing (tracked + untracked non-ignored); never delete under .venv -- the same rule as step 3 of remote_check."""
        listed = set(self.git("ls-files", "--cached", "--others", "--exclude-standard").splitlines())
        present = [str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.root).parts]
        pruned = [name for name in present if name not in listed and ".venv" not in Path(name).parts]
        for name in pruned:
            (self.root / name).unlink()
        return pruned

    def native_found(self) -> bool:
        python = self.root / ".venv" / "bin" / "python"
        probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('probe_native._native') else 1)"
        return subprocess.run([str(python), "-c", probe], cwd="/", capture_output=True, check=False).returncode == 0


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    root = tmp_path / "doeff-gate-full"
    root.mkdir()
    return Tree(root, tmp_path / "wheels")


def test_the_native_module_survives_a_prune_of_unlisted_files_and_the_next_make_sync(tree: Tree) -> None:
    tree.run(_make_sync())
    assert tree.native_found(), "the built artifact is not found by the import search after the first sync"
    pruned = tree.prune_like_remote_check()
    tree.run(_make_sync())
    assert tree.native_found(), f"the built artifact is not found after make sync following the prune of files absent from the listing (pruned files: {pruned})"


def test_rewriting_the_pth_source_line_moves_the_package_and_keeps_the_native_module(tree: Tree) -> None:
    # Failure case (the retargeting to a pinned version in agora-controllers, scripts/pinned_doeff.hy, rewrites only the line in the venv .pth):
    # if line 1 of the .pth is rewritten to another checkout, the package's Python is read from that checkout while the built artifact keeps being read from the venv. Before the fix, the
    # finder held the working-tree path inside itself and kept reading the original checkout even after the rewrite.
    tree.run(_make_sync())
    moved = tree.root.parent / "other-checkout"
    shutil.copytree(tree.root / "packages" / "probe-native", moved)
    (moved / "probe_native" / "__init__.py").write_text("MOVED = True\n")
    (pth,) = (tree.root / ".venv").glob("lib/python*/site-packages/probe_native.pth")
    lines = pth.read_text().splitlines()
    pth.write_text("\n".join([str(moved), *lines[1:]]) + "\n")
    python = tree.root / ".venv" / "bin" / "python"
    probe = "import probe_native, importlib.util; print(probe_native.MOVED, importlib.util.find_spec('probe_native._native') is not None)"
    done = subprocess.run([str(python), "-c", probe], cwd="/", capture_output=True, text=True, check=False)
    assert done.stdout.split() == ["True", "True"], done.stdout + done.stderr
