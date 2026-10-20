"""根の disk を worker と Longhorn が分け合う機体の分け前(shares.yaml)から、worker の env と Longhorn の disk の予約を導く。

分け前の意味・値の出所・守りの外は shares.yaml の頭の註。導く物は 2 つ:
  ../nodes/<機体>/disk-share.yaml  worker の Deployment の env WORKER_ENV_MIN_FREE_GIB・WORKER_ENV_ROOTS_GIB(boot.sh が GiB の整数で読む)
  longhorn-nodes.yaml              Longhorn の Node の disk の予約 storageReserved

使い方(repo の根で):
  uv run python packages/doeff-cluster/deploy/k8s/disk-share/derive.py           導いた file を書く(宣言が釣り合わなければ書かずに 1)
  uv run python packages/doeff-cluster/deploy/k8s/disk-share/derive.py --check   書いてある file が宣言から導いた物と同じかを確かめる(違えば 1)

test(packages/doeff-cluster/tests/test_disk_share.py)がこの module を読み、宣言の釣り合いと、Flux が当てる組み立ての値を確かめる。
"""

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
K8S = HERE.parent
SHARES = HERE / "shares.yaml"
LONGHORN_NODES = HERE / "longhorn-nodes.yaml"
NODE_PATCH = "disk-share.yaml"
GIB = 1 << 30
GB = 10**9
LONGHORN_NAMESPACE = "longhorn-system"
TOP_KEYS = frozenset({"overProvisioningPercent", "shapes", "nodes"})
NODE_KEYS = frozenset(
    {
        "longhornDisk",
        "diskBytes",
        "filesystemReservedBytes",
        "systemBytes",
        "otherResidentsBytes",
        "worker",
    }
)
WORKER_KEYS = frozenset({"minFreeGiB", "rootsGiB", "stateBytes"})


@dataclass(frozen=True)
class WorkerShare:
    """worker の分のうち、宣言が決める 3 つ(near の幅と戻りの幅は worker の code の定数 — Margins)。"""

    min_free_gib: int
    roots_gib: int
    state_bytes: int


@dataclass(frozen=True)
class NodeShare:
    """機体 1 つの分け前(byte)。Longhorn の分は残り — longhorn_bytes。"""

    node: str
    longhorn_disk: str
    disk_bytes: int
    filesystem_reserved_bytes: int
    system_bytes: int
    other_residents_bytes: int
    worker: WorkerShare


@dataclass(frozen=True)
class Declaration:
    over_provisioning_percent: int
    nodes: tuple[NodeShare, ...]


@dataclass(frozen=True)
class Margins:
    """worker が near を名乗る幅と、near から ok へ戻る幅(byte)— 定義元は worker の env_upkeep。"""

    near_bytes: int
    return_bytes: int


def worker_margins() -> Margins:
    """worker の env_upkeep の NEAR-MARGIN-BYTES・NEAR-RETURN-BYTES を読む(写さない — 幅を替えると分け前も替わる)。"""
    import hy  # noqa: F401  — Hy の module を import できるようにする
    from doeff_cluster.worker.core import env_upkeep

    return Margins(
        near_bytes=int(env_upkeep.NEAR_MARGIN_BYTES), return_bytes=int(env_upkeep.NEAR_RETURN_BYTES)
    )


def _keys(value: object, allowed: frozenset[str], where: str) -> Mapping[str, object]:
    """宣言の 1 段の鍵が決まった組と同じかを確かめる — 綴りの誤った鍵を黙って読み飛ばし、欠けを既定で埋めないため。"""
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} が mapping でない: {value!r}")
    unknown = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unknown or missing:
        raise ValueError(f"{where} の鍵が違う — 知らない鍵 {unknown}・欠けた鍵 {missing}")
    return value


def _count(value: object, where: str) -> int:
    """量の欄が 0 以上の整数かを確かめる — 文字や負の値で分け前の和が狂わないため。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} は 0 以上の整数: {value!r}")
    return value


def parse(text: str) -> Declaration:
    """shares.yaml の文を読む。鍵の綴りの誤りや欠けは、黙って既定で埋めずに断る。"""
    top = yaml.safe_load(text)
    if not isinstance(top, Mapping) or set(top) - TOP_KEYS or "nodes" not in top:
        raise ValueError(
            f"shares.yaml の頭の鍵は {sorted(TOP_KEYS)} の内(nodes は必ず): {sorted(top) if isinstance(top, Mapping) else top!r}"
        )
    percent = _count(top.get("overProvisioningPercent"), "overProvisioningPercent")
    if percent == 0:
        raise ValueError("overProvisioningPercent は 1 以上")
    nodes_value = top["nodes"]
    if not isinstance(nodes_value, Mapping) or not nodes_value:
        raise ValueError("nodes は機体の名から分け前への mapping(1 つ以上)")
    return Declaration(
        over_provisioning_percent=percent,
        nodes=tuple(_node_share(str(name), raw) for name, raw in nodes_value.items()),
    )


def _node_share(name: str, raw: object) -> NodeShare:
    """nodes の 1 行を機体の分け前にする(parse の一部 — 機体ごとに鍵と量を確かめる)。"""
    where = f"nodes.{name}"
    entry = _keys(raw, NODE_KEYS, where)
    worker = _keys(entry["worker"], WORKER_KEYS, f"{where}.worker")
    disk = entry["longhornDisk"]
    if not isinstance(disk, str) or not disk:
        raise ValueError(f"{where}.longhornDisk は Longhorn の disk の名: {disk!r}")
    return NodeShare(
        node=name,
        longhorn_disk=disk,
        disk_bytes=_count(entry["diskBytes"], f"{where}.diskBytes"),
        filesystem_reserved_bytes=_count(
            entry["filesystemReservedBytes"], f"{where}.filesystemReservedBytes"
        ),
        system_bytes=_count(entry["systemBytes"], f"{where}.systemBytes"),
        other_residents_bytes=_count(entry["otherResidentsBytes"], f"{where}.otherResidentsBytes"),
        worker=WorkerShare(
            min_free_gib=_count(worker["minFreeGiB"], f"{where}.worker.minFreeGiB"),
            roots_gib=_count(worker["rootsGiB"], f"{where}.worker.rootsGiB"),
            state_bytes=_count(worker["stateBytes"], f"{where}.worker.stateBytes"),
        ),
    )


def load() -> Declaration:
    """この dir の shares.yaml(定義元)を読む — 導く script と test が同じ宣言を読むため。"""
    return parse(SHARES.read_text(encoding="utf-8"))


def worker_bytes(share: NodeShare, margins: Margins) -> int:
    """worker の分 = 空きの最低 + near の幅 + 戻りの幅 + roots の上限 + roots の外の state。空きの最低から上の 3 つは、worker が ok を
    名乗り続けるために空けておく量。"""
    return (
        share.worker.min_free_gib * GIB
        + margins.near_bytes
        + margins.return_bytes
        + share.worker.roots_gib * GIB
        + share.worker.state_bytes
    )


def longhorn_bytes(share: NodeShare, margins: Margins) -> int:
    """Longhorn の分 = disk - filesystem の予約 - OS ほか - 別の系の旧い state - worker の分(負なら disk に収まらない宣言)。"""
    return (
        share.disk_bytes
        - share.filesystem_reserved_bytes
        - share.system_bytes
        - share.other_residents_bytes
        - worker_bytes(share, margins)
    )


def storage_reserved(share: NodeShare, margins: Margins, over_provisioning_percent: int) -> int:
    """Longhorn の disk の予約 = disk - Longhorn の分 × 100 / over-provisioning(切り捨て — 受ける名目の上限が分を越えない向き)。"""
    return share.disk_bytes - longhorn_bytes(share, margins) * 100 // over_provisioning_percent


def longhorn_admits(disk_bytes: int, reserved: int, over_provisioning_percent: int) -> int:
    """予約 reserved の disk に Longhorn が置ける写しの名目の合計の上限 — Longhorn の置き方の判じ(scheduler の IsSchedulableToDisk:
    size + storageScheduled ≤ (storageMaximum - storageReserved) × over-provisioning / 100)と同じ式。"""
    return int(float(disk_bytes - reserved) * float(over_provisioning_percent) / 100)


def faults(declaration: Declaration, margins: Margins) -> tuple[str, ...]:
    """宣言が釣り合わない所(空なら釣り合う)— 導く前と test が、disk に収まらない分け前を値の決まる所で止めるため。"""
    return tuple(
        fault
        for share in declaration.nodes
        for fault in _node_faults(share, margins, declaration.over_provisioning_percent)
    )


def _node_faults(share: NodeShare, margins: Margins, percent: int) -> tuple[str, ...]:
    """機体 1 つの釣り合わない所: Longhorn の分が無い・導いた予約が disk の外・予約の下で Longhorn が分を越えて受ける。"""
    room = longhorn_bytes(share, margins)
    if room <= 0:
        return (
            f"{share.node}: 分け前の合計が disk を越える — Longhorn の分 {room / GB:.2f} GB"
            f"(disk {share.disk_bytes / GB:.2f}・filesystem の予約 {share.filesystem_reserved_bytes / GB:.2f}・"
            f"OS ほか {share.system_bytes / GB:.2f}・別の系 {share.other_residents_bytes / GB:.2f}・"
            f"worker {worker_bytes(share, margins) / GB:.2f} GB)",
        )
    reserved = storage_reserved(share, margins, percent)
    if not 0 <= reserved <= share.disk_bytes:
        return (f"{share.node}: 導いた予約 {reserved} が disk {share.disk_bytes} の外",)
    admits = longhorn_admits(share.disk_bytes, reserved, percent)
    if admits > room:
        return (
            f"{share.node}: 予約 {reserved} の下で Longhorn は名目 {admits} まで受け、分 {room} を越える",
        )
    return ()


@dataclass(frozen=True)
class Derived:
    """導いた file 1 つ(置き場と中身)。"""

    path: Path
    text: str


def _node_patch(share: NodeShare) -> str:
    """機体の worker の Deployment へ、空きの最低と roots の上限の env を足す patch(機体の dir の kustomization が読む)。"""
    return f"""\
# 導いた物 — ../../disk-share/derive.py が ../../disk-share/shares.yaml の {share.node} の分け前から書く(手で直さない)。
# worker の空きの最低と roots の上限(boot.sh が GiB の整数で読む)。分け前の訳は shares.yaml の頭の註。
apiVersion: apps/v1
kind: Deployment
metadata:
  name: doeff-worker
  namespace: agent-worker
spec:
  template:
    spec:
      containers:
        - name: worker
          env:
            - {{ name: WORKER_ENV_MIN_FREE_GIB, value: "{share.worker.min_free_gib}" }}
            - {{ name: WORKER_ENV_ROOTS_GIB, value: "{share.worker.roots_gib}" }}
"""


LONGHORN_NODES_HEAD = """\
# 導いた物 — derive.py が shares.yaml から書く(手で直さない)。Longhorn の Node の disk の予約 storageReserved だけを書く — Flux は
# server-side apply で書いた欄だけを持ち、Node の他の欄(path・tags・allowScheduling ほか)は Longhorn の物のまま。
# prune: disabled — この file から機体を外しても、Flux は Longhorn の Node を消さない(消すと Longhorn がその機体の disk を失う)。
"""


def _longhorn_node(share: NodeShare, margins: Margins, percent: int) -> str:
    """機体 1 つの Longhorn の Node の文書(予約の欄だけ)— 予約の式と、その下で受ける名目の上限を註に残す。"""
    room = longhorn_bytes(share, margins)
    reserved = storage_reserved(share, margins, percent)
    admits = longhorn_admits(share.disk_bytes, reserved, percent)
    return f"""\
# {share.node}: Longhorn の分 {room / GB:.2f} GB → 予約 = {share.disk_bytes / GB:.2f} - {room / GB:.2f} × 100 / {percent} = {reserved / GB:.2f} GB
#   (Longhorn が受ける写しの名目の合計の上限 {admits / GB:.2f} GB)
apiVersion: longhorn.io/v1beta2
kind: Node
metadata:
  name: {share.node}
  namespace: {LONGHORN_NAMESPACE}
  annotations: {{ kustomize.toolkit.fluxcd.io/prune: disabled }}
spec:
  disks:
    {share.longhorn_disk}: {{ storageReserved: {reserved} }}
"""


def render(declaration: Declaration, margins: Margins) -> tuple[Derived, ...]:
    """宣言から導く file の全部 — 書く時と、書いてある file を確かめる時に同じ物を使うため。"""
    percent = declaration.over_provisioning_percent
    nodes_text = (
        LONGHORN_NODES_HEAD
        + "---\n"
        + "---\n".join(_longhorn_node(share, margins, percent) for share in declaration.nodes)
    )
    return (
        *(
            Derived(K8S / "nodes" / share.node / NODE_PATCH, _node_patch(share))
            for share in declaration.nodes
        ),
        Derived(LONGHORN_NODES, nodes_text),
    )


def stale(files: tuple[Derived, ...]) -> tuple[str, ...]:
    """書いてある中身が導いた物と違う file(K8S からの path)— 宣言を直して導き直し忘れた file を見つけるため。"""
    return tuple(
        str(derived.path.relative_to(K8S))
        for derived in files
        if not derived.path.exists() or derived.path.read_text(encoding="utf-8") != derived.text
    )


def main(argv: list[str] | None = None) -> int:
    """入口 — 宣言を読み、釣り合えば導いた file を書く(--check は書かずに違いを数える)。"""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="書かずに、書いてある file が宣言から導いた物と同じかを確かめる",
    )
    args = parser.parse_args(argv)
    declaration = load()
    margins = worker_margins()
    found = faults(declaration, margins)
    if found:
        print("derive: 宣言が釣り合わない — 導かない:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    files = render(declaration, margins)
    if args.check:
        differ = stale(files)
        if differ:
            print(
                f"derive: 宣言から導いた物と違う file: {list(differ)}(derive.py を走らせて書き直す)",
                file=sys.stderr,
            )
            return 1
        return 0
    for derived in files:
        derived.path.write_text(derived.text, encoding="utf-8")
        print(f"derive: {derived.path.relative_to(K8S)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
