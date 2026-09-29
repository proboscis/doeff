"""doeff の Rust の package の build の口(tools/doeff_cargo_backend.py)を確かめる(agora-redesign #1493)。

口は cargo の target を package の dir の外の、1 回の build ごとの一時の dir に置き、wheel を作ったら消す。
作業木ごとに target が残って disk を埋めた(agora-redesign #1472)のを止め、しかも中身の違う作業木どうしで
build を取り違えない事を、小さな bin の crate を 2 か所に置いて本物の maturin と cargo で組んで確かめる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
BUILD_TIMEOUT_SECONDS = 300

# PEP 517 の frontend と同じく、package の dir で build の口の build_wheel を呼び、wheel の名を最後の行に出す。
_BUILD_WHEEL = (
    "import sys; sys.path.insert(0, '.'); import doeff_cargo_backend as b; print(b.build_wheel(sys.argv[1]))"
)


def _write_checkout(root: Path, greeting: str) -> Path:
    """作業木の 1 つに見立てた package の dir を作る(crate の名は同じで、出す文字だけが違う)。"""
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[[bin]]\nname = "probe"\npath = "src/main.rs"\n'
    )
    (root / "src" / "main.rs").write_text(f'fn main() {{ println!("{greeting}"); }}\n')
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["maturin>=1.0,<2.0"]\nbuild-backend = "doeff_cargo_backend"\n'
        'backend-path = ["."]\n\n[project]\nname = "probe"\nversion = "0.1.0"\n\n'
        '[tool.maturin]\nbindings = "bin"\n'
    )
    (root / "doeff_cargo_backend.py").symlink_to(BACKEND)
    return root


def _age(root: Path, seconds: int) -> None:
    """source の file の時刻を過去へずらす — 先に切って、後で組む作業木に見立てるため。"""
    for path in root.rglob("*"):
        stat = path.stat()
        os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds), follow_symlinks=False)


def _two_checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """B を先に切り(source が古い)、A を後に切った 2 つの作業木を作る。"""
    older = _write_checkout(tmp_path / "wt-b", "from-B")
    _age(older, 3600)
    newer = _write_checkout(tmp_path / "wt-a", "from-A")
    return newer, older


def _build_env(temp_root: Path, target_dir: Path | None) -> dict[str, str]:
    """build の process の env: 一時の dir の置き場を temp_root に向け、CARGO_TARGET_DIR は渡す時だけ置く。"""
    env = {name: value for name, value in os.environ.items() if name != "CARGO_TARGET_DIR"}
    env["TMPDIR"] = str(temp_root)
    match target_dir:
        case None:
            pass
        case Path() as given:
            env["CARGO_TARGET_DIR"] = str(given)
    return env


def _start_build(package: Path, wheel_dir: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    """frontend と同じく別の process で build_wheel を呼び始める(同時の build を試すため、待たずに返す)。"""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-c", _BUILD_WHEEL, str(wheel_dir)],
        cwd=package,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_build(process: subprocess.Popen[str], wheel_dir: Path) -> Path:
    """build の終わりを待ち、出来た wheel の path を返す。"""
    out, err = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
    assert process.returncode == 0, err
    return wheel_dir / out.strip().splitlines()[-1]


def _build(package: Path, wheel_dir: Path, env: dict[str, str]) -> Path:
    """1 つの package の wheel を組んで、その path を返す。"""
    return _finish_build(_start_build(package, wheel_dir, env), wheel_dir)


def _greeting(wheel: Path, scratch: Path) -> str:
    """wheel に入った probe の binary を取り出して走らせ、どの作業木の中身で組まれたかを読む。"""
    with zipfile.ZipFile(wheel) as archive:
        (member,) = [name for name in archive.namelist() if name.endswith("/scripts/probe")]
        archive.extract(member, scratch)
    binary = scratch / member
    binary.chmod(0o755)
    return subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout.strip()


def _leftovers(temp_root: Path) -> list[str]:
    """一時の dir の置き場に残った build の target の名。"""
    return sorted(path.name for path in temp_root.iterdir() if path.name.startswith("doeff-cargo-target-"))


def test_a_shared_target_hands_the_older_checkout_the_other_checkouts_build(tmp_path: Path) -> None:
    """反例: 2 つの作業木が 1 つの target を共有すると、先に切った作業木 B は A の build を黙って受け取る。

    cargo は source の時刻で新旧を判定し、成果物を workspace の中の相対 path で名付けるため。口が共有の
    target を使わず、1 回の build ごとの一時の dir を使う理由(agora-redesign #1472 の comment)。
    """
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, tmp_path / "shared-target")

    greetings = [_greeting(_build(package, tmp_path / "wheels" / package.name, env), tmp_path / "x" / package.name)
                 for package in (newer, older)]

    assert greetings == ["from-A", "from-A"]


def test_each_checkout_builds_its_own_wheel_and_leaves_no_target(tmp_path: Path) -> None:
    """口に任せると、2 つの作業木はそれぞれ自分の中身の wheel を得て、package の中にも一時の置き場にも target が残らない。"""
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    greetings = [_greeting(_build(package, tmp_path / "wheels" / package.name, env), tmp_path / "x" / package.name)
                 for package in (newer, older)]

    assert greetings == ["from-A", "from-B"]
    assert [package.name for package in (newer, older) if (package / "target").exists()] == []
    assert _leftovers(temp_root) == []


def test_two_builds_at_once_each_get_their_own_wheel(tmp_path: Path) -> None:
    """2 つの作業木の build を同時に走らせても、両方とも成功し、それぞれ自分の中身の wheel を得る。"""
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    running = [(package, _start_build(package, tmp_path / "wheels" / package.name, env)) for package in (newer, older)]
    wheels = [_finish_build(process, tmp_path / "wheels" / package.name) for package, process in running]

    assert [_greeting(wheel, tmp_path / "x" / wheel.parent.name) for wheel in wheels] == ["from-A", "from-B"]
    assert _leftovers(temp_root) == []


def test_a_given_target_dir_is_used_and_kept(tmp_path: Path) -> None:
    """利用者が CARGO_TARGET_DIR を渡すと、口はそこに組み、消さない(差分の build を使う逃げ道)。"""
    newer, _ = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    given = tmp_path / "given-target"

    _build(newer, tmp_path / "wheels", _build_env(temp_root, given))

    assert (given / "release" / "probe").is_file()
    assert not (newer / "target").exists()
    assert _leftovers(temp_root) == []


def test_every_maturin_package_builds_through_the_backend() -> None:
    """maturin で組む doeff の package は、どれも口を通る(新しい Rust の package が作業木に target を作り直さない)。"""
    maturin_packages = sorted(
        pyproject.parent
        for pyproject in REPO.glob("packages/*/pyproject.toml")
        if any(requirement.startswith("maturin")
               for requirement in tomllib.loads(pyproject.read_text()).get("build-system", {}).get("requires", []))
    )
    backends = {
        package.name: tomllib.loads((package / "pyproject.toml").read_text())["build-system"] for package in maturin_packages
    }

    assert sorted(backends) == [
        "doeff-agentic-cli", "doeff-effect-analyzer", "doeff-indexer", "doeff-linter", "doeff-vm",
    ]
    assert {name: (system["build-backend"], system.get("backend-path")) for name, system in backends.items()} == {
        "doeff-agentic-cli": ("doeff_cargo_backend", ["."]),
        "doeff-effect-analyzer": ("doeff_cargo_backend", ["."]),
        "doeff-indexer": ("doeff_indexer_build_backend", ["."]),
        "doeff-linter": ("doeff_cargo_backend", ["."]),
        "doeff-vm": ("doeff_cargo_backend", ["."]),
    }
    assert [package.name for package in maturin_packages
            if (package / "doeff_cargo_backend.py").resolve() != BACKEND] == []
    assert "from doeff_cargo_backend import cargo_target_dir" in (
        REPO / "packages" / "doeff-indexer" / "doeff_indexer_build_backend.py"
    ).read_text()
