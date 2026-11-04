"""zeus の worker の cargo の中間物の置き場の掃除の係(deploy/k8s/nodes/zeus/cargo-build-keeper.sh)と、溢れた時の build の振る舞いの検。

決まりは cargo-build-keeper.sh と cargo-build-in-memory.yaml の頭の註。df と meminfo は偽(使いは du で測り、上限は検が決める)。flock と cargo は本物。

失敗ケース: 使っている作業木の中間物を消す・形の違う物を消す・溢れた build が黙って別の置き場へ移る・cargo の lock の名か中間物の形が版で変わる
(掃除が使っている物を見分けられなくなる)。
"""

from __future__ import annotations

import fcntl
import os
import resource
import signal
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
ZEUS = PACKAGE / "deploy" / "k8s" / "nodes" / "zeus"
SCRIPT = ZEUS / "cargo-build-keeper.sh"

KIB_LIMIT = 1000
ENTRY_KIB = 100
GRACE = 900
IDLE = 14400
FAKE_DF = """#!/bin/sh
used=$(du -sk "$2" | cut -f1)
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "tmpfs {limit} $used 0 $((used * 100 / {limit}))% $2"
"""


@dataclass(frozen=True)
class World:
    """掃除の 1 回を走らせる場: 置き場・偽の df の dir・偽の meminfo。"""

    root: Path
    bin_dir: Path
    meminfo: Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    root = tmp_path / "cargo-build"
    root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    df = bin_dir / "df"
    df.write_text(FAKE_DF.format(limit=KIB_LIMIT))
    df.chmod(0o755)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 62914560 kB\n")
    return World(root=root, bin_dir=bin_dir, meminfo=meminfo)


def _entry(world: World, name: str, *, age: int, kib: int = ENTRY_KIB) -> Path:
    """作業木 1 つの中間物(cargo と同じ形)を、最後の変更を age 秒前にして作る。"""
    entry = world.root / name[:2] / name[2:]
    deps = entry / "debug" / "deps"
    deps.mkdir(parents=True)
    (deps / "blob").write_bytes(b"x" * (kib * 1024))
    (entry / "debug" / ".cargo-build-lock").touch()
    stamp = time.time() - age
    for directory in (deps, entry / "debug", entry):
        os.utime(directory, (stamp, stamp))
    return entry


def _keep(world: World, **settings: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{world.bin_dir}:{os.environ['PATH']}",
        "CARGO_BUILD_KEEPER_ROOT": str(world.root),
        "CARGO_BUILD_KEEPER_MEMINFO": str(world.meminfo),
        **settings,
    }
    return subprocess.run(["sh", str(SCRIPT)], env=env, check=True, text=True, capture_output=True, timeout=60)


@contextmanager
def _held(entry: Path) -> Iterator[None]:
    """cargo が build の間にするのと同じに、lock を flock で持つ。"""
    with (entry / "debug" / ".cargo-build-lock").open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def test_under_the_high_water_mark_nothing_is_removed(world: World) -> None:
    kept = [_entry(world, "aa11", age=GRACE * 2), _entry(world, "bb22", age=GRACE * 2)]
    _keep(world)
    assert all(entry.is_dir() for entry in kept)


def test_an_idle_workspace_is_removed_without_pressure(world: World) -> None:
    """IDLE 秒使われない中間物は、溢れそうでなくても memory を返す。"""
    idle = _entry(world, "aa11", age=IDLE + 60)
    recent = _entry(world, "bb22", age=GRACE * 2)
    _keep(world)
    assert not idle.exists()
    assert not idle.parent.exists()
    assert recent.is_dir()


def test_over_the_high_water_mark_the_oldest_go_first_down_to_the_low_mark(world: World) -> None:
    """800 KiB / 1000 KiB(80%)から、古い順に消して 50% 未満で止まる。"""
    by_age = [_entry(world, f"{index:02x}11", age=GRACE * (9 - index), kib=100) for index in range(8)]
    _keep(world)
    assert [entry.is_dir() for entry in by_age] == [False, False, False, False, True, True, True, True]


def test_a_low_machine_memory_evicts_even_under_the_high_water_mark(world: World) -> None:
    """機体の MemAvailable が下限を割れば、tmpfs に余裕が在っても古い物から返す。下限の上へ戻らなければ消せる物を全部消す。"""
    world.meminfo.write_text("MemAvailable: 1048576 kB\n")
    old = _entry(world, "aa11", age=GRACE * 2)
    _keep(world)
    assert not old.exists()


def test_a_workspace_held_by_cargo_or_just_changed_is_never_removed(world: World) -> None:
    """溢れそうでも、cargo が lock を持つ作業木と GRACE の内に変わった作業木は消さない。何も消せなければ 1 行出し、その先は ENOSPC に任せる。"""
    held = _entry(world, "aa11", age=IDLE + 60, kib=500)
    changed = _entry(world, "bb22", age=10, kib=450)
    with _held(held):
        done = _keep(world)
    assert held.is_dir()
    assert changed.is_dir()
    assert "消せる物が無い" in done.stderr


def test_only_the_shape_cargo_makes_is_removed(world: World) -> None:
    """置き場の下の形の違う物(16 進でない名・symlink の先)には触らない。"""
    other = world.root / "not-hex" / "abc"
    other.mkdir(parents=True)
    outside = world.root.parent / "outside"
    outside.mkdir()
    (world.root / "cc").mkdir()
    (world.root / "cc" / "dd").symlink_to(outside)
    stamp = time.time() - IDLE - 60
    os.utime(other, (stamp, stamp))
    os.utime(outside, (stamp, stamp))
    _keep(world)
    assert other.is_dir()
    assert outside.is_dir()
    assert (world.root / "cc" / "dd").is_symlink()


def test_leftovers_of_a_stopped_eviction_are_removed(world: World) -> None:
    leftover = world.root / ".evicting-1-abc"
    leftover.mkdir()
    _keep(world)
    assert not leftover.exists()


CARGO_TOML = '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n'


def _crate(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "Cargo.toml").write_text(CARGO_TOML)
    (path / "src" / "main.rs").write_text('fn main() { println!("probe"); }\n')
    return path


def _cargo_env(build_root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "CARGO_TARGET_DIR"}
    env["CARGO_BUILD_BUILD_DIR"] = f"{build_root}/{{workspace-path-hash}}"
    return env


def test_cargo_splits_workspaces_in_the_shape_the_keeper_reads(world: World, tmp_path: Path) -> None:
    """本物の cargo: 作業木ごとに `<16 進 2 字>/<16 進>` の dir に分かれ、最終の成果物は作業木の target/ に在り、lock の名は係の読む名。失敗ケース: cargo の
    版で形か lock の名が変わると、係は使っている中間物を見分けられない。掃除で消した後の build は組み直して通る。"""
    env = _cargo_env(world.root)
    first = _crate(tmp_path / "wt-a")
    second = _crate(tmp_path / "wt-b")
    for crate in (first, second):
        subprocess.run(["cargo", "build", "--quiet"], cwd=crate, env=env, check=True, timeout=120)
        assert (crate / "target" / "debug" / "probe").is_file()
    entries = sorted(path for path in world.root.glob("*/*") if path.is_dir())
    assert len(entries) == 2
    for entry in entries:
        assert len(entry.parent.name) == 2
        assert int(entry.parent.name + entry.name, 16) >= 0
        assert (entry / "debug" / ".cargo-build-lock").is_file()

    done = _keep(world, CARGO_BUILD_KEEPER_IDLE_SECONDS="0", CARGO_BUILD_KEEPER_GRACE_SECONDS="0")
    assert not list(world.root.glob("*/*")), done.stderr
    subprocess.run(["cargo", "build", "--quiet"], cwd=first, env=env, check=True, timeout=120)
    assert len(list(world.root.glob("*/*"))) == 1


def _limit_file_size() -> None:
    """置き場に書けなくなった時の代わり: 書きが write の失敗で返る(検の中で tmpfs を mount できない — 本物の tmpfs の ENOSPC も同じく write の失敗で返る)。"""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024, 64 * 1024))


def test_a_build_that_cannot_write_fails_loudly_and_recovers(world: World, tmp_path: Path) -> None:
    """溢れた時の形: build は code 非 0 で失敗し、黙って別の置き場へ移らない。空きが戻れば同じ命令が通り、頭の註の手(env -u)でも通る。"""
    crate = _crate(tmp_path / "wt")
    env = _cargo_env(world.root)
    failed = subprocess.run(
        ["cargo", "build", "--quiet"], cwd=crate, env=env, check=False, text=True, capture_output=True, timeout=120, preexec_fn=_limit_file_size
    )
    assert failed.returncode != 0, failed.stderr
    assert not (crate / "target" / "debug" / "probe").exists()

    subprocess.run(["cargo", "build", "--quiet"], cwd=crate, env=env, check=True, timeout=120)
    assert (crate / "target" / "debug" / "probe").is_file()

    on_disk = _crate(tmp_path / "wt-on-disk")
    without = {key: value for key, value in env.items() if key != "CARGO_BUILD_BUILD_DIR"}
    subprocess.run(["cargo", "build", "--quiet"], cwd=on_disk, env=without, check=True, timeout=120)
    assert (on_disk / "target" / "debug" / "deps").is_dir()


def _rendered_zeus_patch() -> list[Mapping[str, object]]:
    patch = yaml.safe_load((ZEUS / "cargo-build-in-memory.yaml").read_text())
    assert isinstance(patch, list)
    return patch


def test_the_sidecar_runs_unprivileged_and_reads_the_script_from_the_configmap() -> None:
    """係は特権を持たず、worker と同じ uid で、script を subPath でなく ConfigMap から読み直す(script を替えても Pod を作り直さない)。"""
    value = next(op["value"] for op in _rendered_zeus_patch() if str(op["path"]).endswith("/initContainers/-"))
    assert isinstance(value, dict)
    assert value["restartPolicy"] == "Always"
    assert value["securityContext"] == {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True}
    assert all("subPath" not in mount for mount in value["volumeMounts"])
    assert "sh /etc/cargo-build-keeper/cargo-build-keeper.sh" in value["args"][0]
