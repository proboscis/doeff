"""doeff の Rust の package の build の口(tools/doeff_cargo_backend.py)を確かめる(agora-redesign #1493)。

口は cargo の target を package の dir の外の、1 回の build ごとの一時の dir に置き、wheel を作ったら消す。
作業木ごとに target が残って disk を埋めた(agora-redesign #1472)のを止め、しかも中身の違う作業木どうしで
build を取り違えない事を、小さな bin の crate を 2 か所に置いて本物の maturin と cargo で組んで確かめる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "tools" / "doeff_cargo_backend.py"
BUILD_TIMEOUT_SECONDS = 300
TEMP_PREFIX = "doeff-cargo-target-"

# PEP 517 の frontend と同じく、package の dir で build の口の hook(argv[1])を呼び、出来た file の名を最後の行に出す。
_CALL_HOOK = (
    "import sys; sys.path.insert(0, '.'); import doeff_cargo_backend as b; "
    "hooks = {'build_wheel': b.build_wheel, 'build_editable': b.build_editable, 'build_sdist': b.build_sdist}; "
    "print(hooks[sys.argv[1]](sys.argv[2]))"
)


def _write_checkout(root: Path, greeting: str, *, body: str | None = None) -> Path:
    """作業木の 1 つに見立てた package の dir を作る(crate の名は同じで、出す文字だけが違う)。

    body を渡すと main.rs の中身をそれにする(組めない crate を作るため)。
    """
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[[bin]]\nname = "probe"\npath = "src/main.rs"\n'
    )
    match body:
        case None:
            (root / "src" / "main.rs").write_text(f'fn main() {{ println!("{greeting}"); }}\n')
        case str():
            (root / "src" / "main.rs").write_text(body)
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


def _start_build(
    package: Path, wheel_dir: Path, env: dict[str, str], hook: str = "build_wheel"
) -> subprocess.Popen[str]:
    """frontend と同じく別の process で build の hook を呼び始める(同時の build を試すため、待たずに返す)。"""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-c", _CALL_HOOK, hook, str(wheel_dir)],
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


def _build(package: Path, wheel_dir: Path, env: dict[str, str], hook: str = "build_wheel") -> Path:
    """1 つの package を hook で組んで、出来た file の path を返す。"""
    return _finish_build(_start_build(package, wheel_dir, env, hook), wheel_dir)


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
    return sorted(path.name for path in temp_root.iterdir() if path.name.startswith(TEMP_PREFIX))


def _run_wrapped(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`python doeff_cargo_backend.py <命令…>`(文書が勧める maturin develop の包み)で命令を走らせる。"""
    return subprocess.run(
        [sys.executable, str(BACKEND), *command],
        env=env,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,  # 包みが命令の終了 code をそのまま返す事を、呼び手が確かめる
    )


def test_a_shared_target_hands_the_older_checkout_the_other_checkouts_build(tmp_path: Path) -> None:
    """反例: 2 つの作業木が 1 つの target を共有すると、先に切った作業木 B は A の build を黙って受け取る。

    cargo は source の時刻で新旧を判定し、成果物を workspace の中の相対 path で名付けるため。口が共有の
    target を使わず、1 回の build ごとの一時の dir を使う理由(agora-redesign #1472 の comment)。
    """
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, tmp_path / "shared-target")

    greetings = [
        _greeting(
            _build(package, tmp_path / "wheels" / package.name, env), tmp_path / "x" / package.name
        )
        for package in (newer, older)
    ]

    assert greetings == ["from-A", "from-A"]


@pytest.mark.parametrize("hook", ["build_wheel", "build_editable"])
def test_each_checkout_builds_its_own_wheel_and_leaves_no_target(tmp_path: Path, hook: str) -> None:
    """口に任せると、2 つの作業木はそれぞれ自分の中身の wheel を得て、package の中にも一時の置き場にも target が残らない。

    uv sync は workspace の一員を build_editable で入れるので、両方の hook で確かめる。
    """
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    greetings = [
        _greeting(
            _build(package, tmp_path / "wheels" / package.name, env, hook),
            tmp_path / "x" / package.name,
        )
        for package in (newer, older)
    ]

    assert greetings == ["from-A", "from-B"]
    assert [package.name for package in (newer, older) if (package / "target").exists()] == []
    assert _leftovers(temp_root) == []


def test_two_builds_at_once_each_get_their_own_wheel(tmp_path: Path) -> None:
    """2 つの作業木の build を同時に走らせても、両方とも成功し、それぞれ自分の中身の wheel を得る。"""
    newer, older = _two_checkouts(tmp_path)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)

    running = [
        (package, _start_build(package, tmp_path / "wheels" / package.name, env))
        for package in (newer, older)
    ]
    wheels = [
        _finish_build(process, tmp_path / "wheels" / package.name) for package, process in running
    ]

    assert [_greeting(wheel, tmp_path / "x" / wheel.parent.name) for wheel in wheels] == [
        "from-A",
        "from-B",
    ]
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
        if any(
            requirement.startswith("maturin")
            for requirement in tomllib.loads(pyproject.read_text())
            .get("build-system", {})
            .get("requires", [])
        )
    )
    backends = {
        package.name: tomllib.loads((package / "pyproject.toml").read_text())["build-system"]
        for package in maturin_packages
    }

    assert sorted(backends) == [
        "doeff-agentic-cli",
        "doeff-effect-analyzer",
        "doeff-indexer",
        "doeff-linter",
        "doeff-vm",
    ]
    assert {
        name: (system["build-backend"], system.get("backend-path"))
        for name, system in backends.items()
    } == {
        "doeff-agentic-cli": ("doeff_cargo_backend", ["."]),
        "doeff-effect-analyzer": ("doeff_cargo_backend", ["."]),
        "doeff-indexer": ("doeff_indexer_build_backend", ["."]),
        "doeff-linter": ("doeff_cargo_backend", ["."]),
        "doeff-vm": ("doeff_cargo_backend", ["."]),
    }
    assert [
        package.name
        for package in maturin_packages
        if (package / "doeff_cargo_backend.py").resolve() != BACKEND
    ] == []
    assert (
        "from doeff_cargo_backend import cargo_target_dir"
        in (REPO / "packages" / "doeff-indexer" / "doeff_indexer_build_backend.py").read_text()
    )


def test_a_failed_build_leaves_no_target(tmp_path: Path) -> None:
    """compile に落ちた build も、一時の target を消して抜ける(失敗のたびに disk が減らない)。"""
    broken = _write_checkout(tmp_path / "wt-broken", "", body="fn main() { this is not rust }\n")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()

    process = _start_build(broken, tmp_path / "wheels", _build_env(temp_root, None))
    process.communicate(timeout=BUILD_TIMEOUT_SECONDS)

    assert process.returncode != 0
    assert not (broken / "target").exists()
    assert _leftovers(temp_root) == []


def test_the_command_wrapper_runs_inside_a_temporary_target_and_removes_it(tmp_path: Path) -> None:
    """`python doeff_cargo_backend.py <命令…>` は命令に一時の target を渡し、命令の終了 code を返し、target を消す。"""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    report = "import os, sys; print(os.environ['CARGO_TARGET_DIR']); sys.exit(3)"

    finished = _run_wrapped([sys.executable, "-c", report], _build_env(temp_root, None))

    given = Path(finished.stdout.strip())
    assert finished.returncode == 3
    assert given.parent == temp_root
    assert given.name.startswith(f"{TEMP_PREFIX}")
    assert not given.exists()


def test_a_build_sweeps_targets_left_by_killed_builds_but_not_live_ones(tmp_path: Path) -> None:
    """SIGTERM・SIGKILL で止められた build は後始末をしない。次の build が、持ち主の process が居ない dir だけを消す。"""
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    finished = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(finished.stdout.strip())
    killed = temp_root / f"{TEMP_PREFIX}{dead_pid}-left"
    running = temp_root / f"{TEMP_PREFIX}{os.getpid()}-running"
    foreign = temp_root / f"{TEMP_PREFIX}not-ours"
    for path in (killed, running, foreign):
        (path / "release").mkdir(parents=True)

    _run_wrapped(["true"], _build_env(temp_root, None))

    assert _leftovers(temp_root) == sorted([running.name, foreign.name])


def test_every_maturin_package_sdist_carries_the_backend_at_its_root(tmp_path: Path) -> None:
    """PyPI の sdist から組む人のために、口の file は sdist の根(pyproject と同じ所)に普通の file として入る。

    path 依存の crate を持つ package(doeff-linter・doeff-vm)は、maturin が crate を <名>/ の下へ移すので、
    pyproject の include で根に写す。sdist を組んでも package の中に target が出来ない事もあわせて見る。
    """
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _build_env(temp_root, None)
    call_declared = (
        "import importlib, sys; sys.path.insert(0, '.'); "
        "print(importlib.import_module(sys.argv[1]).build_sdist(sys.argv[2]))"
    )
    missing: dict[str, list[str]] = {}
    for package in sorted(REPO.glob("packages/doeff-*/doeff_cargo_backend.py")):
        root = package.parent
        backend = tomllib.loads((root / "pyproject.toml").read_text())["build-system"][
            "build-backend"
        ]
        had_target = (root / "target").exists()
        out = tmp_path / "sdist" / root.name
        out.mkdir(parents=True)
        built = subprocess.run(
            [sys.executable, "-c", call_declared, backend, str(out)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_SECONDS,
            check=False,  # 落ちた時に stderr を assert の説明に出すため
        )
        assert built.returncode == 0, built.stderr
        with tarfile.open(out / built.stdout.strip().splitlines()[-1]) as sdist:
            (top,) = {member.name.split("/", 1)[0] for member in sdist.getmembers()}
            regular = {member.name for member in sdist.getmembers() if member.isfile()}
        wanted = [f"{top}/pyproject.toml", f"{top}/doeff_cargo_backend.py", f"{top}/{backend}.py"]
        missing[root.name] = [name for name in wanted if name not in regular]
        assert (root / "target").exists() == had_target

    assert missing == {name: [] for name in missing}
    assert sorted(missing) == [
        "doeff-agentic-cli",
        "doeff-effect-analyzer",
        "doeff-indexer",
        "doeff-linter",
        "doeff-vm",
    ]
    assert _leftovers(temp_root) == []
