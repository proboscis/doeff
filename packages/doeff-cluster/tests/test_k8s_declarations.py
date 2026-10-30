"""k8s の宣言(deploy/k8s)は、機体ごとに静的な worker 1 つと coordinator 1 つを持ち、上に載る系の値を名の固定した ConfigMap で受ける。

利用者の決定(2026-10-09 — doeff worker は頻繁に作り直さず、静的に保つ): worker の Pod の
宣言はこの package が持ち、上に載る系の repo は job と DB の宣言だけを持つ。だから宣言はこの dir に在り、機体ごとの差(置く Node・資源の
上限)だけを機体の dir が足す。上に載る系の値(env・準備の script・Bash の包み)は名の固定した ConfigMap を名で参照するだけで、宣言の文字に
上に載る系の語を書かない(規則 doeff-packages-have-no-application-vocabulary — 語の一覧は .semgrep.yaml の規則 1 つから読み、ここに写さない)。

確かめ方: ``kubectl kustomize`` で dir を組み立てた結果(Flux が当てる物と同じ)を読む。API server には触れない。kubectl が無い機体では
赤にする(飛ばさない — 黙って緑にしない)。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE.parents[1]
K8S = PACKAGE / "deploy" / "k8s"
RULE_ID = "doeff-packages-have-no-application-vocabulary"
NODE_NAME_FIELD = {"fieldRef": {"fieldPath": "spec.nodeName"}}
HOST_SYSTEMD_ROOT = "/host/etc/systemd/system"
TOKEN_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
WORKER_ACCOUNT = "doeff-worker"
NAMESPACE = "agent-worker"

Manifest = dict[str, object]


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{where} が mapping でない: {value!r}")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{where} が list でない: {value!r}")
    return value


def _build(directory: Path) -> list[Manifest]:
    """directory を kubectl kustomize で組み立てた物の全部(Flux がその dir を当てる時と同じ物)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, (
        "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    )
    done = subprocess.run(
        [kubectl, "kustomize", str(directory)], capture_output=True, text=True, timeout=25, check=False
    )
    assert done.returncode == 0, f"kubectl kustomize {directory} が失敗した: {done.stderr.strip()}"
    return [
        _mapping(doc, "宣言の 1 つ") for doc in yaml.safe_load_all(done.stdout) if doc is not None
    ]


@pytest.fixture(scope="module")
def rendered() -> list[Manifest]:
    """deploy/k8s を kubectl kustomize で組み立てた物の全部(1 回だけ組む)。"""
    return _build(K8S)


def _pod_spec(deployment: Manifest) -> dict[str, object]:
    spec = _mapping(deployment["spec"], "Deployment の spec")
    template = _mapping(spec["template"], "Deployment の template")
    return _mapping(template["spec"], "Pod の spec")


def _container(deployment: Manifest) -> dict[str, object]:
    containers = _sequence(_pod_spec(deployment)["containers"], "containers")
    assert len(containers) == 1, f"container は 1 つ: {containers!r}"
    return _mapping(containers[0], "container")


def _env(deployment: Manifest) -> list[dict[str, object]]:
    return [
        _mapping(entry, "env の 1 行")
        for entry in _sequence(_container(deployment).get("env", []), "env")
    ]


def _env_value(deployment: Manifest, name: str) -> dict[str, object]:
    found = [entry for entry in _env(deployment) if entry["name"] == name]
    assert len(found) == 1, f"env {name} がちょうど 1 つ無い: {found!r}"
    return found[0]


def _role(deployment: Manifest) -> str | None:
    for entry in _env(deployment):
        if entry["name"] == "ROLE":
            return str(entry.get("value"))
    return None


def _node(deployment: Manifest) -> str | None:
    selector = _mapping(_pod_spec(deployment).get("nodeSelector", {}), "nodeSelector")
    value = selector.get("kubernetes.io/hostname")
    return None if value is None else str(value)


def _deployments(rendered: list[Manifest], role: str) -> list[Manifest]:
    return [doc for doc in rendered if doc["kind"] == "Deployment" and _role(doc) == role]


def _volume(deployment: Manifest, name: str) -> dict[str, object]:
    found = [
        _mapping(volume, "volume")
        for volume in _sequence(_pod_spec(deployment).get("volumes", []), "volumes")
        if _mapping(volume, "volume")["name"] == name
    ]
    assert len(found) == 1, f"volume {name} がちょうど 1 つ無い: {found!r}"
    return found[0]


def _mount_at(deployment: Manifest, path: str) -> dict[str, object]:
    found = [
        _mapping(mount, "volumeMount")
        for mount in _sequence(_container(deployment).get("volumeMounts", []), "volumeMounts")
        if _mapping(mount, "volumeMount")["mountPath"] == path
    ]
    assert len(found) == 1, f"{path} の mount がちょうど 1 つ無い: {found!r}"
    return found[0]


# 機体ごとの worker を置く Node の全部(会社の機体には置かない)。zeus の worker は上に載る系の仕事も受け、ほかの機体の worker は
# 機体の仕事(能力 host-<Node の名> と host-systemd-readable を要る job)だけを受ける。
NODES = ("atlas", "eos", "k3s-0", "k3s-1", "k3s-2", "k3s-3", "zeus")
HOST_ONLY_NODES = tuple(node for node in NODES if node != "zeus")
HOST_ONLY_PROVIDES = "host-$(NODE_NAME),host-systemd-readable"
PREPARE_DIR = "/opt/worker-prepare"


@dataclass(frozen=True)
class Resources:
    """機体の worker の資源の取り分(requests)と上限(limits)— 機体の大きさ(Node の allocatable)に合わせた値。"""

    requests: dict[str, str]
    limits: dict[str, str]


# 機体の仕事だけを受ける worker の資源。取り分は小さく(機体が他の Pod で埋まっていても置ける)、上限は機体の memory の半分ほど
# (最初の起動の Rust の組み立ての山を通す — 越えたら kubelet が取り分を最も越えたこの Pod を先に追い出す)。
HOST_ONLY_RESOURCES = {
    # atlas は上に載る系の日次の全体検証の task も受ける(1 本の上限は旧い専用の worker と同じ 16Gi 前後 — 2026-10-10 調整役 cisco-c8 の値)。
    "atlas": Resources(requests={"cpu": "500m", "memory": "1Gi"}, limits={"cpu": "8", "memory": "18Gi"}),
    "eos": Resources(requests={"cpu": "250m", "memory": "512Mi"}, limits={"cpu": "2", "memory": "4Gi"}),
    "k3s-0": Resources(requests={"cpu": "100m", "memory": "256Mi"}, limits={"cpu": "2", "memory": "2Gi"}),
    "k3s-1": Resources(requests={"cpu": "100m", "memory": "256Mi"}, limits={"cpu": "2", "memory": "2Gi"}),
    "k3s-2": Resources(requests={"cpu": "100m", "memory": "256Mi"}, limits={"cpu": "2", "memory": "2Gi"}),
    "k3s-3": Resources(requests={"cpu": "250m", "memory": "512Mi"}, limits={"cpu": "3", "memory": "4Gi"}),
}


# 置ける組の印(枠の file の欄 pools)ごとの、その組に置ける上に載る系の常駐の job の数(card の組の表から数えた — card ki-17043e66bd83・
# 2026-10-27)。枠の file が名乗る組の印の集まりはこの鍵と同じでなければ failing。worker の受ける数は、手書きの capacity でなく memory の予算
# memoryMiB と task の予約 taskReserve から coordinator が数える(card ki-2acb63aec0fd)。
# atlas の task に空けておく 3 は上に載る系の日次の全体検証の task(:needs host-atlas — 同時に 3 本まで)の分。ほかの機体に task は来ない。
POOL_JOBS = {
    "pool-atlas-eos": 6,
    "pool-atlas-k3s-0": 2,
    "pool-atlas-k3s-2": 1,
    "pool-atlas-k3s-3": 4,
    "pool-k3s-1-k3s-2": 1,
}
ATLAS_TASK_RESERVE = 3
# 機体の仕事だけを受ける機体の枠 — 機体の名 → (task に空けておく数, 置ける組の印)。
HOST_ONLY_POOLS = {
    "atlas": (ATLAS_TASK_RESERVE, ("pool-atlas-eos", "pool-atlas-k3s-0", "pool-atlas-k3s-2", "pool-atlas-k3s-3")),
    "eos": (0, ("pool-atlas-eos",)),
    "k3s-0": (0, ("pool-atlas-k3s-0",)),
    "k3s-1": (0, ("pool-k3s-1-k3s-2",)),
    "k3s-2": (0, ("pool-atlas-k3s-2", "pool-k3s-1-k3s-2")),
    "k3s-3": (0, ("pool-atlas-k3s-3",)),
}
# 常駐の job の memory の予算(枠の file の欄 memoryMiB — card ki-17043e66bd83 の定義点)= worker の container の memory の上限 − worker 自身の分
# − task の分。zeus でない機体: worker 自身 256 MiB(worker の process の RSS 126〜132 MiB・2026-10-27 の 4 機体の実測に余裕を足す)・task の分
# = task に空けておく数 × 3072 MiB(atlas の task の 1 本の山は 8 時間で 1.1〜4.0 GiB・3 本の同時は group の上限で 3 本まで)。
# zeus: container の上限 − cgroup の層の worker の memory.min − task の層(jobs)の memory.max(命令の層は常駐の job の子の分なので引かない)。
# 予算は N+1 の判断の規則 3(置ける job の落ち着いた値の和 + 最悪の 1 台の停止で移る job の起動の山の上乗せ ≤ 予算)が読む。
HOST_ONLY_WORKER_SELF_MIB = 256
TASK_SHARE_MIB = 3072
# 枠の定義元(card ki-6828eda880ce): 機体の名 → taskReserve・backgroundRoom・memoryMiB の JSON 1 つ。名の固定した ConfigMap worker-room にして worker の Pod に
# file で置き、worker は heartbeat の前に読み直す(枠の変更で Pod を作り直さない)。env の WORKER_TASK_RESERVE・WORKER_BACKGROUND_ROOM・
# WORKER_MEMORY_MIB は書かない。
ROOM_FILE = K8S / "workers" / "worker-room.json"
ROOM_CONFIGMAP = "worker-room"
ROOM_DIR = "/etc/worker-room"
ROOM_PATH = ROOM_DIR + "/worker-room.json"
# atlas の worker が job の子へ足して渡す env の名: Rust の toolchain の置き場(image の env)。task は Rust の部品を組むので、継がないと
# rustup が toolchain を選べない。道具の cache の置き場は task が機体の事実 WORK_DIR から自分で作る(worker は上に載る系の置き場を知らない)。
ATLAS_TASK_PASS_ENV = ("RUSTUP_HOME",)


def _worker_on(rendered: list[Manifest], node: str) -> Manifest:
    on_node = [doc for doc in _deployments(rendered, "worker") if _node(doc) == node]
    assert len(on_node) == 1, (
        f"{node} の worker の Deployment はちょうど 1 つ: {[d['metadata'] for d in on_node]!r}"
    )
    return on_node[0]


@pytest.fixture(scope="module")
def zeus_worker(rendered: list[Manifest]) -> Manifest:
    return _worker_on(rendered, "zeus")


@pytest.fixture(scope="module", params=NODES)
def worker(rendered: list[Manifest], request: pytest.FixtureRequest) -> Manifest:
    """機体ごとの worker(全部の機体で同じに保つ約束を、機体ごとに確かめる)。"""
    return _worker_on(rendered, str(request.param))


def test_each_node_has_exactly_one_worker_named_after_it(rendered: list[Manifest]) -> None:
    """worker は会社でない 7 つの機体にちょうど 1 つずつ。名は doeff-worker-<Node の名> で、selector は Node の名で互いの Pod を選ばない。"""
    workers = _deployments(rendered, "worker")
    assert sorted(_node(doc) or "" for doc in workers) == sorted(NODES)
    for doc in workers:
        node = _node(doc)
        assert _mapping(doc["metadata"], "metadata")["name"] == f"doeff-worker-{node}"
        spec = _mapping(doc["spec"], "spec")
        assert spec["replicas"] == 1
        expected = "RollingUpdate" if node in ROLLING_NODES else "Recreate"
        assert _mapping(spec["strategy"], "strategy")["type"] == expected
        selector = _mapping(_mapping(spec["selector"], "selector")["matchLabels"], "matchLabels")
        assert selector.get("doeff.dev/node") == node


def test_zeus_worker_is_one_deployment_with_100gi_memory_limit(zeus_worker: Manifest) -> None:
    """zeus の worker は 1 つで、資源の上限は memory 100Gi だけ(CPU の上限は置かない — 2026-10-11 調整役 cisco-c8 の頼み)、取り分は
    CPU 4・memory 8Gi。"""
    spec = _mapping(zeus_worker["spec"], "spec")
    assert spec["replicas"] == 1
    resources = _mapping(_container(zeus_worker)["resources"], "resources")
    assert resources["limits"] == {"memory": "100Gi"}
    assert resources["requests"] == {"cpu": "4", "memory": "8Gi"}


# 入れ替えで同じ機体に同じ名の worker の 2 つの世代を並べる機体(AL-D2・card ki-2f5186034976): 新しい Pod を先に起こし、Ready になって
# から旧い Pod を止める。2 つの世代は状態を世代ごとの dir に置き(WORKER_GENERATION_DIRS・POD_UID — boot.sh)、旧い Pod の drain は
# 退いた process が走り切るまで待つ。世代の dir を知らない版の boot.sh は WORKER_DIR_LOCK の錠で旧い Pod の終わりを待ち続け、入れ替えが
# 進まない — だから世代の dir を置く worker の版は GENERATION_DIRS_SINCE かその子孫。
ROLLING_NODES = ("zeus",)
GENERATION_DIRS_SINCE = "036d47c48a9204214149ccd19b1c6b4ea9ecb33d"
# 退いた process が走り切る上限(上に載る系の宣言の retiredSeconds)と、worker が子 process を止める猶予(--stop-grace)。
RETIRED_SECONDS = 21600
STOP_GRACE_SECONDS = 10


def test_zeus_worker_starts_the_new_generation_before_stopping_the_old(zeus_worker: Manifest) -> None:
    """zeus の worker は RollingUpdate(maxSurge 1・maxUnavailable 0)で入れ替わり、状態を Pod の uid の dir に置き、旧い Pod の drain は
    退いた process が走り切る上限まで待ち、猶予はその drain と子を止める猶予を含む。"""
    spec = _mapping(zeus_worker["spec"], "spec")
    assert spec["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    assert _env_value(zeus_worker, "POD_UID")["valueFrom"] == {"fieldRef": {"fieldPath": "metadata.uid"}}
    assert _env_value(zeus_worker, "WORKER_GENERATION_DIRS")["value"] == "1"
    drain = int(str(_env_value(zeus_worker, "DRAIN_DEADLINE")["value"]))
    assert drain >= RETIRED_SECONDS
    grace = _pod_spec(zeus_worker)["terminationGracePeriodSeconds"]
    assert isinstance(grace, int) and grace > drain + STOP_GRACE_SECONDS


@pytest.mark.parametrize("node", NODES)
def test_worker_with_generation_dirs_runs_a_boot_script_that_knows_them(rendered: list[Manifest], node: str) -> None:
    """世代の dir を置く worker の版は、boot.sh が世代の dir を知る版(GENERATION_DIRS_SINCE)かその子孫。置かない worker は Recreate
    (同じ dir の 2 つの世代を並べない)。"""
    worker = _worker_on(rendered, node)
    strategy = _mapping(_mapping(worker["spec"], "spec")["strategy"], "strategy")
    if not any(entry["name"] == "WORKER_GENERATION_DIRS" for entry in _env(worker)):
        assert strategy["type"] == "Recreate"
        return
    version = _worker_version(rendered, node)
    assert _same_or_ancestor(GENERATION_DIRS_SINCE, version), (
        f"{node} の worker の版 {version} は世代の dir を知らない(GENERATION_DIRS_SINCE {GENERATION_DIRS_SINCE} の子孫でない)"
    )


def test_generation_dirs_on_a_version_without_them_is_refused() -> None:
    """世代の dir の前の版(10-11 朝の zeus の版 44778ca69)で世代の dir を置く宣言を、版の判じが断る。"""
    assert not _same_or_ancestor(GENERATION_DIRS_SINCE, "44778ca69e9d101f757721c4b5d283c57f856d80")


# cgroup の memory の層(card ki-cd8cb84fdcd2 — nodes/zeus/cgroup.yaml・boot.sh の「cgroup の層」): zeus の worker の container の cgroup の
# 中に、守る層(memory.min — worker の本体と常駐の job)と限る層(memory.max — task と、job の子が置く命令)を作る。1 本の task や命令が
# 際限なく memory を増やしても、kernel は限る層の中だけで回収して止め、守る層は回収されない(10-24 06:03 JST は 1 つの cgroup で
# 16.7 分止まった)。層を知らない版の boot.sh と worker は宣言を読まず、名(DOEFF_WORKER_JOB_CGROUP)だけを読んで、根に居る worker の
# 兄弟を置けずに起動で止まる — だから層を宣言する worker の版は CGROUP_LAYOUT_SINCE かその子孫。
CGROUP_LAYOUT_SINCE = "2c1ac027a212bc59ceb304683130e7b69becbf0b"
# 層の名: 最初の層(居た process の移り先)と、job の子が自分の子(上に載る系の命令)を置く層と、常駐の job が起こす CLI を置く層。
CGROUP_FIRST_LAYER = "worker"
CGROUP_CHILD_LAYER = "commands"
CGROUP_CLI_LAYER = "clis"
# cgroup の層の CPU の重み(card ki-b40c5c399455 — boot.sh の「cgroup の層」の CPU の重み): 層に cpu.weight・cpu.idle を書ける boot.sh
# の最初の版。知らない版の boot.sh は cpu の file を宣言の誤りとして断り、worker が起動で止まる — だから cpu を書く層を宣言する worker の
# 版は CGROUP_CPU_SINCE かその子孫。
CGROUP_CPU_SINCE = "d5673eb9875c1c8535ebf11c80881016e839ad05"
_BYTE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}


def _bytes(amount: str) -> int:
    """kernel の書式(2G)と k8s の書式(100Gi)の量を byte にする(層の和を container の上限と比べるため)。"""
    match = re.fullmatch(r"(\d+)([KMG]i?)?", amount)
    assert match is not None, f"量 {amount!r} を読めない"
    return int(match.group(1)) * _BYTE_UNITS[match.group(2) or ""]


def _layout(deployment: Manifest) -> list[tuple[str, dict[str, str]]]:
    """WORKER_CGROUP_LAYOUT(空白で並べた「<名>:<file>=<値>,…」)を、層の名と file → 値の並びにする。"""
    entries = [entry.partition(":") for entry in str(_env_value(deployment, "WORKER_CGROUP_LAYOUT")["value"]).split()]
    return [
        (name, {key: value for key, _, value in (setting.partition("=") for setting in settings.split(",") if setting)})
        for name, _, settings in entries
    ]


def test_zeus_worker_guards_its_resident_jobs_and_caps_its_tasks_and_commands(zeus_worker: Manifest) -> None:
    """守る層は worker の本体と常駐の job の 2 つ(memory.min)、限る層は task と命令の 2 つ(memory.max・memory.high は掛けない)。
    守る層の min の和と限る層の max の和は container の memory の上限の内。失敗ケース: 限る層の max の和が上限を越えると、限る層が
    上限まで使った時に container の上限の回収が起き、守る層の min を越えた分と kernel の回収の待ちが守る層にも掛かる。"""
    layout = _layout(zeus_worker)
    settings = dict(layout)
    service = str(_env_value(zeus_worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    jobs = str(_env_value(zeus_worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
    assert layout[0][0] == CGROUP_FIRST_LAYER, layout
    guarded = {name for name, values in layout if "memory.min" in values}
    limited = {name for name, values in layout if "memory.max" in values}
    assert guarded == {CGROUP_FIRST_LAYER, service, CGROUP_CLI_LAYER}, layout
    assert limited == {jobs, CGROUP_CHILD_LAYER}, layout
    assert all("memory.high" not in values for _, values in layout), layout
    limits = _mapping(_mapping(_container(zeus_worker)["resources"], "resources")["limits"], "limits")
    limit = _bytes(str(limits["memory"]))
    total = sum(_bytes(settings[name]["memory.min"]) for name in guarded) + sum(_bytes(settings[name]["memory.max"]) for name in limited)
    assert total <= limit, f"守る層の min と限る層の max の和 {total} が container の上限 {limit} を越える"


def test_zeus_worker_puts_its_resident_layers_before_the_bulk_layers_on_the_cpu(zeus_worker: Manifest) -> None:
    """worker の本体と常駐の job の層の cpu.weight は、task・CLI・命令の層の cpu.weight より高い(card ki-b40c5c399455)。失敗ケース:
    子に cpu を掛けない(重みの無い)層では、CPU が埋まった時に起きた画面の thread が CLI と task の thread と同じ重みで実行待ちに並び、
    ws の ping の往復が 50〜140 ms に跳ねた。cpu.idle は掛けない(命令の層が CPU の埋まった間ずっと止まる)。"""
    settings = dict(_layout(zeus_worker))
    service = str(_env_value(zeus_worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    jobs = str(_env_value(zeus_worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
    weights = {name: int(values["cpu.weight"]) for name, values in settings.items() if "cpu.weight" in values}
    assert set(weights) == set(settings), settings
    for resident in (CGROUP_FIRST_LAYER, service):
        for bulk in (jobs, CGROUP_CHILD_LAYER, CGROUP_CLI_LAYER):
            assert weights[resident] > weights[bulk], weights
    assert all("cpu.idle" not in values for values in settings.values()), settings


@pytest.mark.parametrize("node", NODES)
def test_worker_with_cpu_in_its_cgroup_layout_runs_a_boot_script_that_knows_it(rendered: list[Manifest], node: str) -> None:
    """cpu の file を書く層を宣言する worker の版は、boot.sh が cpu の file を知る版(CGROUP_CPU_SINCE)かその子孫。"""
    worker = _worker_on(rendered, node)
    if not any(entry["name"] == "WORKER_CGROUP_LAYOUT" for entry in _env(worker)):
        return
    if not any(key.startswith("cpu.") for _, values in _layout(worker) for key in values):
        return
    version = _worker_version(rendered, node)
    assert _same_or_ancestor(CGROUP_CPU_SINCE, version), (
        f"{node} の worker の版 {version} は cgroup の層の cpu の file を知らない(CGROUP_CPU_SINCE {CGROUP_CPU_SINCE} の子孫でない)"
    )


def test_cgroup_cpu_layout_on_a_version_without_it_is_refused() -> None:
    """cpu の file の前の版(10-29 の zeus の版 036ea2cb1)で cpu を書く層を宣言する宣言を、版の判じが断る。"""
    assert not _same_or_ancestor(CGROUP_CPU_SINCE, "036ea2cb175a3eb4b20cf25eb61bdd85a740d000")


def test_zeus_worker_cgroup_is_delegated_by_a_root_sidecar_while_the_worker_stays_unprivileged(zeus_worker: Manifest) -> None:
    """委ねる係は root・privileged の native sidecar(restartPolicy Always — worker の container が起こし直されても渡し直す)で、image は
    worker と同じ。worker の container は特権を持たず(privileged は host の cgroup の名前空間で起き、/sys/fs/cgroup の見えが変わる)、
    host の cgroupfs を WORKER_CGROUP_HOST に置く。"""
    sidecars = [
        _mapping(c, "initContainer")
        for c in _sequence(_pod_spec(zeus_worker)["initContainers"], "initContainers")
        if _mapping(c, "initContainer")["name"] == "cgroup-delegate"
    ]
    assert len(sidecars) == 1, sidecars
    sidecar = sidecars[0]
    assert sidecar["restartPolicy"] == "Always"
    assert sidecar["securityContext"] == {"privileged": True, "runAsUser": 0, "runAsNonRoot": False}
    assert sidecar["image"] == _container(zeus_worker)["image"]
    assert "privileged" not in _mapping(_container(zeus_worker).get("securityContext") or {}, "securityContext")
    host = str(_env_value(zeus_worker, "WORKER_CGROUP_HOST")["value"])
    assert _mount_at(zeus_worker, host)["name"] == "host-cgroup"
    assert _volume(zeus_worker, "host-cgroup")["hostPath"] == {"path": "/sys/fs/cgroup", "type": "Directory"}


@pytest.mark.parametrize("node", NODES)
def test_worker_with_a_cgroup_layout_runs_a_boot_script_that_knows_it(rendered: list[Manifest], node: str) -> None:
    """層を宣言する worker の版は、boot.sh と worker が層を知る版(CGROUP_LAYOUT_SINCE)かその子孫。"""
    worker = _worker_on(rendered, node)
    if not any(entry["name"] == "WORKER_CGROUP_LAYOUT" for entry in _env(worker)):
        return
    version = _worker_version(rendered, node)
    assert _same_or_ancestor(CGROUP_LAYOUT_SINCE, version), (
        f"{node} の worker の版 {version} は cgroup の層を知らない(CGROUP_LAYOUT_SINCE {CGROUP_LAYOUT_SINCE} の子孫でない)"
    )


def test_cgroup_layout_on_a_version_without_it_is_refused() -> None:
    """層の前の版(10-24 朝の zeus の版 0e6081c18)で層を宣言する宣言を、版の判じが断る。"""
    assert not _same_or_ancestor(CGROUP_LAYOUT_SINCE, "0e6081c18c0d58f665246c296944d8939d0a2dcd")


# worker の doeff の版の定義元は workers/worker-version.env 1 つ(1 行 1 worker「<名>=<commit>」— card ki-6e83e03d146d)。名の固定した
# ConfigMap worker-version になり、雛形が /etc/worker-version に volume で置く(subPath でない)。版を Pod の template に持たないので、版を
# 替えても Pod は作り直されず、keeper(deploy/boot.sh の頭の註の「版の file と keeper」)が同じ container の中で worker の process を
# 入れ替える。反例: 版を env(以前の機体の dir の version.yaml の JSON patch)に置くと、版を上げるたびに Pod の型が替わって Pod が作り直され、
# 走っている仕事が切れる(10-24〜10-25 の 2 日で版だけの作り直しが 4 回)。
VERSION_FILE = K8S / "workers" / "worker-version.env"
VERSION_CONFIGMAP = "worker-version"
VERSION_DIR = "/etc/worker-version"
VERSION_PATH = VERSION_DIR + "/$(WORKER_NAME)"
# 版の file を読んで keeper として回る起動の script を持つ最初の版(段 A — L2908)。版の file の版はこれかその子孫(keeper を知らない版で
# 起きると、版の file を読まずに worker を 1 つ起こすだけで、版を替えても入れ替わらない)。
KEEPER_SINCE = "07fd279c3f53c66f82178b9e40b9b2c75991f9e2"
# 起動の args と preStop が版の file を読む綴り(`$$(` は k8s の $(名) の展開を避け、shell に `$(` で届く)。
VERSION_READ = 'WORKER_DOEFF_COMMIT="$$(cat "$WORKER_VERSION_FILE")"'
SHA = re.compile(r"^[0-9a-f]{40}$")


def _version_rows() -> dict[str, str]:
    """版の file の行(worker の名 → commit)。# の行と空行は読まない(kustomize の envs と同じ)。"""
    rows: dict[str, str] = {}
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, sep, commit = line.partition("=")
        assert sep == "=", f"版の file の行が <名>=<commit> でない: {line!r}"
        assert name not in rows, f"版の file に {name} の行が 2 つ在る"
        rows[name] = commit
    return rows


def _worker_version(rendered: list[Manifest], name: str) -> str:
    """worker name の宣言の版(組み立てた ConfigMap worker-version の鍵 name の値 — 40 桁)。"""
    configmaps = [doc for doc in rendered if doc.get("kind") == "ConfigMap" and _mapping(doc["metadata"], "metadata")["name"] == VERSION_CONFIGMAP]
    assert len(configmaps) == 1, f"ConfigMap {VERSION_CONFIGMAP} はちょうど 1 つ: {len(configmaps)}"
    data = _mapping(configmaps[0]["data"], "data")
    assert name in data, f"ConfigMap {VERSION_CONFIGMAP} に {name} の版が無い"
    commit = str(data[name])
    assert SHA.match(commit), f"{name} の版が 40 桁の 16 進でない: {commit!r}"
    return commit


def test_no_worker_template_carries_its_version(rendered: list[Manifest]) -> None:
    """雛形と組み立てた全部の worker の Deployment の pod template に WORKER_DOEFF_COMMIT が無い — 版は ConfigMap の file だけが持つ。"""
    template = _build(K8S / "worker")
    deployments = [d for d in template if d["kind"] == "Deployment"] + _deployments(rendered, "worker")
    assert len(deployments) == 1 + len(NODES)
    assert [d["metadata"] for d in deployments if any(e["name"] == "WORKER_DOEFF_COMMIT" for e in _env(d))] == []


def test_no_node_dir_patches_a_version() -> None:
    """機体の dir に版の JSON patch(以前の version.yaml)を置かない・読まない。"""
    for node_dir in sorted((K8S / "nodes").iterdir()):
        assert not (node_dir / "version.yaml").exists(), f"{node_dir.name} の機体の dir に version.yaml が在る"
        assert "version.yaml" not in (node_dir / "kustomization.yaml").read_text(encoding="utf-8"), node_dir.name


@pytest.mark.parametrize("name", NODES)
def test_every_worker_reads_its_version_from_the_fixed_named_configmap_file(rendered: list[Manifest], name: str) -> None:
    """worker の版は、名の固定した ConfigMap worker-version を volume で置いた file(subPath でない)の自分の名の鍵から読む — 起動の args と
    preStop がその版を WORKER_DOEFF_COMMIT に入れて image の起動の script へ渡す。keeper が同じ container に 2 つの版を並べるので、世代ごとの
    状態の dir(WORKER_GENERATION_DIRS・POD_UID)を使う。"""
    worker = _worker_on(rendered, name)
    names = [entry["name"] for entry in _env(worker)]
    assert _env_value(worker, "WORKER_VERSION_FILE")["value"] == VERSION_PATH
    # k8s は $(名) を前の行で定義した env だけから展開する — WORKER_NAME は WORKER_VERSION_FILE より前に要る。
    assert names.index("WORKER_NAME") < names.index("WORKER_VERSION_FILE")
    assert _env_value(worker, "WORKER_GENERATION_DIRS")["value"] == "1"
    assert _env_value(worker, "POD_UID")["valueFrom"] == {"fieldRef": {"fieldPath": "metadata.uid"}}
    mount = _mount_at(worker, VERSION_DIR)
    assert "subPath" not in mount and mount.get("readOnly") is True
    volume = _mapping(_volume(worker, str(mount["name"]))["configMap"], "configMap")
    assert volume["name"] == VERSION_CONFIGMAP
    assert volume.get("optional", False) is False
    container = _container(worker)
    assert VERSION_READ in " ".join(str(arg) for arg in _sequence(container["args"], "args"))
    pre_stop = _mapping(_mapping(_mapping(container["lifecycle"], "lifecycle")["preStop"], "preStop")["exec"], "exec")
    assert VERSION_READ.replace('cat "$WORKER_VERSION_FILE"', 'cat /tmp/doeff-worker-keeper/commit 2>/dev/null || cat "$WORKER_VERSION_FILE"') in " ".join(
        str(word) for word in _sequence(pre_stop["command"], "command")
    )


def test_the_version_file_has_one_row_per_worker_at_a_version_that_knows_the_keeper(rendered: list[Manifest]) -> None:
    """版の file は worker ごとに 1 行(40 桁の commit)を持ち、組み立てた ConfigMap はその写し(名に中身の hash を付けない)。どの版も keeper を
    知る版(KEEPER_SINCE)かその子孫。"""
    rows = _version_rows()
    assert sorted(rows) == sorted(NODES)
    for name, commit in rows.items():
        assert _worker_version(rendered, name) == commit
        assert _same_or_ancestor(KEEPER_SINCE, commit), f"{name} の版 {commit} は keeper を知らない(KEEPER_SINCE の子孫でない)"


def test_worker_names_itself_after_its_node(worker: Manifest) -> None:
    """worker の名と NODE_NAME は k8s の Node の名(fieldRef spec.nodeName)。能力は host-<Node の名> と host-systemd-readable を含む。"""
    assert _env_value(worker, "WORKER_NAME")["valueFrom"] == NODE_NAME_FIELD
    assert _env_value(worker, "NODE_NAME")["valueFrom"] == NODE_NAME_FIELD
    names = [entry["name"] for entry in _env(worker)]
    # k8s は $(名) を、前の行で定義した env だけから展開する — NODE_NAME は WORKER_PROVIDES より前に要る。
    assert names.index("NODE_NAME") < names.index("WORKER_PROVIDES")
    provides = str(_env_value(worker, "WORKER_PROVIDES")["value"]).split(",")
    assert "host-$(NODE_NAME)" in provides
    assert "host-systemd-readable" in provides


def test_worker_reads_host_systemd_units_read_only(worker: Manifest) -> None:
    """機体の /etc/systemd/system を読み取り専用で /host/etc/systemd/system に置き、その path を env で渡す。作業の root は /work。"""
    mount = _mount_at(worker, HOST_SYSTEMD_ROOT)
    assert mount.get("readOnly") is True
    volume = _volume(worker, str(mount["name"]))
    assert _mapping(volume["hostPath"], "hostPath")["path"] == "/etc/systemd/system"
    assert _env_value(worker, "WORKER_HOST_SYSTEMD_ROOT")["value"] == HOST_SYSTEMD_ROOT
    assert _env_value(worker, "WORK_DIR")["value"] == "/work"


def test_worker_passes_machine_facts_to_job_children(worker: Manifest) -> None:
    """機体の事実の 3 つ(Node の名・systemd の root・作業の root)と worker の名は job の子の環境へ渡る(子は worker の env を許可表でしか
    継がない)。worker の名(WORKER_NAME)は、job の子が自分の載る worker を名で知るため — Node の名は同じ機体の別の worker(機体の systemd の
    worker など)と同じ値なので、worker を分けられない。"""
    passed = str(_env_value(worker, "WORKER_PASS_ENV")["value"]).split(",")
    for name in ("NODE_NAME", "WORKER_NAME", "WORKER_HOST_SYSTEMD_ROOT", "WORK_DIR"):
        assert name in passed


def test_worker_runs_as_fixed_account_with_projected_token(
    rendered: list[Manifest], worker: Manifest
) -> None:
    """ServiceAccount は名を固定(doeff-worker)し、投影の token・cluster の CA・namespace を標準の path に置く。"""
    accounts = [
        doc
        for doc in rendered
        if doc["kind"] == "ServiceAccount"
        and _mapping(doc["metadata"], "metadata")["name"] == WORKER_ACCOUNT
    ]
    assert len(accounts) == 1, f"ServiceAccount {WORKER_ACCOUNT} がちょうど 1 つ無い"
    assert _mapping(accounts[0]["metadata"], "metadata")["namespace"] == NAMESPACE
    pod = _pod_spec(worker)
    assert pod["serviceAccountName"] == WORKER_ACCOUNT
    assert pod["automountServiceAccountToken"] is False
    mount = _mount_at(worker, TOKEN_DIR)
    assert mount.get("readOnly") is True
    sources = _sequence(
        _mapping(_volume(worker, str(mount["name"]))["projected"], "projected")["sources"],
        "sources",
    )
    kinds = {
        key: _mapping(source, "source")[key]
        for source in sources
        for key in _mapping(source, "source")
    }
    assert _mapping(kinds["serviceAccountToken"], "serviceAccountToken")["path"] == "token"
    assert _mapping(kinds["configMap"], "configMap")["name"] == "kube-root-ca.crt"
    assert _mapping(kinds["downwardAPI"], "downwardAPI")["items"] == [
        {"path": "namespace", "fieldRef": {"fieldPath": "metadata.namespace"}}
    ]


def test_upper_system_values_come_from_fixed_named_configmaps(
    rendered: list[Manifest], zeus_worker: Manifest
) -> None:
    """上に載る系の値は、名の固定した ConfigMap(env は envFrom・準備の script と Bash の包みは volume)を名で参照して受ける。"""
    env_from = _sequence(_container(zeus_worker)["envFrom"], "envFrom")
    assert [
        _mapping(_mapping(s, "envFrom")["configMapRef"], "configMapRef")["name"] for s in env_from
    ] == ["worker-env"]
    referenced = {
        str(_mapping(_mapping(volume, "volume")["configMap"], "configMap")["name"])
        for volume in _sequence(_pod_spec(zeus_worker)["volumes"], "volumes")
        if "configMap" in _mapping(volume, "volume")
    }
    assert {"worker-prepare", "worker-shell"} <= referenced
    coordinators = _deployments(rendered, "coordinator")
    assert len(coordinators) == 1
    env_from = _sequence(_container(coordinators[0])["envFrom"], "envFrom")
    assert [
        _mapping(_mapping(s, "envFrom")["configMapRef"], "configMapRef")["name"] for s in env_from
    ] == ["coordinator-env"]


@pytest.mark.parametrize("node", HOST_ONLY_NODES)
def test_host_only_worker_offers_only_machine_capabilities(rendered: list[Manifest], node: str) -> None:
    """zeus でない機体の worker は、機体の能力 2 つだけを名乗り(上に載る系の能力の残り WORKER_PROVIDES_EXTRA を足さない)、枠は枠の file
    の機体の行の task の予約と、置ける組の印(どちらも HOST_ONLY_POOLS)。上に載る系の
    準備の script(worker-prepare)は置かない — それは上に載る系の仕事を受ける worker の準備で、空の機体では通らない。
    反例: worker-env の能力の残りを名乗ると、その能力だけを要る上に載る系の job が memory 4GiB の機体へ置かれ得る。"""
    host_only = _worker_on(rendered, node)
    assert _env_value(host_only, "WORKER_PROVIDES")["value"] == HOST_ONLY_PROVIDES
    row = _mapping(_room_rows()[node], node)
    assert row["taskReserve"] == HOST_ONLY_POOLS[node][0]
    assert tuple(row["pools"]) == HOST_ONLY_POOLS[node][1]
    mounts = [
        _mapping(mount, "volumeMount")["mountPath"]
        for mount in _sequence(_container(host_only).get("volumeMounts", []), "volumeMounts")
    ]
    assert PREPARE_DIR not in mounts
    referenced = {
        str(_mapping(_mapping(volume, "volume")["configMap"], "configMap")["name"])
        for volume in _sequence(_pod_spec(host_only)["volumes"], "volumes")
        if "configMap" in _mapping(volume, "volume")
    }
    assert "worker-prepare" not in referenced
    env_from = _sequence(_container(host_only)["envFrom"], "envFrom")
    assert [
        _mapping(_mapping(s, "envFrom")["configMapRef"], "configMapRef")["name"] for s in env_from
    ] == ["worker-env"]


def test_atlas_worker_passes_the_rust_toolchain_to_tasks(rendered: list[Manifest]) -> None:
    """atlas の worker は、job の子へ渡す env の名(WORKER_PASS_ENV)に Rust の toolchain の置き場を足す — 雛形の名と使い手の残り
    $(WORKER_PASS_ENV_EXTRA) はそのまま(位置も同じ — k8s は前の行で定義した env だけを展開する)。ほかの機体は雛形のまま。
    反例: 足さないと、atlas で Rust の部品を組む task の rustup が toolchain を選べず落ちる。"""
    base = "NODE_NAME,WORKER_NAME,WORKER_HOST_SYSTEMD_ROOT,WORK_DIR,KUBERNETES_SERVICE_HOST,KUBERNETES_SERVICE_PORT,$(WORKER_PASS_ENV_EXTRA)"
    for node in NODES:
        passed = str(_env_value(_worker_on(rendered, node), "WORKER_PASS_ENV")["value"])
        expected = ",".join((base, *ATLAS_TASK_PASS_ENV)) if node == "atlas" else base
        assert passed == expected, f"{node} の WORKER_PASS_ENV: {passed!r}"


@pytest.mark.parametrize("node", HOST_ONLY_NODES)
def test_host_only_worker_resources_fit_its_node(rendered: list[Manifest], node: str) -> None:
    """zeus でない機体の worker の資源は、機体の大きさに合わせた値(HOST_ONLY_RESOURCES)。"""
    resources = _mapping(_container(_worker_on(rendered, node))["resources"], "resources")
    assert resources["requests"] == HOST_ONLY_RESOURCES[node].requests
    assert resources["limits"] == HOST_ONLY_RESOURCES[node].limits


def _room_rows() -> dict[str, object]:
    rows = yaml.safe_load(ROOM_FILE.read_text(encoding="utf-8"))
    return _mapping(rows, str(ROOM_FILE))


def test_every_worker_reads_its_room_from_the_fixed_named_configmap_file(rendered: list[Manifest], worker: Manifest) -> None:
    """worker の枠は、名の固定した ConfigMap worker-room を volume で置いた file(subPath でない)から読む — 中身を変えても Pod の template は
    変わらず、kubelet が file を入れ替え、worker が heartbeat の前に読み直す。env で枠を決めない(env の値を変えると Pod が作り直される)。
    反例: env の WORKER_CAPACITY で枠を決めると、常駐の job を 1 つ足すたびに worker の Pod が作り直され、走っているターンが切れる
    (2026-10-25 の差し戻し lt-HZM4SN5788SY6NB6AQAXWXM7AC)。"""
    names = [entry["name"] for entry in _env(worker)]
    assert "WORKER_CAPACITY" not in names
    assert "WORKER_TASK_RESERVE" not in names
    assert _env_value(worker, "WORKER_ROOM_FILE")["value"] == ROOM_PATH
    mount = _mount_at(worker, ROOM_DIR)
    assert "subPath" not in mount and mount.get("readOnly") is True
    volume = _mapping(_volume(worker, str(mount["name"]))["configMap"], "configMap")
    assert volume["name"] == ROOM_CONFIGMAP
    assert volume.get("optional", False) is False
    rows = _room_rows()
    assert _node(worker) in rows
    configmaps = [doc for doc in rendered if doc.get("kind") == "ConfigMap" and _mapping(doc["metadata"], "metadata")["name"] == ROOM_CONFIGMAP]
    assert len(configmaps) == 1
    assert yaml.safe_load(str(_mapping(configmaps[0]["data"], "data")["worker-room.json"])) == rows


def test_the_room_file_has_one_valid_row_per_node() -> None:
    """枠の file は機体ごとに 1 行を持ち、どの行も taskReserve・backgroundRoom・memoryMiB が 0 以上の整数(worker が名乗れる値 — 外れた行は
    worker が読まず、前の値のまま名乗り続ける・card ki-2acb63aec0fd)。背景の仕事を同時に置ける数 backgroundRoom は zeus が 4(背景の仕事に
    残す枠 3 + 入れ替えの並べた置き先 1 — 手書きの capacity が持っていた分)、他の機体は 0。手書きの capacity の欄は持たない。省ける欄 warmServices は真偽だけ(service を待ちの子から分ける旗 — 真偽でない綴りは worker の
    起動を断る・card ki-67abc0cf71e9)。"""
    rows = _room_rows()
    assert sorted(rows) == sorted(NODES)
    for node, row in rows.items():
        room = _mapping(row, node)
        assert sorted(set(room) - {"warmServices", "pools"}) == ["backgroundRoom", "memoryMiB", "taskReserve"], node
        assert type(room.get("warmServices", False)) is bool, node
        for field in ("taskReserve", "backgroundRoom", "memoryMiB"):
            assert type(room[field]) is int and int(room[field]) >= 0, (node, field)
        assert room["backgroundRoom"] == (4 if node == "zeus" else 0), node


def test_every_pool_in_the_room_file_is_named_by_two_or_more_machines() -> None:
    """枠の file の置ける組の印(欄 pools)は、どれも 2 台以上の機体の行が名乗る。名は pool- で始まり、機体の行は同じ印を 2 度書かない
    (card ki-17043e66bd83)。zeus の行は組を名乗らない(zeus の常駐の job は移さない)。
    反例: 1 台だけが名乗る組は、組の印で宣言した job を 1 台に縛る — その 1 台が止まると job は置き先を失う(2026-10-27 10:38 JST に
    1 台にしか置けない job が約 16 分止まった)。"""
    rows = {node: _mapping(row, node) for node, row in _room_rows().items()}
    pools_of = {
        node: [str(pool) for pool in _sequence(room.get("pools", []), f"{node} の pools")] for node, room in rows.items()
    }
    for node, pools in pools_of.items():
        assert len(pools) == len(set(pools)), node
        assert all(pool.startswith("pool-") for pool in pools), node
    members = {
        pool: [node for node, pools in pools_of.items() if pool in pools]
        for pool in {pool for pools in pools_of.values() for pool in pools}
    }
    assert "pools" not in rows["zeus"]
    assert {pool: nodes for pool, nodes in members.items() if len(nodes) < 2} == {}
    assert sorted(members) == sorted(POOL_JOBS)


def test_the_memory_allowance_is_the_worker_limit_less_the_worker_and_its_tasks(rendered: list[Manifest]) -> None:
    """枠の file の memoryMiB(常駐の job の memory の予算)は、機体の worker の container の memory の上限から worker 自身の分と task の分を
    引いた値(定義点は HOST_ONLY_WORKER_SELF_MIB・TASK_SHARE_MIB と zeus の cgroup の層)。
    反例: 上限を越える予算は、N+1 の判断の規則 3 を通しても、移った job の起動の山で worker の container の上限に当たり、worker の memory の
    守りが job の起動を止め続ける(k3s-1 の例 — 上に載る系の宣言の註)。"""
    rows = _room_rows()
    for node in HOST_ONLY_NODES:
        limit = _bytes(str(_mapping(_container(_worker_on(rendered, node))["resources"], "resources")["limits"]["memory"])) // 2**20
        reserve = HOST_ONLY_POOLS[node][0]
        assert _mapping(rows[node], node)["memoryMiB"] == limit - HOST_ONLY_WORKER_SELF_MIB - reserve * TASK_SHARE_MIB, node
    zeus = _worker_on(rendered, "zeus")
    settings = dict(_layout(zeus))
    jobs = str(_env_value(zeus, "DOEFF_WORKER_JOB_CGROUP")["value"])
    limit = _bytes(str(_mapping(_mapping(_container(zeus)["resources"], "resources")["limits"], "limits")["memory"]))
    allowance = (limit - _bytes(settings[CGROUP_FIRST_LAYER]["memory.min"]) - _bytes(settings[jobs]["memory.max"])) // 2**20
    assert _mapping(rows["zeus"], "zeus")["memoryMiB"] == allowance


def test_zeus_worker_keeps_upper_system_capabilities(zeus_worker: Manifest) -> None:
    """zeus の worker は今のまま: 能力の残り(WORKER_PROVIDES_EXTRA)を足し、受ける数は枠の file から受ける(env で決めない)。"""
    provides = str(_env_value(zeus_worker, "WORKER_PROVIDES")["value"]).split(",")
    assert provides[-1] == "$(WORKER_PROVIDES_EXTRA)"
    names = [entry["name"] for entry in _env(zeus_worker)]
    assert "WORKER_CAPACITY" not in names
    assert "WORKER_TASK_RESERVE" not in names
    assert _mount_at(zeus_worker, PREPARE_DIR).get("readOnly") is True


def test_coordinator_is_one_deployment_on_atlas(rendered: list[Manifest]) -> None:
    """coordinator は 1 つで atlas に置く(状態の file を 2 つが同時に書かないよう Recreate)。"""
    coordinators = _deployments(rendered, "coordinator")
    assert [_node(doc) for doc in coordinators] == ["atlas"]
    spec = _mapping(coordinators[0]["spec"], "spec")
    assert spec["replicas"] == 1
    assert _mapping(spec["strategy"], "strategy")["type"] == "Recreate"


def test_coordinator_pod_is_guaranteed(rendered: list[Manifest]) -> None:
    """coordinator の Pod は Guaranteed(oom_score_adj -997)— atlas の memory が尽きた時に、kernel が worker の job と並べて殺さない
    (card ki-7b934e2538f9・ki-b3c8a5cc7b9a)。Guaranteed は全部の container(init を含む)で cpu と memory の requests = limits が要る —
    1 つでも欠けると Pod 全体が Burstable に落ちる。"""
    (coordinator,) = _deployments(rendered, "coordinator")
    pod = _pod_spec(coordinator)
    containers = [
        _mapping(entry, "container")
        for key in ("initContainers", "containers")
        for entry in _sequence(pod.get(key, []), key)
    ]
    for container in containers:
        resources = _mapping(container["resources"], f"{container['name']} の resources")
        requests = _mapping(resources["requests"], f"{container['name']} の requests")
        limits = _mapping(resources["limits"], f"{container['name']} の limits")
        assert set(limits) == {"cpu", "memory"}, f"{container['name']} の limits に cpu と memory が要る: {limits!r}"
        assert requests == limits, f"{container['name']} の requests が limits と違う: {requests!r} / {limits!r}"


def _prepare_jobs(rendered: list[Manifest]) -> list[Manifest]:
    return [doc for doc in rendered if doc["kind"] == "Job" and _role(doc) == "prepare"]


@pytest.fixture(scope="module")
def prepared() -> list[Manifest]:
    """coordinator-prepare/ を単独で組み立てた物(Flux がその dir を当てる時と同じ物 — 根の deploy/k8s には並べない)。"""
    return _build(K8S / "coordinator-prepare")


def test_coordinator_root_is_prepared_by_one_job_beside_it(rendered: list[Manifest], prepared: list[Manifest]) -> None:
    """coordinator の上げ先の root を組む Job は 1 つで、coordinator と同じ node・同じ hostPath を同じ /work に mount し、記録の
    /work/coord は mount せず、資格を持たない(ADR-DOE-CLUSTER-003 R1)。"""
    jobs = _prepare_jobs(prepared)
    assert len(jobs) == 1, f"上げ先の root を組む Job はちょうど 1 つ: {[d['metadata'] for d in jobs]!r}"
    (job,) = jobs
    (coordinator,) = _deployments(rendered, "coordinator")
    assert _node(job) == _node(coordinator) == "atlas"
    assert _mapping(_volume(job, "work")["hostPath"], "hostPath")["path"] == _mapping(
        _volume(coordinator, "work")["hostPath"], "hostPath"
    )["path"]
    mounts = [_mapping(m, "volumeMount")["mountPath"] for m in _sequence(_container(job)["volumeMounts"], "volumeMounts")]
    assert mounts == ["/work"], f"組む Job は /work だけを mount する(記録の /work/coord は mount しない): {mounts!r}"
    assert _env_value(job, "WORK_DIR")["value"] == _env_value(coordinator, "WORK_DIR")["value"] == "/work"
    pod = _pod_spec(job)
    assert pod["automountServiceAccountToken"] is False
    assert pod["restartPolicy"] == "Never"
    assert "envFrom" not in _container(job), "組む Job は上に載る系の ConfigMap を読まない"


def test_coordinator_prepare_job_is_not_removed_by_ttl(prepared: list[Manifest]) -> None:
    """組む Job は ttlSecondsAfterFinished を持たない — TTL が終わった Job を消すと、Flux が次の照合で作り直して組み直しの Pod を
    起こし続ける。古い版の Job は名の版が変わって宣言から外れた時に Flux の prune が消す(ADR-DOE-CLUSTER-003 R2)。"""
    (job,) = _prepare_jobs(prepared)
    assert "ttlSecondsAfterFinished" not in _mapping(job["spec"], "Job の spec")


def test_coordinator_prepare_builds_alone_with_only_the_job(prepared: list[Manifest]) -> None:
    """組む Job の dir は、その dir だけで組み立てられ、Job 1 つだけを出す — 版を写すために読む ../coordinator の物は出力から外れ、
    coordinator の物は coordinator/ の Kustomization だけが持つ。Flux は coordinator/ と別の Kustomization で当て、coordinator/ の
    Kustomization がその終わりを待つ(ADR-DOE-CLUSTER-003 R2)。"""
    assert [(doc["kind"], _role(doc)) for doc in prepared] == [("Job", "prepare")]


# coordinator の版の行だけを変えた版上げの見本の版(doeff の歴史の commit — 中身は問わず、今の宣言の版と違えばよい)。
BUMPED_COMMIT = "3fcba1939bd7e731dc47811aa4c305fe40bdeed7"


def test_prepare_job_follows_a_bump_of_the_coordinator_line_alone(tmp_path: Path) -> None:
    """coordinator.yaml の版の行だけを変えた版上げでも、組む Job の env と名は上げ先の版になる — Job の版は組み立ての時に coordinator の
    版の行から写し、版の定義元は coordinator.yaml の 1 行だけ(ADR-DOE-CLUSTER-003 R3)。着地の列は doeff の検を走らせないので、2 つの
    file の版を揃える事を検に頼まない(10-12 18:37 の a53f417cc は coordinator の版だけを上げ、Job は前の版のまま Ready になって、
    coordinator は上げ先の root を Pod の中で組んで入れ替わった)。"""
    copied = tmp_path / "k8s"
    for part in ("coordinator", "coordinator-prepare"):
        shutil.copytree(K8S / part, copied / part)
    declaration = copied / "coordinator" / "coordinator.yaml"
    (coordinator,) = _deployments(_build(K8S / "coordinator"), "coordinator")
    current = str(_env_value(coordinator, "WORKER_DOEFF_COMMIT")["value"])
    assert current != BUMPED_COMMIT
    text = declaration.read_text(encoding="utf-8")
    assert text.count(current) == 1, f"coordinator.yaml の版 {current} の行がちょうど 1 つでない"
    declaration.write_text(text.replace(current, BUMPED_COMMIT), encoding="utf-8")
    (job,) = _prepare_jobs(_build(copied / "coordinator-prepare"))
    assert _env_value(job, "WORKER_DOEFF_COMMIT")["value"] == BUMPED_COMMIT
    assert _mapping(job["metadata"], "metadata")["name"] == f"coordinator-prepare-{BUMPED_COMMIT}"


# 版の向き(card ki-76a269c31615): どの機体の worker の版も、coordinator の版と同じか、その祖先(古い版)でなければならない。
# coordinator は古い worker を受けられるが、古い coordinator は新しい worker を受けられるとは限らない — 10-11 02:21〜02:37 に zeus の
# worker を 44778ca69 へ上げた宣言(ad90f71c5)が coordinator の e247ea66d より先に main へ入り、新しい worker が登録できずに zeus が
# 約 16 分止まった。worker を先に要る変更でも、coordinator の版を先に(同じ版か、より新しい版へ)上げてから worker を上げる。


@dataclass(frozen=True)
class NodeVersion:
    """機体 1 つの worker の版(機体の名と、その worker が動かす doeff の commit)。"""

    node: str
    commit: str


def _same_or_ancestor(commit: str, of: str) -> bool:
    """commit が of と同じか of の祖先か — 版の前後を doeff の歴史で判じるため。歴史に無い commit(浅い clone・知らない版)は判じられないので
    赤にする(黙って緑にしない)。"""
    git = shutil.which("git")
    assert git is not None, "git が無い — 版の前後を判じられない(このテストは飛ばさない)"
    done = subprocess.run(
        [git, "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", commit, of],
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert done.returncode in (0, 1), f"{commit} が {of} の祖先かを判じられない(歴史に無い commit か): {done.stderr.strip()}"
    return done.returncode == 0


def _workers_newer_than(coordinator: str, workers: tuple[NodeVersion, ...]) -> tuple[NodeVersion, ...]:
    """coordinator の版より新しい worker の版(coordinator の版と同じでも祖先でもない物)— 宣言の検が断る物を名指すため。"""
    return tuple(worker for worker in workers if not _same_or_ancestor(worker.commit, coordinator))


def test_no_worker_is_newer_than_the_coordinator(rendered: list[Manifest]) -> None:
    """宣言の全部の機体の worker の版は、coordinator の版と同じかその祖先(上の註の版の向き)。"""
    coordinators = _deployments(rendered, "coordinator")
    assert len(coordinators) == 1
    coordinator = str(_env_value(coordinators[0], "WORKER_DOEFF_COMMIT")["value"])
    workers = tuple(
        NodeVersion(node=node, commit=_worker_version(rendered, node))
        for node in NODES
    )
    newer = _workers_newer_than(coordinator, workers)
    assert newer == (), f"coordinator の版 {coordinator} より新しい worker の版(coordinator を先に上げる): {newer!r}"


# 10-11 02:21 の止まりの宣言(ad90f71c5 の時点の main): zeus の worker は 44778ca69・coordinator は e247ea66d(44778ca69 の祖先)。
WORKER_FIRST_OF_10_11 = (NodeVersion(node="zeus", commit="44778ca69e9d101f757721c4b5d283c57f856d80"),)
COORDINATOR_OF_10_11 = "e247ea66dc3dc807423460fa9bf7e52f79669090"


def test_the_worker_first_declaration_of_10_11_is_refused() -> None:
    """10-11 の止まりの形(worker を coordinator より先に上げた宣言)を、版の向きの判じが断る。"""
    assert _workers_newer_than(COORDINATOR_OF_10_11, WORKER_FIRST_OF_10_11) == WORKER_FIRST_OF_10_11


# Program の cache の名の版(card ki-172e63fed4c7・ki-bc7697da6cf0): どの機体の worker の版も、service の Program の cache を
# (宣言の版・sha) で名づける版(PROGRAM_CACHE_BY_VERSION_SINCE)かその子孫でなければならない。それより前の worker は cache を <sha>.json と
# sha だけで名づけ、doeff の pin を上げても Program の sha が変わらない宣言し直しで前の版の file を子へ渡し、子が「版が違うので Program を
# 解かない」で落ち続ける — 10-16・10-17・10-18 の 3 回(card ki-d132c14e6f13・ki-fbefb0cf6abd・ki-bc7697da6cf0)eos(901c200c8)の
# patrol-watch が落ち続け、k3s-0(同じ版)の ideas も 0.7.0 で落ちた(ki-3c1998e1e787)。直しの版の後に機体の worker を古い版のまま残すと
# 穴が残るので、宣言の側で断る。
PROGRAM_CACHE_BY_VERSION_SINCE = "7decfe5f7d23c813504e3260a59da0f7be89e7f1"
WORKER_OF_10_18 = "901c200c8ff693e58c49284395a980532bae760f"


def _workers_caching_by_sha_alone(workers: tuple[NodeVersion, ...]) -> tuple[NodeVersion, ...]:
    """Program の cache を sha だけで名づける版の worker(PROGRAM_CACHE_BY_VERSION_SINCE の子孫でない物)— 宣言の検が断る物を名指すため。"""
    return tuple(worker for worker in workers if not _same_or_ancestor(PROGRAM_CACHE_BY_VERSION_SINCE, worker.commit))


def test_no_worker_caches_programs_by_sha_alone(rendered: list[Manifest]) -> None:
    """宣言の全部の機体の worker の版は、Program の cache を (版・sha) で名づける版かその子孫(上の註)。"""
    workers = tuple(
        NodeVersion(node=node, commit=_worker_version(rendered, node))
        for node in NODES
    )
    stale = _workers_caching_by_sha_alone(workers)
    assert stale == (), (
        f"Program の cache を sha だけで名づける版の worker(PROGRAM_CACHE_BY_VERSION_SINCE {PROGRAM_CACHE_BY_VERSION_SINCE} の子孫へ"
        f"上げる): {stale!r}"
    )


def test_the_eos_worker_of_10_18_is_refused() -> None:
    """10-18 の落ちの形(eos の worker が 901c200c8 — cache を sha だけで名づける版)を、cache の名の版の判じが断る。"""
    eos = (NodeVersion(node="eos", commit=WORKER_OF_10_18),)
    assert _workers_caching_by_sha_alone(eos) == eos


@dataclass(frozen=True)
class Part:
    """上に載る系が別々の時に引き継げるよう、別の dir で組み立てる宣言の組 1 つ(dir の名と、その中の Deployment の役と ServiceAccount)。"""

    directory: str
    role: str
    account: str


PARTS = (
    Part(directory="workers", role="worker", account=WORKER_ACCOUNT),
    Part(directory="coordinator", role="coordinator", account="coordinator"),
)


@pytest.mark.parametrize("part", PARTS, ids=[part.directory for part in PARTS])
def test_each_part_builds_alone_with_only_its_own_role(part: Part) -> None:
    """worker の組と coordinator の組は、それぞれの dir だけで組み立てられ、相手の物を含まない — Flux は片方だけを当てられる。"""
    built = _build(K8S / part.directory)
    roles = {_role(doc) for doc in built if doc["kind"] == "Deployment"}
    accounts = {
        str(_mapping(doc["metadata"], "metadata")["name"]) for doc in built if doc["kind"] == "ServiceAccount"
    }
    assert roles == {part.role}
    assert accounts == {part.account}


def test_namespace_stays_with_the_deploying_side(rendered: list[Manifest]) -> None:
    """Namespace は宣言しない — 配備する側が持つ(ここで持つと、外した時に prune が Namespace ごと Secret を消す)。"""
    assert [doc for doc in rendered if doc["kind"] == "Namespace"] == []


def test_worker_account_is_bound_to_cluster_admin() -> None:
    """worker の組は、worker の ServiceAccount を ClusterRole cluster-admin に結ぶ ClusterRoleBinding をちょうど 1 つ持つ — job の子が
    kubectl で cluster を扱える(利用者 1 人の cluster なので絞らない)。"""
    built = _build(K8S / "workers")
    bindings = [doc for doc in built if doc["kind"] == "ClusterRoleBinding"]
    assert len(bindings) == 1, bindings
    (binding,) = bindings
    assert _mapping(binding["roleRef"], "roleRef") == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "cluster-admin",
    }
    assert _sequence(binding["subjects"], "subjects") == [
        {"kind": "ServiceAccount", "name": WORKER_ACCOUNT, "namespace": NAMESPACE}
    ]


@dataclass(frozen=True)
class Vocabulary:
    """規則 doeff-packages-have-no-application-vocabulary から読んだ 2 つ: 上に載る系の語と、許す綴り。"""

    forbidden: re.Pattern[str]
    allowed: re.Pattern[str]


def _vocabulary() -> Vocabulary:
    """規則の語と許す綴りを読む(定義元は .semgrep.yaml の規則 1 つ)。"""
    config = _mapping(
        yaml.safe_load((REPO_ROOT / ".semgrep.yaml").read_text(encoding="utf-8")), ".semgrep.yaml"
    )
    rules = [_mapping(rule, "規則") for rule in _sequence(config["rules"], "rules")]
    found = [rule for rule in rules if rule["id"] == RULE_ID]
    assert len(found) == 1, f"規則 {RULE_ID} がちょうど 1 つ無い"
    patterns = [_mapping(p, "pattern") for p in _sequence(found[0]["patterns"], "patterns")]
    forbidden = [str(p["pattern-regex"]) for p in patterns if "pattern-regex" in p]
    allowed = [str(p["pattern-not-regex"]) for p in patterns if "pattern-not-regex" in p]
    assert len(forbidden) == 1
    assert len(allowed) == 1
    return Vocabulary(forbidden=re.compile(forbidden[0]), allowed=re.compile(allowed[0]))


def _declaration_files() -> Iterator[Path]:
    yield from sorted(path for path in K8S.rglob("*") if path.is_file())


def test_declarations_name_no_upper_system(rendered: list[Manifest]) -> None:
    """宣言の file の中身と名・組み立てた結果に、上に載る系の語が無い。"""
    vocabulary = _vocabulary()
    texts = {
        str(path.relative_to(K8S)): path.read_text(encoding="utf-8")
        for path in _declaration_files()
    }
    assert texts, f"{K8S} に宣言の file が無い"
    texts["(組み立てた結果)"] = yaml.safe_dump_all(rendered, allow_unicode=True)
    hits = {
        where: sorted(
            {
                m.group(0)
                for m in vocabulary.forbidden.finditer(
                    vocabulary.allowed.sub("", where + "\n" + text)
                )
            }
        )
        for where, text in texts.items()
    }
    assert {where: words for where, words in hits.items() if words} == {}


# zeus の worker の作業の root(volume work — WORK_DIR=/work)は、機体の root の disk でなく USB の SSD の下に置く(2026-10-10 の利用者の
# 決定 — zeus の容量を食う置き場を USB の SSD へ移す。同じ日の 13:39 JST に root の disk の空きが準備の最低
# 25 GiB を切り、zeus へ置く job の新しい版の準備が全部止まった)。hostPath の type は Directory: SSD が外れて mount の点が無い時に、root の
# disk へ黙って dir を作らず Pod の起動で止まる。他の機体の worker は今の /var/lib/agent-worker のまま。
ZEUS_WORK_ROOT = "/mnt/fast_ssd_usb/agent-worker"
DEFAULT_WORK_ROOT = "/var/lib/agent-worker"


def _work_volume(worker: Manifest) -> dict[str, object]:
    template = _mapping(_mapping(worker["spec"], "spec")["template"], "template")
    volumes = _sequence(_mapping(template["spec"], "template.spec")["volumes"], "volumes")
    found = [_mapping(v, "volume") for v in volumes if _mapping(v, "volume").get("name") == "work"]
    assert len(found) == 1, f"volume work が {len(found)} 個"
    return _mapping(found[0]["hostPath"], "volume work の hostPath")


def test_zeus_worker_work_root_is_on_the_usb_ssd(zeus_worker: Manifest) -> None:
    """zeus の worker の作業の root は USB の SSD の下で、mount の点が無ければ起動で止まる。"""
    work = _work_volume(zeus_worker)
    assert work == {"path": ZEUS_WORK_ROOT, "type": "Directory"}


# zeus の worker は、USB の SSD の上で作業の root の外に在る dir 2 つを、作業の root の下の host-usb/ に置く(利用者 2026-10-12 15:51 の答え —
# 上に載る系の job が USB の SSD の 3 つの置き場を日次に刈る。機体の利用者の home の下は置かない)。type は Directory(dir が無ければ新しい Pod が
# 起動で止まり、黙って空の dir を作らない)。
ZEUS_USB_MOUNTS = {
    "/work/host-usb/claude-projects": {"path": "/mnt/fast_ssd_usb/claude-projects", "type": "Directory"},
    "/work/host-usb/service-workers": {"path": "/mnt/fast_ssd_usb/service-workers", "type": "Directory"},
}


def test_zeus_worker_puts_the_two_usb_dirs_under_the_work_root(zeus_worker: Manifest) -> None:
    """zeus の worker は USB の SSD の dir 2 つだけを作業の root の下の host-usb/ に置き、機体の利用者の home の下は置かない。"""
    spec = _mapping(_mapping(_mapping(zeus_worker["spec"], "spec")["template"], "template")["spec"], "template.spec")
    volumes = {
        str(_mapping(v, "volume")["name"]): _mapping(v, "volume").get("hostPath")
        for v in _sequence(spec["volumes"], "volumes")
    }
    worker = [_mapping(c, "container") for c in _sequence(spec["containers"], "containers") if _mapping(c, "container")["name"] == "worker"]
    assert len(worker) == 1
    mounts = {
        str(_mapping(m, "volumeMount")["mountPath"]): volumes.get(str(_mapping(m, "volumeMount")["name"]))
        for m in _sequence(worker[0]["volumeMounts"], "volumeMounts")
        if str(_mapping(m, "volumeMount")["mountPath"]).startswith("/work/host-usb/")
    }
    assert mounts == ZEUS_USB_MOUNTS
    host_paths = [str(_mapping(p, "hostPath")["path"]) for p in volumes.values() if p is not None]
    assert not [p for p in host_paths if p.startswith("/mnt/fast_ssd_usb/kento")], host_paths


def test_other_workers_keep_the_default_work_root(rendered: list[Manifest]) -> None:
    """zeus 以外の機体の worker の作業の root は今のまま(機体ごとの差は機体の dir だけが持つ)。"""
    others = [doc for doc in rendered if doc.get("kind") == "Deployment" and _mapping(doc["metadata"], "metadata").get("name") not in (
        "doeff-worker-zeus", "coordinator")]
    assert others
    for doc in others:
        assert _work_volume(doc)["path"] == DEFAULT_WORK_ROOT, _mapping(doc["metadata"], "metadata")["name"]


# USB の SSD の印(2026-10-10 調整役の答え: fstab は nofail なので、USB が無い起動では mount の点が空の dir のまま進み、type Directory だけでは
# 止まらない)。zeus の init own-work は、作業の root に印の file が無ければ名を挙げて止まり、在る時だけ worker の uid へ渡す。
ZEUS_WORK_MARKER = "/work/.on-usb-ssd"


def _init(worker: Manifest, name: str) -> dict[str, object]:
    template = _mapping(_mapping(worker["spec"], "spec")["template"], "template")
    inits = _sequence(_mapping(template["spec"], "template.spec")["initContainers"], "initContainers")
    found = [_mapping(c, "initContainer") for c in inits if _mapping(c, "initContainer").get("name") == name]
    assert len(found) == 1, f"init {name} が {len(found)} 個"
    return found[0]


def test_zeus_worker_stops_at_init_without_the_usb_ssd_marker(zeus_worker: Manifest) -> None:
    """zeus の init own-work は印の file を確かめてから作業の root を worker の uid へ渡す(印が無ければ 0 でない code で止まる)。"""
    command = " ".join(str(part) for part in _sequence(_init(zeus_worker, "own-work")["command"], "command"))
    assert f"test -f {ZEUS_WORK_MARKER}" in command, command
    assert command.index(f"test -f {ZEUS_WORK_MARKER}") < command.index("chown 1000:1000 /work"), command
    assert "exit 1" in command, command
