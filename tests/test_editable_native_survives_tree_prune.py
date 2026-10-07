"""editable で入れた Rust の部品の組んだ物(native の拡張 module)が、作業木の git の名簿に無い file を消す写し(dotfiles の
remote_check の段 3 — 日次の全体検証の道)の後の `make sync` でも import できる事の失敗ケース(agora-redesign #3860)。

2026-10-07 04:00 の日次: remote_check が手順ごとに pod の木を zeus へ写し、写すたびに名簿に無い file を消す(消さないのは .venv
だけ)。build の口は editable の入れで .so を source の木の中に置いていたので、次の手順の写しで消え、uv の入れた記録(.venv の中)と
cache-keys は変わらないので、次の `make sync` は何も組まず、conftest の import doeff_vm.doeff_vm が落ちて pytest が 1 本も走らなかった。

本物の uv と本物の build の口(tools/doeff_cargo_backend.py)を使い、組み方だけを偽物(組んだ物の代わりの file を持つ wheel を返す)に
替えた小さな package を入れる。import の探し(find_spec)で組んだ物の代わりが見つかるかを見る(中身は読み込まない)。
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

# 本物の build の口を読み、組み方だけを偽物に替えて hook を渡す包み(package の dir に置く・backend-path = ["."])。
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
    """子の PATH: build の口が組む時に cargo が呼ぶ rustc と git の dir と、基本の dir(保存先の鍵は rustc を読まない — ADR-DOE-BUILD-001 R4 の追補)。"""
    found = [shutil.which(name) for name in ("rustc", "git")]
    assert all(found), f"rustc と git が探し道に要る: {found}"
    return ":".join(dict.fromkeys([*(str(Path(path).parent) for path in found if path), "/usr/bin", "/bin"]))


def _make_sync() -> list[str]:
    """Makefile の sync の target の命令(日次の手順ごとの同期と同じ)。"""
    found = re.search(r"^sync:\n\t(.+)$", (REPO / "Makefile").read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, "Makefile に sync の target が無い"
    return shlex.split(found.group(1))


class Tree:
    """git の作業木の workspace(根 = root)に、本物の build の口を通す package probe-native を 1 つ持つ。"""

    def __init__(self, root: Path, store: Path) -> None:
        self.root = root
        self.env = {"HOME": str(Path.home()), "UV_OFFLINE": "1", "UV_PYTHON": sys.executable, "UV_NO_CONFIG": "1",
                    "DOEFF_WHEEL_CACHE": str(store), "PYTHONDONTWRITEBYTECODE": "1", "PATH": _tool_path()}
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
        assert uv is not None, f"{argv[0]} が探し道に無い"
        done = subprocess.run([uv, *argv[1:]], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=25, check=False)
        assert done.returncode == 0, f"{argv} が rc {done.returncode}: {done.stderr}"
        return done.stderr

    def prune_like_remote_check(self) -> list[str]:
        """git の名簿(管理下 + 未追跡の非無視)に無い file を消す(.venv の下は消さない)— remote_check の段 3 と同じ規則。"""
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
    assert tree.native_found(), "最初の同期の後に組んだ物が import の探しで見つからない"
    pruned = tree.prune_like_remote_check()
    tree.run(_make_sync())
    assert tree.native_found(), f"名簿に無い file を消した後の make sync で組んだ物が見つからない(消した file: {pruned})"


def test_rewriting_the_pth_source_line_moves_the_package_and_keeps_the_native_module(tree: Tree) -> None:
    # 失敗ケース(agora-redesign #3860 — agora-controllers の固定の版への向け直し scripts/pinned_doeff.hy は venv の .pth の行だけを書き換える):
    # .pth の 1 行目を別の checkout へ書き換えると、package の Python はその checkout から読まれ、組んだ物は venv から読まれ続ける。直す前の
    # finder は作業木の path を自分の中に持ち、書き換えた後も元の checkout を読んだ。
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
