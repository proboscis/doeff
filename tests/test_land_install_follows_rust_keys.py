"""取り込みの作業の作り直し(.agents/land-queue.toml の [follow] install)と make sync が、Rust の部品(doeff-vm)の build の口を
呼ぶのは doeff-vm の tool.uv.cache-keys の file が変わった時だけである事の失敗ケース(agora-redesign #3860 の 2・ADR-DOE-BUILD-001)。

uv は cache-keys の file の変化で build の口を呼び、口は同じ file の中身の鍵で wheel の保存先を引く。だから旗
`--reinstall-package doeff-vm` は要らず、在ると変わっていない時も毎回口を呼ぶ。ここでは cargo と maturin を撃たず、本物の
uv に、doeff-vm と同じ cache-keys を持ち、呼ばれた度数を記録するだけの build の口の package を入れさせて数える。
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VM_PYPROJECT = REPO / "packages" / "doeff-vm" / "pyproject.toml"

# 呼ばれるたびに workspace の根の calls.log へ 1 行足し、中身の無い wheel を返す build の口(依存なし)。
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


def _cache_keys() -> list[str]:
    """本物の doeff-vm の tool.uv.cache-keys の file の glob(この並びを、そのまま偽の package に写す)。"""
    declared = tomllib.loads(VM_PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]["cache-keys"]
    return [key["file"] for key in declared]


def _land_install() -> list[str]:
    """取り込みの作業の作り直しの命令のうち、venv-run の包みの後ろの uv の命令。"""
    install = tomllib.loads((REPO / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["follow"]["install"]
    return shlex.split(install.split(" -- ", 1)[1])


def _make_sync() -> list[str]:
    """Makefile の sync の target の命令。"""
    found = re.search(r"^sync:\n\t(.+)$", (REPO / "Makefile").read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, "Makefile に sync の target が無い"
    return shlex.split(found.group(1))


# cache-keys の glob 1 つごとに、その glob に当たる file の置き場(偽の package の dir からの相対 path)。
KEY_FILES = {
    "pyproject.toml": "pyproject.toml",
    "Cargo.toml": "Cargo.toml",
    "Cargo.lock": "Cargo.lock",
    "src/**/*.rs": "src/lib.rs",
    "doeff_vm/**/*.py": "doeff_vm/__init__.py",
    "doeff_vm/**/*.pyi": "doeff_vm/__init__.pyi",
    "doeff_vm/py.typed": "doeff_vm/py.typed",
    "../doeff-vm-core/pyproject.toml": "../doeff-vm-core/pyproject.toml",
    "../doeff-vm-core/Cargo.toml": "../doeff-vm-core/Cargo.toml",
    "../doeff-vm-core/Cargo.lock": "../doeff-vm-core/Cargo.lock",
    "../doeff-vm-core/src/**/*.rs": "../doeff-vm-core/src/lib.rs",
}


class Workspace:
    """doeff-vm と同じ cache-keys を持つ偽の package を 1 つ入れる workspace(根 = root)。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.vm = root / "packages" / "doeff-vm"
        # 子の環境は呼び手の環境を継がずに組む(呼び手の venv・uv の設定・wheel の保存先を持ち込まない)。
        self.env = {
            "HOME": str(root.parent),
            "UV_OFFLINE": "1",
            "UV_PYTHON": sys.executable,
            "UV_CACHE_DIR": str(root.parent / "uv-cache"),
            "UV_NO_CONFIG": "1",
        }

    def build(self) -> None:
        keys = _cache_keys()
        assert sorted(keys) == sorted(KEY_FILES), f"doeff-vm の cache-keys が変わった — KEY_FILES を揃える: {keys}"
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "probe-root"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = ["doeff-vm"]\n'
            '[dependency-groups]\ndev = []\n'
            '[tool.uv.sources]\ndoeff-vm = { workspace = true }\n'
            '[tool.uv.workspace]\nmembers = ["packages/doeff-vm"]\n'
        )
        pyproject = (
            '[build-system]\nrequires = []\nbuild-backend = "stub_backend"\nbackend-path = ["."]\n'
            '[project]\nname = "doeff-vm"\nversion = "0.2.0"\nrequires-python = ">=3.10"\n'
            "[tool.uv]\ncache-keys = [\n" + "".join(f'    {{ file = "{k}" }},\n' for k in keys) + "]\n"
        )
        for rel in KEY_FILES.values():
            path = self.vm / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 0\n")
        (self.vm / "pyproject.toml").write_text(pyproject)
        (self.vm / "stub_backend.py").write_text(STUB_BACKEND)
        (self.vm / "README.md").write_text("鍵の外\n")
        self.run(["uv", "lock"])
        self.run(_land_install())

    def run(self, argv: list[str]) -> None:
        uv = shutil.which(argv[0])
        assert uv is not None, f"{argv[0]} が探し道に無い"
        done = subprocess.run([uv, *argv[1:]], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=25, check=False)
        assert done.returncode == 0, f"{argv} が rc {done.returncode}: {done.stderr}"

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
    assert workspace.calls() == before, f"何も変えていない {command()} が build の口を {workspace.calls() - before} 度呼んだ"


@pytest.mark.parametrize("key", sorted(KEY_FILES))
def test_a_change_to_each_cache_key_file_calls_the_rust_build_entry_once(workspace: Workspace, key: str) -> None:
    path = workspace.vm / KEY_FILES[key]
    path.write_text(path.read_text() + f"# {key} を替えた\n")
    before = workspace.calls()
    workspace.run(_land_install())
    assert workspace.calls() == before + 1, f"{key} を替えた後の作り直しが build の口を {workspace.calls() - before} 度呼んだ(1 度のはず)"


def test_a_change_outside_the_cache_keys_does_not_call_the_rust_build_entry(workspace: Workspace) -> None:
    readme = workspace.vm / "README.md"
    readme.write_text(readme.read_text() + "替えた\n")
    before = workspace.calls()
    workspace.run(_land_install())
    assert workspace.calls() == before
