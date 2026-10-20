"""根の disk を worker と Longhorn が分け合う機体の分け前(deploy/k8s/disk-share/shares.yaml)が disk に収まり、Flux が当てる worker の env と
Longhorn の disk の予約がその宣言から導かれている事の検(card ki-3dfe8e9d820a)。

2026-10-21 02:30 JST に k3s-1 の worker が near を名乗った。worker は roots を 20 GiB まで、Longhorn は写しを名目の合計 (disk - 既定の予約 30%)
× 200% まで受ける作りで、2 つが disk を独り占めする前提の上限を別々に持ち、どちらも自分の上限の内のまま near の線を割った。分け前の宣言は
1 か所(shares.yaml)で、worker の空きの最低・roots の上限と Longhorn の予約をそこから導く(deploy/k8s/disk-share/derive.py)。

確かめる事:
  (1) 宣言が釣り合う — 機体ごとに Longhorn の分(残り)が正で、導いた予約の下で Longhorn が受ける名目の上限がその分の内。
  (2) 書いてある導いた file が宣言から導いた物と同じ(宣言を直して導き直し忘れた file が無い)。
  (3) Flux が当てる組み立て(kubectl kustomize workers)の worker の env と Longhorn の予約が宣言の値 — 予約を手で下げた file は、
      Longhorn が分を越えて受けるので赤。
  (4) worker の分の空き(最低 + near の幅 + 戻りの幅)は、worker が near から ok へ戻る線と同じ — 幅は worker の code から読む。
失敗ケース(赤になる事の検): 分け前の合計が disk を越える宣言・Longhorn の既定の予約(30%)のままの Node・綴りの誤った鍵。
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
K8S = PACKAGE / "deploy" / "k8s"
DERIVE = K8S / "disk-share" / "derive.py"
WORKER_ROLE = "worker"
NODE_LABEL = "doeff.dev/node"
PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"
# Longhorn が k3s の default-disk に置いた既定の予約(storage-reserved-percentage-for-default-disk 30% — 2026-10-21 の値)。
LONGHORN_DEFAULT_RESERVED = 31_647_123_456


def _load_derive() -> ModuleType:
    """宣言を導く script(deploy の下の file — package でない)を module として読む。import の仕組みを通さずに読む — 通すと
    deploy/k8s の下に __pycache__ の .pyc を書き、宣言の file を全部読む語の検(test_k8s_declarations)がそれを読んで落ちる。"""
    module = ModuleType("disk_share_derive")
    module.__file__ = str(DERIVE)
    # dataclass は自分の module を sys.modules から引く(文字の註釈を解くため)— 読む前に名を置く。
    sys.modules[module.__name__] = module
    exec(compile(DERIVE.read_text(encoding="utf-8"), str(DERIVE), "exec"), module.__dict__)
    return module


derive = _load_derive()


@dataclasses.dataclass(frozen=True)
class Applied:
    """Flux が当てる組み立てから読んだ、機体 1 つの値(worker の env の 2 つと Longhorn の disk の予約)。"""

    node: str
    min_free_gib: str | None
    roots_gib: str | None
    longhorn_disk: str
    storage_reserved: int
    prune: str | None


def _build_workers() -> list[dict[str, object]]:
    """worker の組を kubectl kustomize で組み立てた物(Flux の Kustomization が当てる物と同じ)。kubectl が無ければ赤(飛ばさない)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, (
        "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    )
    done = subprocess.run(
        [kubectl, "kustomize", str(K8S / "workers")],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert done.returncode == 0, f"kubectl kustomize workers が失敗した: {done.stderr.strip()}"
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc is not None]


def _worker_env(docs: list[dict[str, object]], node: str, name: str) -> str | None:
    """機体の worker の Deployment の env の値(無ければ None)— 宣言から導いた値が当たるかを読むため。"""
    workers = [
        doc
        for doc in docs
        if doc["kind"] == "Deployment" and doc["metadata"].get("labels", {}).get(NODE_LABEL) == node
    ]
    assert len(workers) == 1, f"{node} の worker の Deployment がちょうど 1 つ無い: {len(workers)}"
    (container,) = [
        c for c in workers[0]["spec"]["template"]["spec"]["containers"] if c["name"] == WORKER_ROLE
    ]
    values = [e.get("value") for e in container.get("env", []) if e["name"] == name]
    assert len(values) <= 1, f"{node} の worker の env {name} が 2 つ以上"
    return values[0] if values else None


def _applied(docs: list[dict[str, object]]) -> tuple[Applied, ...]:
    """組み立ての Longhorn の Node ごとに、その機体の worker の env と予約を並べる。"""
    nodes = [
        doc
        for doc in docs
        if doc["kind"] == "Node" and doc["apiVersion"].startswith("longhorn.io/")
    ]
    return tuple(
        Applied(
            node=doc["metadata"]["name"],
            min_free_gib=_worker_env(docs, doc["metadata"]["name"], "WORKER_ENV_MIN_FREE_GIB"),
            roots_gib=_worker_env(docs, doc["metadata"]["name"], "WORKER_ENV_ROOTS_GIB"),
            longhorn_disk=disk,
            storage_reserved=int(spec["storageReserved"]),
            prune=doc["metadata"].get("annotations", {}).get(PRUNE_ANNOTATION),
        )
        for doc in nodes
        for disk, spec in doc["spec"]["disks"].items()
    )


def _applied_on(applied: tuple[Applied, ...], node: str) -> Applied:
    """組み立てから読んだ機体 node の値(ちょうど 1 つ)— 宣言の機体ごとに当てる値を引くため。"""
    found = [a for a in applied if a.node == node]
    assert len(found) == 1, f"{node} の Longhorn の Node の予約がちょうど 1 つ無い: {found}"
    return found[0]


def _over_share(share: object, reserved: int, percent: int, margins: object) -> int:
    """予約 reserved の下で Longhorn が受ける名目の上限が、宣言の Longhorn の分を越える量(0 以下なら分の内)。"""
    return derive.longhorn_admits(share.disk_bytes, reserved, percent) - derive.longhorn_bytes(
        share, margins
    )


@pytest.fixture(scope="module")
def declaration() -> object:
    return derive.load()


@pytest.fixture(scope="module")
def margins() -> object:
    return derive.worker_margins()


@pytest.fixture(scope="module")
def applied() -> tuple[Applied, ...]:
    return _applied(_build_workers())


def test_each_declared_share_fits_its_disk(declaration: object, margins: object) -> None:
    """機体ごとに、OS ほか・別の系・worker の分を引いた Longhorn の分が正で、導いた予約の下で Longhorn が受ける名目の上限がその分の内。"""
    assert derive.faults(declaration, margins) == ()
    for share in declaration.nodes:
        assert derive.longhorn_bytes(share, margins) > 0, share.node


def test_derived_files_are_the_declaration(declaration: object, margins: object) -> None:
    """書いてある worker の patch と Longhorn の Node の file は、宣言から導いた物と同じ(導き直しは derive.py)。"""
    assert derive.stale(derive.render(declaration, margins)) == ()


def test_flux_applies_the_worker_amounts_of_the_declaration(
    declaration: object, applied: tuple[Applied, ...]
) -> None:
    """Flux が当てる組み立ての worker の空きの最低と roots の上限が、宣言の値(Longhorn の Node を持つ機体は宣言の機体と同じ)。"""
    assert sorted(a.node for a in applied) == sorted(share.node for share in declaration.nodes)
    for share in declaration.nodes:
        got = _applied_on(applied, share.node)
        assert (got.min_free_gib, got.roots_gib) == (
            str(share.worker.min_free_gib),
            str(share.worker.roots_gib),
        ), share.node


def test_longhorn_admits_no_more_than_its_share_as_applied(
    declaration: object, margins: object, applied: tuple[Applied, ...]
) -> None:
    """Flux が当てる Longhorn の予約の下で、Longhorn が受ける写しの名目の合計の上限(over-provisioning を掛けた後)が宣言の Longhorn の
    分の内。Node は Flux が消さない印(prune: disabled)を持ち、予約は宣言の disk の名の欄に在る。"""
    for share in declaration.nodes:
        got = _applied_on(applied, share.node)
        assert got.longhorn_disk == share.longhorn_disk, share.node
        assert got.prune == "disabled", share.node
        assert (
            _over_share(share, got.storage_reserved, declaration.over_provisioning_percent, margins)
            <= 0
        ), f"{share.node}: 予約 {got.storage_reserved} の下で Longhorn は分を越えて受ける"


def test_worker_free_share_is_the_line_where_the_worker_returns_to_ok(
    declaration: object, margins: object, doeff_interpreter: Callable[[object], object]
) -> None:
    """worker の分のうち空けておく量(最低 + near の幅 + 戻りの幅)は、near を名乗った worker が ok へ戻る線 — 空きがその量あれば、
    worker は ok を名乗り、1 byte 欠ければ near のまま。幅は worker の env_upkeep の定数から読む。"""
    import hy  # noqa: F401  — Hy の module を import できるようにする
    from doeff_cluster.worker.core.env_upkeep import env_capacity

    for share in declaration.nodes:
        min_free = share.worker.min_free_gib * derive.GIB
        kept = min_free + margins.near_bytes + margins.return_bytes
        assert doeff_interpreter(env_capacity(kept, min_free, "near")) == "ok", share.node
        assert doeff_interpreter(env_capacity(kept - 1, min_free, "near")) == "near", share.node


# --- 失敗ケース(赤になる事の検)---------------------------------------------------------------------------------------------------------


def test_a_declaration_over_the_disk_is_refused(declaration: object, margins: object) -> None:
    """k3s-1 の roots の上限を 40 GiB にした宣言は、分け前の合計が disk を越え(Longhorn の分 -2.0 GB)、導かない(derive.py も書かずに 1 で終わる)。"""
    (k3s1,) = [share for share in declaration.nodes if share.node == "k3s-1"]
    swollen = dataclasses.replace(k3s1, worker=dataclasses.replace(k3s1.worker, roots_gib=40))
    over = dataclasses.replace(
        declaration,
        nodes=tuple(swollen if share.node == "k3s-1" else share for share in declaration.nodes),
    )
    found = derive.faults(over, margins)
    assert len(found) == 1, found
    assert found[0].startswith("k3s-1: 分け前の合計が disk を越える"), found


def test_the_default_reserve_lets_longhorn_take_more_than_its_share(
    declaration: object, margins: object
) -> None:
    """Longhorn の既定の予約(30%)のままの Node は、over-provisioning 200% の下で名目を宣言の分の 4 倍ほど受ける — 当てる値の検
    (test_longhorn_admits_no_more_than_its_share_as_applied)はこの予約を赤にする。"""
    for share in declaration.nodes:
        assert (
            _over_share(
                share, LONGHORN_DEFAULT_RESERVED, declaration.over_provisioning_percent, margins
            )
            > 0
        ), share.node


def test_a_misspelled_key_is_refused() -> None:
    """鍵の綴りの誤り(rootGiB)は、既定の値で埋めずに読む所で断る。"""
    text = derive.SHARES.read_text(encoding="utf-8").replace("rootsGiB:", "rootGiB:")
    with pytest.raises(ValueError, match="知らない鍵"):
        derive.parse(text)
