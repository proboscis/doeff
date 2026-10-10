"""coordinator の記録(WAL と snapshot)の控え — script deploy/k8s/coordinator/coord-wal-backup.sh と、それを ConfigMap coord-wal-backup に
組んで init container wal-backup で走らせる宣言(同じ dir)を守る検。控えの仕組みは宣言と同じ dir が持つ(控えは記録を戻す元)。

script そのものを sh で実際に走らせる(置き場は検ごとの一時の dir):
- 緑: WAL と snapshot を UTC の YYYYMMDDTHHMMSSZ の名の dir へ写し、sha256 の控えが元と合い、控えた 1 行を出す。新しい順に 5 つを残す。
- 消す所の失敗ケース(消されない事を 1 本ずつ): 名の形に合わない dir・形の名の symlink(指す先の中身も残る)・形の名の file・.partial の symlink。
- 起動を止めない所の失敗ケース: 控えを取れない時は、必ず訳の 1 行("coord-wal-backup: 控えを飛ばす")を出して 0 で終わる(黙って飛ばさない)
  — WAL が無い・空きが下限を切る・.partial が symlink・script が読めない(Deployment の外側の sh)。
宣言の検(``kubectl kustomize`` で coordinator の dir を組んだ物 — Flux がその dir を当てる時と同じ物): init container の順(own-state の
後)・env の値・控えの置き場が WAL の dir の外・ConfigMap が optional で、その ConfigMap がこの dir の生成(名に hash なし・中身は script の
file そのもの)で在る。kubectl が無い機体では赤にする(飛ばさない — 黙って緑にしない)。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
COORDINATOR_DIR = PACKAGE / "deploy" / "k8s" / "coordinator"
SCRIPT = COORDINATOR_DIR / "coord-wal-backup.sh"
CONFIGMAP = "coord-wal-backup"
NAMESPACE = "agent-worker"

STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SKIP = "coord-wal-backup: 控えを飛ばす"
OLD_STAMPS = (
    "20200101T000000Z",
    "20200102T000000Z",
    "20200103T000000Z",
    "20200104T000000Z",
    "20200105T000000Z",
    "20200106T000000Z",
)

Manifest = dict[str, object]


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{where} が mapping でない: {value!r}")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{where} が list でない: {value!r}")
    return value


# --- script を sh で走らせる検 ---


def run_backup(root: Path, min_free: int = 0) -> subprocess.CompletedProcess[str]:
    """script を、root の下の wal/ と backups/ を置き場として走らせる。"""
    env = {
        "PATH": os.environ["PATH"],
        "WAL_DIR": str(root / "wal"),
        "BACKUP_DIR": str(root / "backups"),
        "BACKUP_KEEP": "5",
        "BACKUP_MIN_FREE_BYTES": str(min_free),
    }
    return subprocess.run(
        ["sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=60
    )


def with_wal(root: Path) -> None:
    (root / "wal").mkdir()
    (root / "wal" / "wal.jsonl").write_text('{"op": "put"}\n')
    (root / "wal" / "snapshot.json").write_text('{"rows": {}}\n')


def stamped(root: Path) -> list[str]:
    """控えの置き場の直下の、名が形に合う本物の dir の名(新しい順)。"""
    backups = root / "backups"
    return sorted(
        (
            p.name
            for p in backups.iterdir()
            if p.is_dir() and not p.is_symlink() and STAMP.match(p.name)
        ),
        reverse=True,
    )


def test_the_backup_copies_the_wal_and_snapshot_under_a_stamped_name_with_their_sha256(
    tmp_path: Path,
) -> None:
    root = tmp_path
    with_wal(root)
    done = run_backup(root)
    assert done.returncode == 0, done
    names = stamped(root)
    assert len(names) == 1, names
    copy = root / "backups" / names[0]
    for name in ("wal.jsonl", "snapshot.json"):
        assert (copy / name).read_bytes() == (root / "wal" / name).read_bytes()
    # sha256sum の行 = 「<digest>  <name>」— 名 → digest の写像にする。
    sums = {
        name: digest
        for digest, name in (
            line.split("  ", 1) for line in (copy / "SHA256SUMS").read_text().splitlines()
        )
    }
    assert sums == {
        name: hashlib.sha256((root / "wal" / name).read_bytes()).hexdigest()
        for name in ("wal.jsonl", "snapshot.json")
    }
    assert f"coord-wal-backup: 控えた {copy}" in done.stdout, done.stdout
    assert not (root / "backups" / ".partial").exists()


def test_the_newest_five_backups_are_kept(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    for stamp in OLD_STAMPS:
        (root / "backups" / stamp).mkdir(parents=True)
    done = run_backup(root)
    assert done.returncode == 0, done
    names = stamped(root)
    assert len(names) == 5, names
    assert names[1:] == [
        "20200106T000000Z",
        "20200105T000000Z",
        "20200104T000000Z",
        "20200103T000000Z",
    ], names
    assert "古い控え 20200101T000000Z を消した" in done.stdout, done.stdout
    assert "古い控え 20200102T000000Z を消した" in done.stdout, done.stdout


def test_a_directory_whose_name_is_not_a_stamp_is_not_removed(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    for stamp in OLD_STAMPS:
        (root / "backups" / stamp).mkdir(parents=True)
    odds = ("o12-20261005T055928", "keep-me", "2019-01-01")
    for odd in odds:
        (root / "backups" / odd).mkdir()
        (root / "backups" / odd / "note").write_text(odd)
    assert run_backup(root).returncode == 0
    for odd in odds:
        assert (root / "backups" / odd / "note").read_text() == odd


def test_a_symlink_with_a_stamp_name_is_not_removed_nor_its_target(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    target = root / "elsewhere"
    target.mkdir()
    (target / "precious").write_text("x")
    (root / "backups").mkdir()
    (root / "backups" / "20190101T000000Z").symlink_to(target, target_is_directory=True)
    for stamp in OLD_STAMPS:
        (root / "backups" / stamp).mkdir()
    assert run_backup(root).returncode == 0
    assert (root / "backups" / "20190101T000000Z").is_symlink()
    assert (target / "precious").read_text() == "x"


def test_a_file_with_a_stamp_name_is_not_removed(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    (root / "backups").mkdir()
    (root / "backups" / "20190101T000000Z").write_text("not a backup")
    for stamp in OLD_STAMPS:
        (root / "backups" / stamp).mkdir()
    assert run_backup(root).returncode == 0
    assert (root / "backups" / "20190101T000000Z").read_text() == "not a backup"


def test_a_partial_symlink_is_left_alone_and_the_skip_is_named(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    target = root / "elsewhere"
    target.mkdir()
    (target / "precious").write_text("x")
    (root / "backups").mkdir()
    (root / "backups" / ".partial").symlink_to(target, target_is_directory=True)
    done = run_backup(root)
    assert done.returncode == 0, done
    assert f"{SKIP} — {root / 'backups' / '.partial'} が symlink" in done.stdout, done.stdout
    assert (target / "precious").read_text() == "x"
    assert sorted(p.name for p in target.iterdir()) == ["precious"]
    assert stamped(root) == []


def test_a_leftover_partial_directory_is_cleared_before_the_copy(tmp_path: Path) -> None:
    root = tmp_path
    with_wal(root)
    (root / "backups" / ".partial").mkdir(parents=True)
    (root / "backups" / ".partial" / "wal.jsonl").write_text("half")
    assert run_backup(root).returncode == 0
    assert not (root / "backups" / ".partial").exists()
    assert len(stamped(root)) == 1


def test_every_skip_names_its_reason_and_lets_the_coordinator_start(tmp_path: Path) -> None:
    """起動を止めない所の失敗ケース: どの飛ばしも 0 で終わり、訳の 1 行を必ず出す(黙って飛ばさない)。"""
    no_wal = tmp_path / "no-wal"
    no_wal.mkdir()
    (no_wal / "wal").mkdir()
    done = run_backup(no_wal)
    assert done.returncode == 0, done
    assert f"{SKIP} — {no_wal / 'wal'} に wal.jsonl も snapshot.json も無い" in done.stdout, done
    low_free = tmp_path / "low-free"
    low_free.mkdir()
    with_wal(low_free)
    done = run_backup(low_free, min_free=1 << 62)
    assert done.returncode == 0, done
    assert SKIP in done.stdout, done
    assert "下限" in done.stdout, done
    assert stamped(low_free) == []


# --- 宣言の検(coordinator の dir を kubectl kustomize で組んだ物)---


@pytest.fixture(scope="module")
def built() -> list[Manifest]:
    """coordinator の dir を kubectl kustomize で組み立てた物の全部(Flux がその dir を当てる時と同じ物)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, (
        "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    )
    done = subprocess.run(
        [kubectl, "kustomize", str(COORDINATOR_DIR)],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert done.returncode == 0, (
        f"kubectl kustomize {COORDINATOR_DIR} が失敗した: {done.stderr.strip()}"
    )
    return [
        _mapping(doc, "宣言の 1 つ") for doc in yaml.safe_load_all(done.stdout) if doc is not None
    ]


@pytest.fixture(scope="module")
def coordinator_pod(built: list[Manifest]) -> dict[str, object]:
    found = [
        doc
        for doc in built
        if doc["kind"] == "Deployment"
        and _mapping(doc["metadata"], "metadata")["name"] == "coordinator"
    ]
    assert len(found) == 1, "coordinator の Deployment がちょうど 1 つ無い"
    template = _mapping(_mapping(found[0]["spec"], "spec")["template"], "template")
    return _mapping(template["spec"], "Pod の spec")


@pytest.fixture(scope="module")
def init_container(coordinator_pod: dict[str, object]) -> dict[str, object]:
    found = [
        _mapping(container, "initContainer")
        for container in _sequence(coordinator_pod["initContainers"], "initContainers")
        if _mapping(container, "initContainer")["name"] == "wal-backup"
    ]
    assert len(found) == 1, "init container wal-backup がちょうど 1 つ無い"
    return found[0]


def test_a_missing_script_still_lets_the_coordinator_start_and_says_so(
    init_container: dict[str, object],
) -> None:
    """Deployment の外側の sh: script が読めない(ConfigMap が無い・壊れた)時も 0 で終わり、訳の 1 行を出す。"""
    command = _sequence(init_container["command"], "command")
    assert command[:2] == ["sh", "-c"], command
    done = subprocess.run(
        ["sh", "-c", str(command[2])],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={"PATH": os.environ["PATH"]},
    )
    assert done.returncode == 0, done
    assert f"{SKIP} — script が失敗した" in done.stdout, done.stdout


def test_the_coordinator_runs_the_backup_after_handing_the_volume_over_and_before_it_starts(
    coordinator_pod: dict[str, object], init_container: dict[str, object]
) -> None:
    names = [
        _mapping(c, "initContainer")["name"]
        for c in _sequence(coordinator_pod["initContainers"], "initContainers")
    ]
    assert names == ["own-state", "wal-backup"], names
    env = {
        _mapping(e, "env の 1 行")["name"]: _mapping(e, "env の 1 行").get("value")
        for e in _sequence(init_container["env"], "env")
    }
    assert env == {
        "WAL_DIR": "/work/coord/wal",
        "BACKUP_DIR": "/work/coord/backups",
        "BACKUP_KEEP": "5",
        "BACKUP_MIN_FREE_BYTES": str(16 * 1024**3),
    }, env
    wal = Path(str(env["WAL_DIR"]))
    backups = Path(str(env["BACKUP_DIR"]))
    assert backups != wal, (wal, backups)
    assert wal not in backups.parents, (wal, backups)


def test_the_script_comes_from_an_optional_configmap_generated_here_from_the_file(
    built: list[Manifest], coordinator_pod: dict[str, object], init_container: dict[str, object]
) -> None:
    """script は、この dir の生成で組んだ ConfigMap coord-wal-backup(名に hash なし・中身は script の file そのもの)から読む。
    反例: 生成が無い(上に載る系に任せる)と、ConfigMap が optional なので控えが黙って飛ぶ。名に hash が付くと、名で参照する
    ほかの宣言から外れる。"""
    mounts = {
        _mapping(m, "volumeMount")["name"]: _mapping(m, "volumeMount")
        for m in _sequence(init_container["volumeMounts"], "volumeMounts")
    }
    script_mount = mounts[CONFIGMAP]
    assert script_mount.get("readOnly") is True
    command = str(_sequence(init_container["command"], "command")[2])
    assert f"sh {script_mount['mountPath']}/backup.sh ||" in command, command
    volumes = {
        _mapping(v, "volume")["name"]: _mapping(v, "volume")
        for v in _sequence(coordinator_pod["volumes"], "volumes")
    }
    assert _mapping(volumes[CONFIGMAP]["configMap"], "configMap") == {
        "name": CONFIGMAP,
        "optional": True,
    }
    configmaps = [doc for doc in built if doc["kind"] == "ConfigMap"]
    assert [
        (
            _mapping(doc["metadata"], "metadata")["name"],
            _mapping(doc["metadata"], "metadata")["namespace"],
        )
        for doc in configmaps
    ] == [(CONFIGMAP, NAMESPACE)]
    assert _mapping(configmaps[0]["data"], "data") == {
        "backup.sh": SCRIPT.read_text(encoding="utf-8")
    }
