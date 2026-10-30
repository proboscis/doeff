"""根の disk を worker と Longhorn と container の image が分け合う機体の分け前(shares.yaml)から、worker の env と Longhorn の disk の
予約と kubelet の image GC の線を導く。

分け前の意味・値の出所・守りの外は shares.yaml の頭の註。導く物は 4 つ:
  ../nodes/<機体>/disk-share.yaml        worker の Deployment の env WORKER_ENV_MIN_FREE_GIB・WORKER_ENV_ROOTS_GIB(boot.sh が GiB の整数で読む)
  ../nodes/<機体>/<機体直の worker>.env  同じ根の disk の機体直の worker(k8s の外 — nativeWorker を宣言した機体だけ)の同じ 2 つの env。
                                         機体直の worker の起動 script が `名=値` の行を読み、boot.sh へ渡す
  ../nodes/<機体>/kubelet-image-gc.conf  kubelet の drop-in(imageGCHighThresholdPercent・imageGCLowThresholdPercent — 機体の
                                         /etc/rancher/k3s/kubelet.conf.d/ に置く。Flux は当てない)
  longhorn-nodes.yaml                    Longhorn の Node の disk の予約 storageReserved(Longhorn の disk を宣言した機体だけ)

使い方(repo の根で):
  uv run python packages/doeff-cluster/deploy/k8s/disk-share/derive.py           導いた file を書く(宣言が釣り合わなければ書かずに 1)
  uv run python packages/doeff-cluster/deploy/k8s/disk-share/derive.py --check   書いてある file が宣言から導いた物と同じかを確かめる(違えば 1)
  uv run python packages/doeff-cluster/deploy/k8s/disk-share/derive.py --live    機体の kubelet が今 使う image GC の線(kubectl で configz を
                                                                                 読む)が宣言から導いた線と同じかを確かめる(違えば 1)

test(packages/doeff-cluster/tests/test_disk_share.py)がこの module を読み、宣言の釣り合いと、Flux が当てる組み立ての値を確かめる。
"""

import argparse
import json
import shutil
import subprocess
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
KUBELET_DROPIN = "kubelet-image-gc.conf"
# drop-in を置く機体の dir と名(k3s の config.yaml の kubelet-arg の config-dir — zeus の置き方と同じ dir)。
KUBELET_DROPIN_HOST_PATH = "/etc/rancher/k3s/kubelet.conf.d/60-disk-share-image-gc.conf"
# image GC の high と low の間(使用率の point)— k3s の VPS と zeus が使ってきた 70 / 60 の間。
IMAGE_GC_SPAN_PERCENT = 10
# high の上限(kubelet の既定 85)— disk が大きくても、既定より遅くは消し始めない。
IMAGE_GC_HIGH_MAX_PERCENT = 85
GIB = 1 << 30
GB = 10**9
LONGHORN_NAMESPACE = "longhorn-system"
TOP_KEYS = frozenset({"overProvisioningPercent", "shapes", "nodes"})
# Longhorn の disk の 3 つの欄は、揃って在るか揃って無い(無い機体 = 根の disk の Longhorn の disk を宣言しない機体 — Node を導かない)。
LONGHORN_KEYS = frozenset({"longhornDisk", "longhornScheduling", "longhornDiskTags"})
NODE_KEYS = frozenset(
    {
        "diskBytes",
        "filesystemReservedBytes",
        "systemBytes",
        "otherResidentsBytes",
        "worker",
    }
)
WORKER_KEYS = frozenset({"minFreeGiB", "rootsGiB", "stateBytes"})
# 機体直の worker(nativeWorker — 在る機体だけ)の欄。空きの最低は持たない — 同じ根の disk の空きは 1 つなので、機体の worker の
# minFreeGiB を使う(線を 2 つ置くと、片方だけが near を名乗る帯ができる — card ki-705b0ad4893e)。
NATIVE_KEY = "nativeWorker"
NATIVE_KEYS = frozenset({"name", "rootsGiB", "stateBytes"})
NATIVE_ENV_SUFFIX = ".env"
# 導いた Longhorn の Node が書く欄の全部(Node の spec と disk の 1 つ)。Flux の server-side apply は kubectl の manager が持っていた欄を
# 引き取ってから当て、宣言に無い欄を消す — 人が置き方を替える欄(置くか・退かせるか・札・予約)は、全部を宣言から書く(card ki-c37ae82751bf)。
FLUX_NODE_FIELDS = frozenset({"allowScheduling", "evictionRequested", "tags", "disks"})
FLUX_DISK_FIELDS = frozenset({"allowScheduling", "evictionRequested", "tags", "storageReserved"})


@dataclass(frozen=True)
class WorkerShare:
    """worker の分のうち、宣言が決める 3 つ(near の幅と戻りの幅は worker の code の定数 — Margins)。"""

    min_free_gib: int
    roots_gib: int
    state_bytes: int


@dataclass(frozen=True)
class NativeWorkerShare:
    """機体直の worker(k8s の外で同じ根の disk に作業の dir を持つ worker)の分のうち、宣言が決める 2 つと名(導く env file の名)。"""

    name: str
    roots_gib: int
    state_bytes: int


@dataclass(frozen=True)
class LonghornDisk:
    """根の disk の上の Longhorn の disk の置き方。scheduling が偽の disk は Longhorn を外した機体(写しを受けず、在る写しを退かせる)。"""

    disk: str
    scheduling: bool
    tags: tuple[str, ...]


@dataclass(frozen=True)
class NodeShare:
    """機体 1 つの分け前(byte)。残り(longhorn_bytes)は、longhorn を持つ機体では Longhorn の分、持たない機体では誰の分でもない余り。"""

    node: str
    longhorn: LonghornDisk | None
    disk_bytes: int
    filesystem_reserved_bytes: int
    system_bytes: int
    other_residents_bytes: int
    worker: WorkerShare
    native: NativeWorkerShare | None


@dataclass(frozen=True)
class Declaration:
    over_provisioning_percent: int
    nodes: tuple[NodeShare, ...]


@dataclass(frozen=True)
class ImageGc:
    """kubelet の image GC の線(使用率の %)— 使用が high に達すると、low まで使われていない image を消す。"""

    high_percent: int
    low_percent: int


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
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} が mapping でない: {raw!r}")
    present = LONGHORN_KEYS & set(raw)
    if present and present != LONGHORN_KEYS:
        raise ValueError(
            f"{where} の Longhorn の欄は {sorted(LONGHORN_KEYS)} が揃って在るか揃って無い — 在る欄 {sorted(present)}"
        )
    native_key = frozenset({NATIVE_KEY}) & set(raw)
    entry = _keys(raw, NODE_KEYS | present | native_key, where)
    worker = _keys(entry["worker"], WORKER_KEYS, f"{where}.worker")
    return NodeShare(
        node=name,
        longhorn=_longhorn_disk(entry, where) if present else None,
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
        native=_native_worker(entry[NATIVE_KEY], f"{where}.{NATIVE_KEY}") if native_key else None,
    )


def _native_worker(raw: object, where: str) -> NativeWorkerShare:
    """機体の行の機体直の worker の欄を読む(_node_share の一部)— 名は導く file の名になるので、path を作らない綴りだけを受ける。"""
    entry = _keys(raw, NATIVE_KEYS, where)
    name = entry["name"]
    if not isinstance(name, str) or not name or not all(c.isalnum() or c == "-" for c in name):
        raise ValueError(f"{where}.name は worker の名(英数字と -): {name!r}")
    return NativeWorkerShare(
        name=name,
        roots_gib=_count(entry["rootsGiB"], f"{where}.rootsGiB"),
        state_bytes=_count(entry["stateBytes"], f"{where}.stateBytes"),
    )


def _longhorn_disk(entry: Mapping[str, object], where: str) -> LonghornDisk:
    """機体の行の Longhorn の 3 つの欄を読む(_node_share の一部)。"""
    disk = entry["longhornDisk"]
    if not isinstance(disk, str) or not disk:
        raise ValueError(f"{where}.longhornDisk は Longhorn の disk の名: {disk!r}")
    scheduling = entry["longhornScheduling"]
    if not isinstance(scheduling, bool):
        raise ValueError(f"{where}.longhornScheduling は true か false: {scheduling!r}")
    tags = entry["longhornDiskTags"]
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
        raise ValueError(f"{where}.longhornDiskTags は札の名の list: {tags!r}")
    return LonghornDisk(disk=disk, scheduling=scheduling, tags=tuple(tags))


def load() -> Declaration:
    """この dir の shares.yaml(定義元)を読む — 導く script と test が同じ宣言を読むため。"""
    return parse(SHARES.read_text(encoding="utf-8"))


def worker_bytes(share: NodeShare, margins: Margins) -> int:
    """worker の分 = 空きの最低 + near の幅 + 戻りの幅 + roots の上限 + roots の外の state(+ 機体直の worker の roots の上限と state)。
    空きの最低から上の 3 つは、worker が ok を名乗り続けるために空けておく量で、同じ disk の worker どうしで 1 つ。"""
    native = share.native
    return (
        share.worker.min_free_gib * GIB
        + margins.near_bytes
        + margins.return_bytes
        + share.worker.roots_gib * GIB
        + share.worker.state_bytes
        + (native.roots_gib * GIB + native.state_bytes if native is not None else 0)
    )


def longhorn_bytes(share: NodeShare, margins: Margins) -> int:
    """残り = disk - filesystem の予約 - OS ほか - 別の系の旧い state - worker の分(負なら disk に収まらない宣言)。Longhorn の disk を
    持つ機体では Longhorn の分。"""
    return (
        share.disk_bytes
        - share.filesystem_reserved_bytes
        - share.system_bytes
        - share.other_residents_bytes
        - worker_bytes(share, margins)
    )


def near_entry_bytes(share: NodeShare, margins: Margins) -> int:
    """worker が near を名乗る線 — 空きがこれを割ると near(空きの最低 + near の幅)。"""
    return share.worker.min_free_gib * GIB + margins.near_bytes


def ok_return_bytes(share: NodeShare, margins: Margins) -> int:
    """near を名乗った worker が ok へ戻る線 — 空きがこれ以上なら ok(空きの最低 + near の幅 + 戻りの幅)。"""
    return near_entry_bytes(share, margins) + margins.return_bytes


def free_at_usage(disk_bytes: int, percent: int) -> int:
    """使用率 percent の時の空き(byte)— kubelet は使用率を (容量 - 空き) / 容量 で読み、空きは filesystem の予約を除いた avail
    (worker の読む空きと同じ)。"""
    return disk_bytes * (100 - percent) // 100


def image_gc(share: NodeShare, margins: Margins) -> ImageGc:
    """kubelet の image GC の線 — high は、その使用率の空きが worker の near の線を割らない最も高い %(上限 IMAGE_GC_HIGH_MAX_PERCENT)、
    low は high - IMAGE_GC_SPAN_PERCENT。kubelet は worker が near を名乗る前に消し始め、ok へ戻る線の上まで消す。"""
    room = share.disk_bytes - near_entry_bytes(share, margins)
    high = min(IMAGE_GC_HIGH_MAX_PERCENT, room * 100 // share.disk_bytes) if room > 0 else 0
    return ImageGc(high_percent=high, low_percent=high - IMAGE_GC_SPAN_PERCENT)


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
    """機体 1 つの釣り合わない所: image GC の線が worker の線の下・残りが無い・導いた予約が disk の外・予約の下で Longhorn が分を越えて受ける。"""
    return _image_gc_faults(share, margins) or _share_faults(share, margins, percent)


def _image_gc_faults(share: NodeShare, margins: Margins) -> tuple[str, ...]:
    """image GC の線が worker の線と合わない所: low が 1 % を割る・high の空きが near の線を割る・low の空きが ok へ戻る線を割る。"""
    gc = image_gc(share, margins)
    if gc.low_percent < 1:
        return (
            f"{share.node}: disk {share.disk_bytes / GB:.2f} GB が worker の near の線 "
            f"{near_entry_bytes(share, margins) / GB:.2f} GB に比べて小さく、image GC の線を置けない(high {gc.high_percent}%)",
        )
    start = free_at_usage(share.disk_bytes, gc.high_percent)
    if start < near_entry_bytes(share, margins):
        return (
            f"{share.node}: image GC は空き {start / GB:.2f} GB で消し始め、worker の near の線 "
            f"{near_entry_bytes(share, margins) / GB:.2f} GB の下",
        )
    end = free_at_usage(share.disk_bytes, gc.low_percent)
    if end < ok_return_bytes(share, margins):
        return (
            f"{share.node}: image GC は空き {end / GB:.2f} GB まで消して止まり、worker が ok へ戻る線 "
            f"{ok_return_bytes(share, margins) / GB:.2f} GB の下",
        )
    return ()


def _share_faults(share: NodeShare, margins: Margins, percent: int) -> tuple[str, ...]:
    """分け前が disk に収まらない所: 残りが負(Longhorn の disk を持つ機体は 0 も)・導いた予約が disk の外・Longhorn が分を越えて受ける。"""
    room = longhorn_bytes(share, margins)
    if room < 0 or (share.longhorn is not None and room == 0):
        return (
            f"{share.node}: 分け前の合計が disk を越える — 残り {room / GB:.2f} GB"
            f"(disk {share.disk_bytes / GB:.2f}・filesystem の予約 {share.filesystem_reserved_bytes / GB:.2f}・"
            f"OS ほか {share.system_bytes / GB:.2f}・別の系 {share.other_residents_bytes / GB:.2f}・"
            f"worker {worker_bytes(share, margins) / GB:.2f} GB)",
        )
    if share.longhorn is None:
        return ()
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


def native_env(share: NodeShare) -> dict[str, str]:
    """機体直の worker の env(boot.sh が GiB の整数で読む 2 つ)— 空きの最低は機体の worker と同じ値(同じ disk の線は 1 つ)。"""
    assert share.native is not None, share.node
    return {
        "WORKER_ENV_MIN_FREE_GIB": str(share.worker.min_free_gib),
        "WORKER_ENV_ROOTS_GIB": str(share.native.roots_gib),
    }


def _native_env_file(share: NodeShare) -> str:
    """機体直の worker の env file(`名=値` の行 — 起動 script が読んで boot.sh へ渡す)。"""
    assert share.native is not None, share.node
    lines = "".join(f"{name}={value}\n" for name, value in native_env(share).items())
    return f"""\
# 導いた物 — ../../disk-share/derive.py が ../../disk-share/shares.yaml の {share.node} の分け前から書く(手で直さない)。
# 機体 {share.node} の上で k8s の外で動く worker {share.native.name} の空きの最低と roots の上限(boot.sh が GiB の整数で読む)。
# 読み手 = 機体直の worker の起動 script(unit の env が この file の path を名指す)。空きの最低は同じ根の disk の Pod の worker と同じ値。
{lines}"""


LONGHORN_NODES_HEAD = """\
# 導いた物 — derive.py が shares.yaml から書く(手で直さない)。Longhorn の Node の置き方の欄(Node と disk の allowScheduling・
# evictionRequested・tags と disk の予約 storageReserved)を全部書く — Flux の server-side apply は kubectl の manager(kubectl-edit・
# kubectl-patch ほか)が持っていた欄を引き取ってから当て、ここに無い欄を消す。手で kubectl から替えた置き方は次の当て直しで戻るので、
# 替える時は shares.yaml を直す。path・diskType ほか Longhorn が自分で書く欄は Longhorn の物のまま。
# prune: disabled — この file から機体を外しても、Flux は Longhorn の Node を消さない(消すと Longhorn がその機体の disk を失う)。
"""


def _kubelet_dropin(share: NodeShare, margins: Margins) -> str:
    """機体の kubelet の image GC の線の drop-in — 線の空きと、比べた worker の線を註に残す。"""
    gc = image_gc(share, margins)
    disk = share.disk_bytes
    return f"""\
# 導いた物 — ../../disk-share/derive.py が ../../disk-share/shares.yaml の {share.node} の分け前から書く(手で直さない)。
# 置き先 = 機体の {KUBELET_DROPIN_HOST_PATH}(k3s の config.yaml の kubelet-arg に
# config-dir=/etc/rancher/k3s/kubelet.conf.d を置き、image-gc-high-threshold・image-gc-low-threshold の flag は置かない — flag は
# drop-in より強い)。効く時 = k3s を起動し直して kubelet が読んだ後。効いているかは derive.py --live が kubelet の configz と比べる。
# high {gc.high_percent}% = 空き {free_at_usage(disk, gc.high_percent) / GB:.2f} GB で消し始める — worker が near を名乗る線
#   {near_entry_bytes(share, margins) / GB:.2f} GB(空きの最低 {share.worker.min_free_gib} GiB + near の幅)より上
# low {gc.low_percent}% = 空き {free_at_usage(disk, gc.low_percent) / GB:.2f} GB まで消す — worker が ok へ戻る線 {ok_return_bytes(share, margins) / GB:.2f} GB より上
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
imageGCHighThresholdPercent: {gc.high_percent}
imageGCLowThresholdPercent: {gc.low_percent}
"""


def _longhorn_node(share: NodeShare, longhorn: LonghornDisk, margins: Margins, percent: int) -> str:
    """機体 1 つの Longhorn の Node の文書(置き方の欄の全部 — FLUX_NODE_FIELDS・FLUX_DISK_FIELDS)— 予約の式と、その下で受ける名目の
    上限を註に残す。"""
    room = longhorn_bytes(share, margins)
    reserved = storage_reserved(share, margins, percent)
    admits = longhorn_admits(share.disk_bytes, reserved, percent)
    schedule = "true" if longhorn.scheduling else "false"
    evict = "false" if longhorn.scheduling else "true"
    role = (
        "写しを受ける"
        if longhorn.scheduling
        else "Longhorn を外した機体 — 写しを受けず、在る写しを退かせる"
    )
    return f"""\
# {share.node}: Longhorn の分 {room / GB:.2f} GB → 予約 = {share.disk_bytes / GB:.2f} - {room / GB:.2f} × 100 / {percent} = {reserved / GB:.2f} GB
#   (Longhorn が受ける写しの名目の合計の上限 {admits / GB:.2f} GB)。{role}
apiVersion: longhorn.io/v1beta2
kind: Node
metadata:
  name: {share.node}
  namespace: {LONGHORN_NAMESPACE}
  annotations: {{ kustomize.toolkit.fluxcd.io/prune: disabled }}
spec:
  allowScheduling: {schedule}
  evictionRequested: {evict}
  tags: []
  disks:
    {longhorn.disk}:
      allowScheduling: {schedule}
      evictionRequested: {evict}
      tags: {json.dumps(list(longhorn.tags))}
      storageReserved: {reserved}
"""


def render(declaration: Declaration, margins: Margins) -> tuple[Derived, ...]:
    """宣言から導く file の全部 — 書く時と、書いてある file を確かめる時に同じ物を使うため。"""
    percent = declaration.over_provisioning_percent
    nodes_text = (
        LONGHORN_NODES_HEAD
        + "---\n"
        + "---\n".join(
            _longhorn_node(share, share.longhorn, margins, percent)
            for share in declaration.nodes
            if share.longhorn is not None
        )
    )
    return (
        *(
            Derived(K8S / "nodes" / share.node / NODE_PATCH, _node_patch(share))
            for share in declaration.nodes
        ),
        *(
            Derived(K8S / "nodes" / share.node / KUBELET_DROPIN, _kubelet_dropin(share, margins))
            for share in declaration.nodes
        ),
        *(
            Derived(
                K8S / "nodes" / share.node / (share.native.name + NATIVE_ENV_SUFFIX),
                _native_env_file(share),
            )
            for share in declaration.nodes
            if share.native is not None
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


def live_image_gc(node: str) -> ImageGc:
    """機体の kubelet が今 使う image GC の線 — kubectl で Node の proxy の configz を読む(kubectl が無ければ・読めなければ止まる)。"""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl が無い — kubelet の configz を読めない")
    done = subprocess.run(
        [kubectl, "get", "--raw", f"/api/v1/nodes/{node}/proxy/configz"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"{node} の kubelet の configz を読めない: {done.stderr.strip()}")
    config = json.loads(done.stdout)["kubeletconfig"]
    return ImageGc(
        high_percent=int(config["imageGCHighThresholdPercent"]),
        low_percent=int(config["imageGCLowThresholdPercent"]),
    )


def differ_live(declaration: Declaration, margins: Margins) -> tuple[str, ...]:
    """kubelet が今 使う image GC の線が、宣言から導いた線と違う機体(drop-in を置いていない・k3s を起動し直していない機体)。"""
    found = []
    for share in declaration.nodes:
        derived = image_gc(share, margins)
        live = live_image_gc(share.node)
        if live != derived:
            found.append(
                f"{share.node}: kubelet は high {live.high_percent}% / low {live.low_percent}%・宣言から導いた線は "
                f"high {derived.high_percent}% / low {derived.low_percent}%(drop-in {KUBELET_DROPIN_HOST_PATH} を置き、k3s を起動し直す)"
            )
    return tuple(found)


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
    parser.add_argument(
        "--live",
        action="store_true",
        help="書かずに、機体の kubelet が今 使う image GC の線が宣言から導いた線と同じかを確かめる(kubectl で configz を読む)",
    )
    args = parser.parse_args(argv)
    declaration = load()
    margins = worker_margins()
    found = faults(declaration, margins)
    if found:
        print("derive: 宣言が釣り合わない — 導かない:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    if args.live:
        live = differ_live(declaration, margins)
        if live:
            print(
                "derive: kubelet の image GC の線が宣言と違う機体:\n  " + "\n  ".join(live),
                file=sys.stderr,
            )
            return 1
        return 0
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
