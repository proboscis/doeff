"""k8s の宣言(deploy/k8s)は、機体ごとに静的な worker 1 つと coordinator 1 つを持ち、上に載る系の値を名の固定した ConfigMap で受ける。

決まり: doeff worker は頻繁に作り直さず、静的に保つ。worker の Pod の
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


# 機体ごとの worker を置く Node の全部(会社の機体には置かない)。zeus の worker は上に載る系の仕事も受け、helios と atlas の worker は
# 上に載る系の仕事を余りの空きで受け(GUEST_NODES — ../deploy/k8s/guest-work)、ほかの機体の worker は機体の仕事(能力 host-<Node の名> と
# host-systemd-readable を要る job)だけを受ける。
NODES = ("atlas", "eos", "helios", "k3s-0", "k3s-1", "k3s-2", "k3s-3", "zeus")
OTHER_NODES = tuple(node for node in NODES if node != "zeus")
GUEST_NODES = ("atlas", "helios")
HOST_ONLY_NODES = tuple(node for node in OTHER_NODES if node not in GUEST_NODES)
HOST_ONLY_PROVIDES = "host-$(NODE_NAME),host-systemd-readable"
# 余りの空きで受ける機体の能力: 機体の 2 つの後ろに、上に載る系が worker-env で置く能力の残り(空なら機体の仕事だけを受ける機体のまま)。
GUEST_PROVIDES = "host-$(NODE_NAME),host-systemd-readable,$(WORKER_PROVIDES_GUEST_EXTRA)"
PREPARE_DIR = "/opt/worker-prepare"


@dataclass(frozen=True)
class Resources:
    """機体の worker の資源の取り分(requests)と上限(limits)— 機体の大きさ(Node の allocatable)に合わせた値。"""

    requests: dict[str, str]
    limits: dict[str, str]


# zeus でない機体の worker の資源。memory の取り分は cgroup の層の memory.min の和以上(層の守る分 — 下の
# test_worker_requests_at_least_the_memory_its_layers_guard)、memory の上限は cgroup の層の和
# (worker の min + 常駐の job の予算 + task と準備の層の max(と会話の CLI と命令の層の max)— 越えたら kubelet が取り分を最も越えたこの
# Pod を先に追い出す)。task を受けない機体の上限は、前の上限(機体の memory の半分ほど)に準備の層の分 1536Mi を足した値(予算を前の値の
# まま残す)。
MACHINE_RESOURCES = {
    # atlas は上に載る系の日次の全体検証の task も受ける(1 本の上限は旧い専用の worker と同じ 16Gi 前後 — 2026-10-10 調整役 cisco-c8 の値)。
    # 機体の memory が足りずに kubelet がこの Pod を追い出しているので、層を置いても前の上限 18Gi は上げなかった(予算の側を下げた)。
    # 会話の CLI と命令の限る層(2G + 2G)の分だけを足して 22Gi(予算は変えない)。
    "atlas": Resources(requests={"cpu": "500m", "memory": "512Mi"}, limits={"cpu": "8", "memory": "22Gi"}),
    "eos": Resources(requests={"cpu": "250m", "memory": "3840Mi"}, limits={"cpu": "2", "memory": "5632Mi"}),
    # helios は組の印 pool-helios の常駐の job 24 本(起動の山の和 約 31GiB・落ち着いた値の和 約 16.5GiB)を受ける。上限は前の 40Gi に
    # 会話の CLI と命令の限る層(5G + 3G)の分を足した 48Gi(worker の外の Pod の約 9 GiB と合わせて allocatable 58.2 GiB の内)。
    "helios": Resources(requests={"cpu": "2", "memory": "31Gi"}, limits={"cpu": "12", "memory": "48Gi"}),
    "k3s-0": Resources(requests={"cpu": "100m", "memory": "1792Mi"}, limits={"cpu": "2", "memory": "3584Mi"}),
    "k3s-1": Resources(requests={"cpu": "100m", "memory": "512Mi"}, limits={"cpu": "2", "memory": "3584Mi"}),
    "k3s-2": Resources(requests={"cpu": "100m", "memory": "1792Mi"}, limits={"cpu": "2", "memory": "3584Mi"}),
    "k3s-3": Resources(requests={"cpu": "250m", "memory": "3840Mi"}, limits={"cpu": "3", "memory": "5632Mi"}),
}


# 置ける組の印(枠の file の欄 pools)ごとの、その組に置ける上に載る系の常駐の job の数(2026-10-27 に組の表から数えた:
# pool-atlas-eos 6・pool-atlas-k3s-0 2・pool-atlas-k3s-2 1・pool-atlas-k3s-3 4・pool-k3s-1-k3s-2 1。pool-helios 24 は helios へ移す予定の
# 上に載る系のコアの job の数)。枠の file が名乗る組の印の集まりはこの鍵と同じでなければ failing。worker の受ける job の数は手書きの capacity
# でなく、coordinator が memory の予算 memoryMiB と job の宣言の memory から数える(N+1 の判断 failover-refusals も同じ)。
# atlas の task に空けておく 3 は上に載る系の日次の全体検証の task(:needs host-atlas — 同時に 3 本まで)の分。helios の 1 は上に載る系の
# 画面(webapp)の配りの組む係の 1 回きりの task(webapp の job と同じ :needs pool-helios — 同じ worker の作業の根で束を組む)の分。
# ほかの機体に task は来ない。
POOL_JOBS = {
    "pool-atlas-eos": 6,
    "pool-atlas-k3s-0": 2,
    "pool-atlas-k3s-2": 1,
    "pool-atlas-k3s-3": 4,
    "pool-k3s-1-k3s-2": 1,
    "pool-helios": 24,
}
# 1 台だけが名乗ってよい組の印 → 1 台に縛る訳(訳の無い 1 台の組は赤 — test_every_pool_in_the_room_file_is_named_by_two_or_more_machines)。
SINGLE_MACHINE_POOLS = {
    # 決まり: 上に載る系のコアの job は helios の worker で動かす。2 台目を名乗らせる時はここから消す。
    "pool-helios": "上に載る系のコアの job を helios の 1 台で動かす",
}
ATLAS_TASK_RESERVE = 3
HELIOS_TASK_RESERVE = 1
# zeus でない機体の枠 — 機体の名 → (task に空けておく数, 置ける組の印)。受ける job の数は手書きせず、coordinator が
# memory の予算 memoryMiB と job の宣言の memory から数える。
MACHINE_POOLS = {
    "atlas": (ATLAS_TASK_RESERVE, ("pool-atlas-eos", "pool-atlas-k3s-0", "pool-atlas-k3s-2", "pool-atlas-k3s-3")),
    "eos": (0, ("pool-atlas-eos",)),
    "helios": (HELIOS_TASK_RESERVE, ("pool-helios",)),
    "k3s-0": (0, ("pool-atlas-k3s-0",)),
    "k3s-1": (0, ("pool-k3s-1-k3s-2",)),
    "k3s-2": (0, ("pool-atlas-k3s-2", "pool-k3s-1-k3s-2")),
    "k3s-3": (0, ("pool-atlas-k3s-3",)),
}
# 常駐の job の memory の予算(枠の file の欄 memoryMiB)= worker の container の memory の上限 − cgroup の層の worker の memory.min − task と
# 準備の層(jobs と、宣言する機体の prepare)の memory.max(全部の機体 — zeus の命令の層は常駐の job の子の分なので引かない)。会話の CLI の
# 層を限る層に置く機体(GUEST_NODES)は、会話の CLI と命令の層の memory.max も引く(会話は常駐の job の予算の外の余りだけを使う)。
# 予算は N+1 の判断の規則 3(置ける job の落ち着いた値の和 + 最悪の 1 台の停止で移る job の起動の山の上乗せ ≤ 予算)が読む。
# 層の値の下限(nodes/<Node の名>/cgroup.yaml の値の決め方は ../deploy/k8s/cgroup-layers): worker の memory.min ≥ worker 自身の分 256 MiB
# (worker の process の RSS 126〜132 MiB・2026-10-27 の 4 機体の実測に余裕を足す)。jobs の memory.max ≥ 準備の分 1536 MiB + task に空けて
# おく数 × task の分 3072 MiB(atlas の task の 1 本の山は 8 時間で 1.1〜4.0 GiB・3 本の同時は group の上限で 3 本まで)。準備の分は、
# container の上限 2Gi の機体(k3s-0〜2)で実行環境と木の準備が通っていた余地(2Gi − worker の本体と常駐の job の約 0.4 GiB)。
WORKER_SELF_MIB = 256
PREPARE_SHARE_MIB = 1536
TASK_SHARE_MIB = 3072
# 手書きの枠 capacity の欄は持たない(job の側は memoryMiB と job の宣言の memory、task の側は taskReserve と backgroundRoom で数える)。
# 枠の定義元: 機体の名 → taskReserve・backgroundRoom・memoryMiB の JSON 1 つ。名の固定した ConfigMap worker-room にして worker の Pod に
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
    """worker は会社でない 8 つの機体にちょうど 1 つずつ。名は doeff-worker-<Node の名> で、selector は Node の名で互いの Pod を選ばない。"""
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


def test_zeus_worker_is_one_deployment_with_116gi_memory_limit(zeus_worker: Manifest) -> None:
    """zeus の worker は 1 つで、資源の上限は memory 116Gi だけ(CPU の上限は置かない — 2026-10-11 調整役 cisco-c8 の頼み)、取り分は
    CPU 4・memory 26Gi(cgroup の層の memory.min の和)。"""
    spec = _mapping(zeus_worker["spec"], "spec")
    assert spec["replicas"] == 1
    resources = _mapping(_container(zeus_worker)["resources"], "resources")
    assert resources["limits"] == {"memory": "116Gi"}
    assert resources["requests"] == {"cpu": "4", "memory": "26Gi"}


# 入れ替えで同じ機体に同じ名の worker の 2 つの世代を並べる機体(AL-D2): 新しい Pod を先に起こし、Ready になって
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


# cgroup の memory の層(nodes/<Node の名>/cgroup.yaml・boot.sh の「cgroup の層」): 層を宣言する機体(CGROUP_LAYERS)の worker の
# container の cgroup の中に、守る層(memory.min — worker の本体と常駐の job)と限る層(memory.max — task と、job の子が置く命令)を作る。1 本の task や命令が
# 際限なく memory を増やしても、kernel は限る層の中だけで回収して止め、守る層は回収されない(10-24 06:03 JST は 1 つの cgroup で
# 16.7 分止まった)。層を知らない版の boot.sh と worker は宣言を読まず、名(DOEFF_WORKER_JOB_CGROUP)だけを読んで、根に居る worker の
# 兄弟を置けずに起動で止まる — だから層を宣言する worker の版は CGROUP_LAYOUT_SINCE かその子孫。
CGROUP_LAYOUT_SINCE = "2c1ac027a212bc59ceb304683130e7b69becbf0b"
# 層の名: 最初の層(居た process の移り先)と、job の子が自分の子(上に載る系の命令)を置く層と、常駐の job が起こす CLI を置く層。
CGROUP_FIRST_LAYER = "worker"
CGROUP_CHILD_LAYER = "commands"
CGROUP_CLI_LAYER = "clis"
# keeper の版の準備の層(空きの小さい機体だけ — ../deploy/k8s/cgroup-layers の頭の註)と、それを知る worker と boot.sh の最初の版。
# 知らない版の keeper は準備を jobs の層で起こし、知らない版の worker は準備の層を数えない — 準備の層を宣言する worker の版はこれかその子孫。
CGROUP_PREPARE_LAYER = "prepare"
PREPARE_LAYER_SINCE = "fffb250ee6a7b3e242a8b701349ada31164bd8d8"
# 準備の山(11-02 に手元で測った — doeff 6e1996649 の root を冷えた bytecode の保存先で準備し、焼きを 2 並列で回した process の木の anon の
# 最大 453 MiB)。準備の層を宣言する機体の CPU の上限は 2(焼きの並列数)で、層の上限はこれ以上。
PREPARE_PEAK_MIB = 453
# 準備の層を宣言する機体の平常の追い出しまでの空き(11-02 の 6 時間の worker の log の 5 分ごとの最小の中央 — k3s-1 は 1,067 MiB・k3s-0 は
# 1,241 MiB・k3s-2 は 1,728 MiB)のうち小さい方。余裕 + 準備の層の上限がこれ以下なら、keeper は平常の空きで準備を起こす。
SMALL_NODE_ROOM_MIB = 1024
# 上に載る系の配備の宣言の CLI を置く層(zeus だけ)。遅れに敏感な 1 本の process なので、CPU の重みは常駐の層と同じまで上げてよい
# (常駐の層より上には置かない)・ほかの一括の仕事の層より上。
CGROUP_DEPLOY_LAYER = "deploy"
# 層を宣言する機体(全部の機体)→ 最初の層と常駐の job の層(DOEFF_WORKER_SERVICE_CGROUP)のほかに守る層・task の層
# (DOEFF_WORKER_JOB_CGROUP)のほかに限る層。zeus の会話の CLI の層は守る層(会話を受ける主の機体)。上に載る系の仕事を余りの空きで受ける
# 機体(GUEST_NODES)は会話の CLI と命令の層を両方とも限る層に置き、常駐の job の守りと予算の外の余りだけを使わせる。ほかの機体は CLI も
# 命令の層も使わない(CLI と命令を起こす上に載る系の常駐の job が置かれない)。zeus でない機体の委ねる係と層の名は部品
# ../deploy/k8s/cgroup-layers 1 つ。
CGROUP_LAYERS = {
    "zeus": ({CGROUP_CLI_LAYER}, {CGROUP_CHILD_LAYER, CGROUP_DEPLOY_LAYER}),
    "atlas": (set(), {CGROUP_CLI_LAYER, CGROUP_CHILD_LAYER}),
    "eos": (set(), set()),
    "helios": (set(), {CGROUP_CLI_LAYER, CGROUP_CHILD_LAYER}),
    "k3s-0": (set(), {CGROUP_PREPARE_LAYER}),
    "k3s-1": (set(), {CGROUP_PREPARE_LAYER}),
    "k3s-2": (set(), {CGROUP_PREPARE_LAYER}),
    "k3s-3": (set(), set()),
}
# helios の常駐の job の層の memory.min の下限: helios へ移す常駐の job 29 本の memory の峰の見積もり 約 28 GiB(2026-11-01 の上に載る系の
# 調べ — zeus の services の層の 54.6 時間の峰 35.3 GiB × 移す物の割合 79%)。
HELIOS_SERVICE_PEAK = "28G"
# zeus の守る層の memory.min の下限(11-03 13:2x JST — 上に載る系のコアの常駐の job が helios へ移った後の zeus の worker で測った使い):
# 常駐の job の層 = zeus に残る 10 本の宣言の settle の和 2572 MiB(CLI を起こす job の本体を除く)+ その本体の RSS 929 MiB。会話の CLI の
# 層 = 層の process の RSS の和 14827 MiB。
ZEUS_SERVICE_USE = "3501M"
ZEUS_CLI_USE = "14827M"
# cgroup の層の CPU の重み(boot.sh の「cgroup の層」の CPU の重み): 層に cpu.weight・cpu.idle を書ける boot.sh
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


def test_every_node_declares_cgroup_layers(rendered: list[Manifest]) -> None:
    """全部の機体の worker が cgroup の層を宣言し、層の約束を確かめる test(CGROUP_LAYERS)が全部の機体を読む。失敗ケース: 層の無い機体では
    task・実行環境の準備・入口の検めが常駐の job と container の cgroup 1 つを分け合い、1 本が上限の手前で回収を詰まらせると常駐の job も
    止まる。"""
    declared = {node for node in NODES if any(entry["name"] == "WORKER_CGROUP_LAYOUT" for entry in _env(_worker_on(rendered, node)))}
    assert declared == set(CGROUP_LAYERS) == set(NODES)


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_layers_leave_room_for_the_worker_its_preparation_and_its_tasks(rendered: list[Manifest], node: str) -> None:
    """worker の層の memory.min は worker 自身の分(WORKER_SELF_MIB)以上、task と準備の層の memory.max は準備の分と、task に空けておく数
    × task の分の和(PREPARE_SHARE_MIB + taskReserve × TASK_SHARE_MIB)以上。失敗ケース: 限る層の max がこの和を下回ると、実行環境の準備
    (uv・Rust の wheel・焼き)や枠どおりに置いた task が層の上限で殺され、常駐の job の版を上げられず、task が失敗する。"""
    worker = _worker_on(rendered, node)
    settings = dict(_layout(worker))
    jobs = str(_env_value(worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
    reserve = _mapping(_room_rows()[node], node)["taskReserve"]
    assert isinstance(reserve, int)
    assert _bytes(settings[CGROUP_FIRST_LAYER]["memory.min"]) >= WORKER_SELF_MIB * 2**20, settings
    if CGROUP_PREPARE_LAYER not in settings:
        assert _bytes(settings[jobs]["memory.max"]) >= (PREPARE_SHARE_MIB + reserve * TASK_SHARE_MIB) * 2**20, settings
        return
    # 準備の層を宣言する機体: keeper の版の準備は準備の層、実行環境と木の準備(同じ焼きの道具)は jobs の層 — どちらも準備の山以上。
    assert _bytes(settings[CGROUP_PREPARE_LAYER]["memory.max"]) >= PREPARE_PEAK_MIB * 2**20, settings
    assert _bytes(settings[jobs]["memory.max"]) >= (PREPARE_PEAK_MIB + reserve * TASK_SHARE_MIB) * 2**20, settings


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_is_given_what_it_needs_to_hold_its_jobs_layer_under_the_eviction_line(rendered: list[Manifest], node: str) -> None:
    """worker が kubelet の追い出しの線までの空きを数え、jobs の層の memory.max をその空きから掛け直すのに要る物
    (worker/protocol/eviction_line・worker/entry/main の eviction-settings-of)を、層を宣言する全部の機体の worker が受ける: host の
    cgroupfs の mount(WORKER_CGROUP_HOST と同じ path — kubepods と root の cgroup を読む)・jobs の層の名と層の宣言(静的な上限)・
    Node の名(Node の宣言の capacity と allocatable を読む)・Pod の uid(API server を区画の CA で検める口を組む印)。失敗ケース: どれかが
    無い機体の worker は線を数えず、jobs の層は宣言の静的な上限のまま — task の跳ねが kubelet の allocatable の線に先に当たり、worker の
    Pod ごと(常駐の job ごと)追い出される(atlas の 10-30〜11-01 の 5 回)。"""
    worker = _worker_on(rendered, node)
    host = _env_value(worker, "WORKER_CGROUP_HOST")["value"]
    mounts = [_mapping(m, "volumeMount")["mountPath"] for m in _sequence(_container(worker).get("volumeMounts", []), "volumeMounts")]
    assert host in mounts, mounts
    assert _env_value(worker, "DOEFF_WORKER_JOB_CGROUP")["value"], worker
    assert "memory.max=" in str(_env_value(worker, "WORKER_CGROUP_LAYOUT")["value"]), worker
    for name, path in (("NODE_NAME", "spec.nodeName"), ("POD_UID", "metadata.uid")):
        source = _mapping(_mapping(_env_value(worker, name)["valueFrom"], name)["fieldRef"], name)
        assert source["fieldPath"] == path, source


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_guards_its_resident_jobs_and_caps_its_tasks_and_commands(rendered: list[Manifest], node: str) -> None:
    """守る層は worker の本体と常駐の job(と zeus の CLI)の層(memory.min)、限る層は task(と zeus の命令)の層(memory.max・memory.high は
    掛けない)。守る層の min の和と限る層の max の和は container の memory の上限の内。失敗ケース: 限る層の max の和が上限を越えると、限る層が
    上限まで使った時に container の上限の回収が起き、守る層の min を越えた分と kernel の回収の待ちが守る層にも掛かる。"""
    worker = _worker_on(rendered, node)
    layout = _layout(worker)
    settings = dict(layout)
    service = str(_env_value(worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    jobs = str(_env_value(worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
    more_guarded, more_limited = CGROUP_LAYERS[node]
    assert layout[0][0] == CGROUP_FIRST_LAYER, layout
    guarded = {name for name, values in layout if "memory.min" in values}
    limited = {name for name, values in layout if "memory.max" in values}
    assert guarded == {CGROUP_FIRST_LAYER, service, *more_guarded}, layout
    assert limited == {jobs, *more_limited}, layout
    assert all("memory.high" not in values for _, values in layout), layout
    limits = _mapping(_mapping(_container(worker)["resources"], "resources")["limits"], "limits")
    limit = _bytes(str(limits["memory"]))
    total = sum(_bytes(settings[name]["memory.min"]) for name in guarded) + sum(_bytes(settings[name]["memory.max"]) for name in limited)
    assert total <= limit, f"守る層の min と限る層の max の和 {total} が container の上限 {limit} を越える"


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_requests_at_least_the_memory_its_layers_guard(rendered: list[Manifest], node: str) -> None:
    """worker の container の memory の取り分(requests)は、cgroup の層の memory.min の和(worker の本体と常駐の job と zeus の CLI の層)
    以上。失敗ケース: kubelet は Node の memory が allocatable を越えた時、使いが取り分を最も越えた Pod から追い出す。守る層の和より小さい
    取り分の worker は、守る層が守る常駐の job の memory だけで取り分を越え、毎回最初に追い出される(atlas の 10-30〜11-01 の 5 回 —
    取り分 1Gi・守る層の和 7424 MiB)。scheduler も守る分を空きと数え、ほかの Pod を機体に載せすぎる。守る層は、今その worker に
    置かれた常駐の job の分だけにする(組の相手の停止で受ける job の分を前もって守ると、Pod の取り分が Node に入らない — atlas・k3s-1)。"""
    worker = _worker_on(rendered, node)
    guarded = sum(_bytes(values["memory.min"]) for _, values in _layout(worker) if "memory.min" in values)
    requests = _mapping(_mapping(_container(worker)["resources"], "resources")["requests"], "requests")
    request = _bytes(str(requests["memory"]))
    assert request >= guarded, f"{node} の worker の memory の取り分 {request} が守る層の memory.min の和 {guarded} より小さい"


def test_helios_worker_guards_its_resident_jobs_above_their_estimated_peak(rendered: list[Manifest]) -> None:
    """helios の常駐の job の層の memory.min は、helios へ移す常駐の job の memory の峰の見積もり(HELIOS_SERVICE_PEAK)以上。失敗ケース:
    守る値が峰を下回ると、準備の層が上限まで使った時の container の上限の回収が、峰の常駐の job の memory にも掛かる。"""
    helios = _worker_on(rendered, "helios")
    service = str(_env_value(helios, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    assert _bytes(dict(_layout(helios))[service]["memory.min"]) >= _bytes(HELIOS_SERVICE_PEAK)


def test_zeus_worker_guards_its_resident_jobs_and_its_clis_above_their_measured_use(zeus_worker: Manifest) -> None:
    """zeus の常駐の job の層と会話の CLI の層(CGROUP_CLI_LAYER)の memory.min は、それぞれ測った使い(ZEUS_SERVICE_USE・ZEUS_CLI_USE)
    以上。失敗ケース: CLI の層の守る値が今の使いを下回ると、task と命令の層が上限まで使った時の container の上限の回収が、走っている
    会話の CLI の memory に掛かる(常駐の job が helios へ移る前の 11G は、移った後の CLI の RSS の和 14827 MiB より小さい)。"""
    settings = dict(_layout(zeus_worker))
    service = str(_env_value(zeus_worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    assert _bytes(settings[service]["memory.min"]) >= _bytes(ZEUS_SERVICE_USE), settings
    assert _bytes(settings[CGROUP_CLI_LAYER]["memory.min"]) >= _bytes(ZEUS_CLI_USE), settings


@pytest.mark.parametrize("node", GUEST_NODES)
def test_guest_worker_keeps_conversation_clis_and_commands_off_its_resident_jobs_guard(rendered: list[Manifest], node: str) -> None:
    """余りの空きで会話を受ける機体(GUEST_NODES)の会話の CLI の層と命令の層は、memory.max と cpu.weight だけを持つ限る層
    (memory.min を持たない — 守る分を取らない)で、重みは常駐の job の層より低い。worker と常駐の job の層の memory.min と、jobs・clis・
    commands の memory.max の和は container の上限の内で、常駐の job の予算(枠の file の memoryMiB)は会話の 2 つの層の外 — 予算と 2 つの
    層の max と worker の min と jobs の max の和が上限にちょうど等しく、予算は常駐の job の層の memory.min 以上。
    失敗ケース: 層が無いと上に載る系の会話の CLI は常駐の job の層 services に居続け、その memory.min の守りと予算を食う(helios の 30G・
    atlas の 256M)。限る層の和が上限を越えると、会話が上限まで使った時に container の上限の回収が常駐の job の memory にも掛かる。予算に
    会話の分を数えると、coordinator が常駐の job を会話の memory の上へ置き、常駐の job が先という順(helios はコアの計算が先)が崩れる。"""
    worker = _worker_on(rendered, node)
    settings = dict(_layout(worker))
    service = str(_env_value(worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    jobs = str(_env_value(worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
    conversation_layers = (CGROUP_CLI_LAYER, CGROUP_CHILD_LAYER)
    for layer in conversation_layers:
        assert set(settings[layer]) == {"memory.max", "cpu.weight"}, (layer, settings)
        assert int(settings[layer]["cpu.weight"]) < int(settings[service]["cpu.weight"]), settings
    limit = _bytes(str(_mapping(_mapping(_container(worker)["resources"], "resources")["limits"], "limits")["memory"]))
    guarded = _bytes(settings[CGROUP_FIRST_LAYER]["memory.min"]) + _bytes(settings[service]["memory.min"])
    limited = sum(_bytes(settings[name]["memory.max"]) for name in (jobs, *conversation_layers))
    assert guarded + limited <= limit, f"{node}: 守る層 {guarded} + 限る層 {limited} が container の上限 {limit} を越える"
    budget = int(str(_mapping(_room_rows()[node], node)["memoryMiB"])) * 2**20
    outside = _bytes(settings[CGROUP_FIRST_LAYER]["memory.min"]) + limited
    assert budget + outside == limit, f"{node}: 予算 {budget} が会話の層の外の余り {limit - outside} と違う"
    assert budget >= _bytes(settings[service]["memory.min"]), (node, budget, settings)


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_puts_its_resident_layers_before_the_bulk_layers_on_the_cpu(rendered: list[Manifest], node: str) -> None:
    """worker の本体と常駐の job の層の cpu.weight は、一括の仕事の層(task・CLI・命令)の cpu.weight より高い。配備の宣言の CLI の層
    (CGROUP_DEPLOY_LAYER)は常駐の層の重み以下で、一括の仕事の層より高い。失敗ケース: 子に cpu を掛けない(重みの無い)層では、CPU が
    埋まった時に起きた画面の thread が CLI と task の thread と同じ重みで実行待ちに並び、ws の ping の往復が 50〜140 ms に跳ねた。
    配備の層が一括の仕事と同じ重みでは、load 100 以上の時に宣言の CLI の import の wall が CPU の秒の 2〜3 倍に伸びた。cpu.idle は
    掛けない(命令の層が CPU の埋まった間ずっと止まる)。"""
    worker = _worker_on(rendered, node)
    settings = dict(_layout(worker))
    service = str(_env_value(worker, "DOEFF_WORKER_SERVICE_CGROUP")["value"])
    weights = {name: int(values["cpu.weight"]) for name, values in settings.items() if "cpu.weight" in values}
    assert set(weights) == set(settings), settings
    resident = {CGROUP_FIRST_LAYER, service}
    prompt = {CGROUP_DEPLOY_LAYER} & set(settings)
    for bulk in set(settings) - resident - prompt:
        assert all(weights[name] > weights[bulk] for name in resident | prompt), weights
    for name in prompt:
        assert weights[name] <= min(weights[layer] for layer in resident), weights
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


def _cgroup_delegate(deployment: Manifest) -> Manifest:
    sidecars = [
        _mapping(c, "initContainer")
        for c in _sequence(_pod_spec(deployment)["initContainers"], "initContainers")
        if _mapping(c, "initContainer")["name"] == "cgroup-delegate"
    ]
    assert len(sidecars) == 1, sidecars
    return sidecars[0]


@pytest.mark.parametrize("node", sorted(CGROUP_LAYERS))
def test_worker_cgroup_is_delegated_by_a_root_sidecar_while_the_worker_stays_unprivileged(rendered: list[Manifest], node: str) -> None:
    """委ねる係は root・privileged の native sidecar(restartPolicy Always — worker の container が起こし直されても渡し直す)で、image は
    worker と同じ。worker の container は特権を持たず(privileged は host の cgroup の名前空間で起き、/sys/fs/cgroup の見えが変わる)、
    host の cgroupfs を WORKER_CGROUP_HOST に置く。委ねる係の宣言は層を持つ全部の機体で同じ(zeus の dir の宣言と、ほかの機体の部品 cgroup-layers の宣言が離れない)。"""
    worker = _worker_on(rendered, node)
    sidecar = _cgroup_delegate(worker)
    assert sidecar["restartPolicy"] == "Always"
    assert sidecar["securityContext"] == {"privileged": True, "runAsUser": 0, "runAsNonRoot": False}
    assert sidecar["image"] == _container(worker)["image"]
    assert sidecar == _cgroup_delegate(_worker_on(rendered, "zeus"))
    assert "privileged" not in _mapping(_container(worker).get("securityContext") or {}, "securityContext")
    host = str(_env_value(worker, "WORKER_CGROUP_HOST")["value"])
    assert _mount_at(worker, host)["name"] == "host-cgroup"
    assert _volume(worker, "host-cgroup")["hostPath"] == {"path": "/sys/fs/cgroup", "type": "Directory"}


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


@pytest.mark.parametrize("node", NODES)
def test_a_prepare_layer_is_declared_with_its_name_its_margin_and_a_version_that_knows_it(rendered: list[Manifest], node: str) -> None:
    """準備の層を宣言する worker は、層の名(DOEFF_WORKER_PREPARE_CGROUP)と機体の余裕(WORKER_EVICTION_MARGIN)を受け、版は準備の層を
    知る版(PREPARE_LAYER_SINCE)かその子孫。余裕 + 準備の層の上限は、その機体の平常の追い出しまでの空き(SMALL_NODE_ROOM_MIB)以下 —
    keeper は平常の空きで準備を起こし、空きの縮んだ間だけ待つ。失敗ケース: 版が古いと keeper は準備を jobs の層で起こし、worker が縮めた
    jobs の上限で OOM になる(11-02 の k3s-1)。余裕 1 GiB を引くと、k3s-1 の平常の空き 約 1 GiB では準備がいつまでも起きない。"""
    worker = _worker_on(rendered, node)
    if not any(entry["name"] == "WORKER_CGROUP_LAYOUT" for entry in _env(worker)):
        return
    settings = dict(_layout(worker))
    if CGROUP_PREPARE_LAYER not in settings:
        assert not any(entry["name"] == "DOEFF_WORKER_PREPARE_CGROUP" for entry in _env(worker)), worker
        return
    assert _env_value(worker, "DOEFF_WORKER_PREPARE_CGROUP")["value"] == CGROUP_PREPARE_LAYER
    margin = _bytes(str(_env_value(worker, "WORKER_EVICTION_MARGIN")["value"]))
    assert margin + _bytes(settings[CGROUP_PREPARE_LAYER]["memory.max"]) <= SMALL_NODE_ROOM_MIB * 2**20, (margin, settings)
    version = _worker_version(rendered, node)
    assert _same_or_ancestor(PREPARE_LAYER_SINCE, version), (
        f"{node} の worker の版 {version} は準備の層を知らない(PREPARE_LAYER_SINCE {PREPARE_LAYER_SINCE} の子孫でない)"
    )


def test_cgroup_layout_on_a_version_without_it_is_refused() -> None:
    """層の前の版(10-24 朝の zeus の版 0e6081c18)で層を宣言する宣言を、版の判じが断る。"""
    assert not _same_or_ancestor(CGROUP_LAYOUT_SINCE, "0e6081c18c0d58f665246c296944d8939d0a2dcd")


# worker の doeff の版の定義元は workers/worker-version.env 1 つ(1 行 1 worker「<名>=<commit>」)。名の固定した
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
# 起動の args が版の file を読む綴り(`$$(` は k8s の $(名) の展開を避け、shell に `$(` で届く)。preStop の命令は kubelet が展開しないので
# この綴りを使わない(test_pre_stop_reads_the_keeper_commit_in_shell)。
VERSION_READ = 'WORKER_DOEFF_COMMIT="$$(cat "$WORKER_VERSION_FILE")"'
KEEPER_COMMIT = "/tmp/doeff-worker-keeper/commit"
DRAIN = "ROLE=drain exec /opt/worker-boot.sh"
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


def _pre_stop_script(worker: Manifest) -> str:
    """preStop の exec の命令(/bin/sh -c <script>)の script。"""
    lifecycle = _mapping(_container(worker)["lifecycle"], "lifecycle")
    command = [str(word) for word in _sequence(_mapping(_mapping(lifecycle["preStop"], "preStop")["exec"], "exec")["command"], "command")]
    assert command[:2] == ["/bin/sh", "-c"] and len(command) == 3, command
    return command[2]


@pytest.mark.parametrize("name", NODES)
@pytest.mark.parametrize("keeper_started", [True, False])
def test_pre_stop_reads_the_keeper_commit_in_shell(rendered: list[Manifest], name: str, keeper_started: bool, tmp_path: Path) -> None:
    """preStop は keeper の状態の dir の commit(keeper が起きる前なら版の file の版)を WORKER_DOEFF_COMMIT に入れて drain を起こす。
    kubelet は lifecycle の exec の命令の $(名) を展開しない(container の command・args だけ)ので、宣言の文字をそのまま sh が読む。
    反例: `$$(` と書くと sh が `$$` を PID と読み、版が「<PID>(cat …)」になって drain が落ち、drain を待たずに SIGTERM が届く。
    確かめ方: 宣言の script の keeper の file を tmp の file に、drain の起動を版の表示に替えて sh で走らせ、40 桁の commit になるかを見る。"""
    script = _pre_stop_script(_worker_on(rendered, name))
    assert script.count(KEEPER_COMMIT) == 1 and script.endswith(DRAIN), script
    keeper_commit = tmp_path / "keeper-commit"
    version_file = tmp_path / "version"
    version_file.write_text("1" * 40, encoding="utf-8")
    if keeper_started:
        keeper_commit.write_text("2" * 40, encoding="utf-8")
    probe = script.replace(KEEPER_COMMIT, str(keeper_commit)).removesuffix(DRAIN) + 'printf %s "$WORKER_DOEFF_COMMIT"'
    shown = subprocess.run(
        ["/bin/sh", "-c", probe], env={"PATH": "/usr/bin:/bin", "WORKER_VERSION_FILE": str(version_file)}, capture_output=True, text=True, check=True
    ).stdout
    assert shown == ("2" if keeper_started else "1") * 40


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
    の機体の行の task の予約と、置ける組の印(どちらも MACHINE_POOLS)。上に載る系の
    準備の script(worker-prepare)は置かない — それは上に載る系の仕事を受ける worker の準備で、空の機体では通らない。
    反例: worker-env の能力の残りを名乗ると、その能力だけを要る上に載る系の job が memory 4GiB の機体へ置かれ得る。"""
    host_only = _worker_on(rendered, node)
    assert _env_value(host_only, "WORKER_PROVIDES")["value"] == HOST_ONLY_PROVIDES
    row = _mapping(_room_rows()[node], node)
    assert row["taskReserve"] == MACHINE_POOLS[node][0]
    assert tuple(row["pools"]) == MACHINE_POOLS[node][1]
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


@pytest.mark.parametrize("node", GUEST_NODES)
def test_guest_worker_offers_the_upper_systems_guest_capabilities_and_runs_its_prepare_script(rendered: list[Manifest], node: str) -> None:
    """余りの空きで会話を受ける機体(GUEST_NODES)の worker は、機体の能力 2 つの後ろに上に載る系の置く能力の残り
    $(WORKER_PROVIDES_GUEST_EXTRA)を名乗り(zeus の $(WORKER_PROVIDES_EXTRA) でない — zeus だけが持つ能力を名乗らない)、上に載る系の
    準備の script を起動の前に走らせ(worker-prepare を mount する)、枠は枠の file の機体の行(MACHINE_POOLS)。作業場所の volume の mount の
    係は置かない(余りで受ける仕事は機体の dir で始める — ../deploy/k8s/guest-work の註)。
    反例: zeus の能力の残りを名乗ると、作業の dir の持ち主の能力を要る仕事(zeus と同じ path で作業の dir を読み書きする)が、その dir の
    無い機体へ置かれる。準備の script を外すと、会話の CLI が要る道具が PATH に無い。係を置くと、作業場所の volume が網越しに zeus の
    USB の SSD の replica へ書く(遅く、zeus の SSD の書きを増やす)。"""
    guest = _worker_on(rendered, node)
    assert _env_value(guest, "WORKER_PROVIDES")["value"] == GUEST_PROVIDES
    assert not any(entry["name"] == "WORKER_WORKSPACE_MOUNT_DIR" for entry in _env(guest)), guest
    assert "workspace-mount" not in [
        _mapping(c, "initContainer")["name"] for c in _sequence(_pod_spec(guest)["initContainers"], "initContainers")
    ]
    row = _mapping(_room_rows()[node], node)
    assert row["taskReserve"] == MACHINE_POOLS[node][0]
    assert tuple(row["pools"]) == MACHINE_POOLS[node][1]
    assert _mount_at(guest, PREPARE_DIR).get("readOnly") is True
    env_from = _sequence(_container(guest)["envFrom"], "envFrom")
    assert [
        _mapping(_mapping(s, "envFrom")["configMapRef"], "configMapRef")["name"] for s in env_from
    ] == ["worker-env"]


@pytest.mark.parametrize("node", OTHER_NODES)
def test_machine_worker_resources_fit_its_node(rendered: list[Manifest], node: str) -> None:
    """zeus でない機体の worker の資源は、機体の大きさに合わせた値(MACHINE_RESOURCES)。"""
    resources = _mapping(_container(_worker_on(rendered, node))["resources"], "resources")
    assert resources["requests"] == MACHINE_RESOURCES[node].requests
    assert resources["limits"] == MACHINE_RESOURCES[node].limits


def _room_rows() -> dict[str, object]:
    rows = yaml.safe_load(ROOM_FILE.read_text(encoding="utf-8"))
    return _mapping(rows, str(ROOM_FILE))


def test_every_worker_reads_its_room_from_the_fixed_named_configmap_file(rendered: list[Manifest], worker: Manifest) -> None:
    """worker の枠は、名の固定した ConfigMap worker-room を volume で置いた file(subPath でない)から読む — 中身を変えても Pod の template は
    変わらず、kubelet が file を入れ替え、worker が heartbeat の前に読み直す。env で枠を決めない(env の値を変えると Pod が作り直される)。
    反例: env の WORKER_CAPACITY で枠を決めると、常駐の job を 1 つ足すたびに worker の Pod が作り直され、走っているターンが切れる。"""
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
    worker が読まず、前の値のまま名乗り続ける)。背景の仕事を同時に置ける数 backgroundRoom は zeus が 4(背景の仕事に
    残す枠 3 + 入れ替えの並べた置き先 1 — 手書きの capacity が持っていた分)、他の機体は 0。手書きの capacity の欄は持たない。
    省ける欄 warmServices は真偽だけ(service を待ちの子から分ける旗 — 真偽でない綴りは worker の
    起動を断る)。"""
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
    """枠の file の置ける組の印(欄 pools)は、1 台に縛る訳を SINGLE_MACHINE_POOLS に書いた組を除き、どれも 2 台以上の機体の行が名乗る。
    名は pool- で始まり、機体の行は同じ印を 2 度書かない。zeus の行は組を名乗らない(zeus の常駐の job は移さない)。
    反例: 1 台だけが名乗る組は、組の印で宣言した job を 1 台に縛る — その 1 台が止まると job は置き先を失う(2026-10-27 10:38 JST に
    1 台にしか置けない job が約 16 分止まった)。訳を書いた組の job は、N+1 の判断の規則 1 を job の宣言の :single-host で外す。"""
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
    assert {pool: nodes for pool, nodes in members.items() if len(nodes) < 2 and pool not in SINGLE_MACHINE_POOLS} == {}
    assert {pool: members.get(pool, []) for pool in SINGLE_MACHINE_POOLS if len(members.get(pool, [])) != 1} == {}
    assert sorted(members) == sorted(POOL_JOBS)


def test_the_memory_allowance_is_the_worker_limit_less_the_worker_and_its_tasks(rendered: list[Manifest]) -> None:
    """枠の file の memoryMiB(常駐の job の memory の予算)は、機体の worker の container の memory の上限から worker 自身の分と task と準備の
    分を引いた値(定義点は cgroup の層 — worker の memory.min と task と準備の層の memory.max)。
    反例: 上限を越える予算は、N+1 の判断の規則 3 を通しても、移った job の起動の山で worker の container の上限に当たり、worker の memory の
    守りが job の起動を止め続ける(k3s-1 の例 — 上に載る系の宣言の註)。"""
    rows = _room_rows()
    for node in NODES:
        worker = _worker_on(rendered, node)
        limit = _bytes(str(_mapping(_mapping(_container(worker)["resources"], "resources")["limits"], "limits")["memory"]))
        settings = dict(_layout(worker))
        jobs = str(_env_value(worker, "DOEFF_WORKER_JOB_CGROUP")["value"])
        # 準備の層を宣言する機体は、前の準備の分を jobs と prepare に分けた(和は前のまま)— 両方を引く。
        prepare = _bytes(settings[CGROUP_PREPARE_LAYER]["memory.max"]) if CGROUP_PREPARE_LAYER in settings else 0
        # 会話の CLI の層が限る層の機体は、会話の CLI と命令の層も引く(zeus の CLI の層は守る層で、CLI は常駐の job の子として予算に入る)。
        conversations = (
            _bytes(settings[CGROUP_CLI_LAYER]["memory.max"]) + _bytes(settings[CGROUP_CHILD_LAYER]["memory.max"])
            if "memory.max" in settings.get(CGROUP_CLI_LAYER, {})
            else 0
        )
        # cargo の中間物の tmpfs の分(zeus の上限に足した分 — cargo-build-in-memory.yaml の頭の註)は命令の層の取り分で、常駐の job の予算へ回さない。
        volumes = [_mapping(v, "volume") for v in _sequence(_pod_spec(worker)["volumes"], "volumes")]
        cargo_build = sum(_bytes(str(_mapping(v["emptyDir"], "emptyDir")["sizeLimit"])) for v in volumes if v["name"] == "cargo-build")
        allowance = (
            limit - _bytes(settings[CGROUP_FIRST_LAYER]["memory.min"]) - _bytes(settings[jobs]["memory.max"]) - prepare - conversations - cargo_build
        ) // 2**20
        assert _mapping(rows[node], node)["memoryMiB"] == allowance, node


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
    """coordinator の Pod は Guaranteed(oom_score_adj -997)— atlas の memory が尽きた時に、kernel が worker の job と並べて殺さない。
   Guaranteed は全部の container(init を含む)で cpu と memory の requests = limits が要る —
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


def test_coordinator_prepare_builds_alone_with_only_the_job_and_its_priority_class(prepared: list[Manifest]) -> None:
    """組む Job の dir は、その dir だけで組み立てられ、PriorityClass 1 つと Job 1 つだけを出す — 版を写すために読む ../coordinator の物は
    出力から外れ、coordinator の物は coordinator/ の Kustomization だけが持つ。Flux は coordinator/ と別の Kustomization で当て、coordinator/ の
    Kustomization がその終わりを待つ(ADR-DOE-CLUSTER-003 R2・R5)。"""
    assert [(doc["kind"], _role(doc)) for doc in prepared if doc["kind"] != "PriorityClass"] == [("Job", "prepare")]
    assert [_mapping(doc["metadata"], "metadata")["name"] for doc in _priority_classes(prepared)] == [
        COORDINATOR_PRIORITY_CLASS
    ]
    assert len(prepared) == 2, [(doc["kind"], _mapping(doc["metadata"], "metadata")["name"]) for doc in prepared]


# 組む Job の 1 回の試しが止まった(hang)時に切る秒(ADR-DOE-CLUSTER-003 R4)。Pod の spec に置く — Pod の期限は kubelet が Pod を受けた
# 刻(status.startTime)から数え、置けずに Pending の間は数えない。Job の spec に置くと Pending の間も数え、一度も走らずに Failed になる
# (2026-11-02 の版上げは、atlas の memory の requests の和が allocatable の 98%・空き 359Mi で request 512Mi の Pod が置けず、20 分で
# DeadlineExceeded になった)。Job の名は版で決まり、Flux は Failed の Job を作り直さないので、次の版上げか人の手まで固まった。
PREPARE_HANG_SECONDS = 1200


def test_coordinator_prepare_job_waits_while_unplaced_and_cuts_a_hang_of_its_run(prepared: list[Manifest]) -> None:
    """組む Job の期限は Pod の spec が持ち(走った時間だけを数える)、Job の spec は期限を持たない — 置けない間は Pending で待ち、空いた
    時に人の手なしに組み終える。Job が失敗するのは組めない時(非 0 で終わる・1 回の試しが期限を越えて止まる)だけ(ADR-DOE-CLUSTER-003 R4)。"""
    (job,) = _prepare_jobs(prepared)
    assert "activeDeadlineSeconds" not in _mapping(job["spec"], "Job の spec"), (
        "Job の spec の activeDeadlineSeconds は置けない(Pending の)間も数え、置き場の不足を組みの失敗と同じ Failed にする"
    )
    assert _pod_spec(job).get("activeDeadlineSeconds") == PREPARE_HANG_SECONDS


def test_a_removed_try_of_the_prepare_job_is_not_counted_as_a_failure(prepared: list[Manifest]) -> None:
    """組む Job の試しが消された時(kubelet の memory 不足の eviction・より高い class の追い出し・drain — Pod の condition
    DisruptionTarget)は backoffLimit に数えず作り直す — 消された試しが 2 度で Job が Failed になると、置けなかった時と同じく次の版上げまで
    固まる。非 0 の終わりと期限切れは今まで通り失敗に数える(規則は DisruptionTarget の Ignore 1 つだけ)(ADR-DOE-CLUSTER-003 R4)。"""
    (job,) = _prepare_jobs(prepared)
    policy = _mapping(_mapping(job["spec"], "Job の spec").get("podFailurePolicy"), "podFailurePolicy")
    assert policy == {"rules": [{"action": "Ignore", "onPodConditions": [{"type": "DisruptionTarget"}]}]}
    assert _pod_spec(job)["restartPolicy"] == "Never", "podFailurePolicy は restartPolicy Never の Job だけが持てる"


# 組む Job と coordinator の PriorityClass(ADR-DOE-CLUSTER-003 R5)。値は既定の 0(PriorityClass を持たない一般の Pod)より上で、
# user が定義できる上限 10 億(API の上限 — storage と node の system の class はこの辺りから上を使う)より下 — 追い出すのは一般の Pod だけ。
COORDINATOR_PRIORITY_CLASS = "doeff-cluster-coordinator"
USER_PRIORITY_CEILING = 1_000_000_000


def _priority_classes(built: list[Manifest]) -> list[Manifest]:
    return [doc for doc in built if doc["kind"] == "PriorityClass"]


def test_coordinator_and_its_prepare_job_outrank_the_default_pods_on_their_node(
    rendered: list[Manifest], prepared: list[Manifest]
) -> None:
    """組む Job と coordinator は atlas に固定され他の node へ逃げられないので、置き場を他の workload の量に任せない — 同じ
    PriorityClass(既定の 0 より上・PreemptLowerPriority)を持ち、node の memory の空きが足りない時は、低い Pod を追い出して置かれる。
    coordinator も持つのは、Recreate で旧い Pod を止めた拍に空いた memory を他の Pod が先に取ると、新しい Pod が置けず全部の worker が
    coordinator に届かないままになるから(ADR-DOE-CLUSTER-003 R5)。"""
    (job,) = _prepare_jobs(prepared)
    (coordinator,) = _deployments(rendered, "coordinator")
    assert _pod_spec(job).get("priorityClassName") == COORDINATOR_PRIORITY_CLASS
    assert _pod_spec(coordinator).get("priorityClassName") == COORDINATOR_PRIORITY_CLASS
    (priority,) = _priority_classes(prepared)
    value = priority["value"]
    assert isinstance(value, int) and 0 < value < USER_PRIORITY_CEILING, f"PriorityClass の値 {value!r} が 0 と 10 億の間に無い"
    assert priority.get("preemptionPolicy") == "PreemptLowerPriority"
    assert priority.get("globalDefault") is False, "既定の class にすると、一般の Pod も同じ値になって追い出せる相手が居なくなる"


def test_the_priority_class_is_applied_before_the_pods_that_name_it(rendered: list[Manifest]) -> None:
    """PriorityClass は組む Job の dir(Flux が先に当てる Kustomization)が持ち、coordinator/ と根の deploy/k8s は持たない — coordinator/ が
    持つと、coordinator の Kustomization は組む Job の終わりを待つので、初めて当てる時に Job の Pod が無い class を名指して作られず
    (admission が断る)、2 つの Kustomization が互いを待って止まる(ADR-DOE-CLUSTER-003 R5)。"""
    assert _priority_classes(_build(K8S / "coordinator")) == []
    assert _priority_classes(rendered) == []


# Flux の印: 当てが template の変えられない(immutable)欄で断られた時に、その物を消して作り直す。
FLUX_FORCE = "kustomize.toolkit.fluxcd.io/force"


def test_a_prepare_job_changed_at_the_same_version_is_recreated_not_refused(prepared: list[Manifest]) -> None:
    """Job の template は変えられない — 版を変えずに組む Job の template を変える commit を Flux が当てると、当てが断られて
    Kustomization が Ready にならず、coordinator が当たらない。組む Job は Flux の force の印を持ち、断られた時は作り直される(組み済みの
    root なら秒で終わる)(ADR-DOE-CLUSTER-003 R2)。"""
    (job,) = _prepare_jobs(prepared)
    annotations = _mapping(_mapping(job["metadata"], "metadata").get("annotations", {}), "Job の annotations")
    assert annotations.get(FLUX_FORCE) == "Enabled"


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


# 版の向き: どの機体の worker の版も、coordinator の版と同じか、その祖先(古い版)でなければならない。
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


# Program の cache の名の版: どの機体の worker の版も、service の Program の cache を
# (宣言の版・sha) で名づける版(PROGRAM_CACHE_BY_VERSION_SINCE)かその子孫でなければならない。それより前の worker は cache を <sha>.json と
# sha だけで名づけ、doeff の pin を上げても Program の sha が変わらない宣言し直しで前の版の file を子へ渡し、子が「版が違うので Program を
# 解かない」で落ち続ける — 10-16・10-17・10-18 の 3 回 eos(901c200c8)の
# patrol-watch が落ち続け、k3s-0(同じ版)の ideas も 0.7.0 で落ちた。直しの版の後に機体の worker を古い版のまま残すと
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


# zeus の worker の作業の root(volume work — WORK_DIR=/work)は、機体の root の disk でなく USB の SSD の下に置く(
# zeus の容量を食う置き場を USB の SSD へ移す。同じ日の 13:39 JST に root の disk の空きが準備の最低
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


# zeus の worker は、USB の SSD の上で作業の root の外に在る dir 2 つを、作業の root の下の host-usb/ に置く(
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


@pytest.mark.parametrize("name", NODES)
def test_every_worker_mounts_the_optional_machine_ssh_secret_without_subpath(rendered: list[Manifest], name: str) -> None:
    """機体へ ssh で入る key・known_hosts・ssh の設定は、名の固定した Secret worker-machine-ssh を /etc/worker-machine-ssh に読み取り専用で
    置く(subPath でない — 中身の差し替えに Pod を作り直さない)。無くても起動する(optional)。"""
    worker = _worker_on(rendered, name)
    mount = _mount_at(worker, "/etc/worker-machine-ssh")
    assert "subPath" not in mount and mount.get("readOnly") is True
    secret = _mapping(_volume(worker, str(mount["name"]))["secret"], "secret")
    assert secret["secretName"] == "worker-machine-ssh"
    assert secret.get("optional") is True
    assert secret.get("defaultMode") == 0o400


# zeus の worker の /tmp は memory(tmpfs の emptyDir — 機体の dir の tmp-in-memory.yaml の頭の註)。作っては消す一時の file の書きを SSD に
# 届けない。上限は tmpfs の大きさにもなり、越える書きは ENOSPC で落ちる。
ZEUS_TMP_SIZE_LIMIT = "8Gi"


def test_zeus_worker_keeps_tmp_in_memory_with_a_size_limit(zeus_worker: Manifest) -> None:
    """zeus の worker の container の /tmp は medium Memory の emptyDir で、sizeLimit を持つ(上限の無い tmpfs は Pod の memory を食い尽くせる)。"""
    mount = _mount_at(zeus_worker, "/tmp")
    assert "subPath" not in mount
    assert _volume(zeus_worker, str(mount["name"]))["emptyDir"] == {"medium": "Memory", "sizeLimit": ZEUS_TMP_SIZE_LIMIT}


# zeus の worker の cargo の中間物は memory(tmpfs の emptyDir — 機体の dir の cargo-build-in-memory.yaml の頭の註)。
ZEUS_CARGO_BUILD_SIZE_LIMIT = "16Gi"
ZEUS_CARGO_BUILD_DIR = "/cargo-build"


def test_zeus_worker_points_cargo_intermediates_at_a_sized_memory_mount(zeus_worker: Manifest) -> None:
    """zeus の worker の CARGO_BUILD_BUILD_DIR は sizeLimit つきの medium Memory の emptyDir の下で、作業木ごとに分かれる形を持つ。失敗ケース:
    作業木の hash の無い 1 つの値だと、作業木どうしが中間物を分け合い、cargo が別の作業木の build を黙って使う。"""
    mount = _mount_at(zeus_worker, ZEUS_CARGO_BUILD_DIR)
    assert _volume(zeus_worker, str(mount["name"]))["emptyDir"] == {"medium": "Memory", "sizeLimit": ZEUS_CARGO_BUILD_SIZE_LIMIT}
    assert _env_value(zeus_worker, "CARGO_BUILD_BUILD_DIR")["value"] == f"{ZEUS_CARGO_BUILD_DIR}/{{workspace-path-hash}}"
    assert "CARGO_TARGET_DIR" not in [entry["name"] for entry in _env(zeus_worker)]


@pytest.mark.parametrize("name", [node for node in NODES if node != "zeus"])
def test_other_workers_keep_cargo_intermediates_in_target(rendered: list[Manifest], name: str) -> None:
    """zeus 以外の機体の worker は cargo の置き場を替えない(memory の取り分を足していない)。"""
    worker = _worker_on(rendered, name)
    assert "CARGO_BUILD_BUILD_DIR" not in [entry["name"] for entry in _env(worker)]
    mounts = [_mapping(m, "volumeMount") for m in _sequence(_container(worker).get("volumeMounts", []), "volumeMounts")]
    assert not [m for m in mounts if m["mountPath"] == ZEUS_CARGO_BUILD_DIR], mounts


# zeus の worker の /dev/shm は memory(tmpfs の emptyDir — 機体の dir の dev-shm-in-memory.yaml の頭の註)。test の使い捨ての PostgreSQL の
# data の書きを SSD に届けない(card ki-5ab9473f5328)。
ZEUS_DEV_SHM_SIZE_LIMIT = "8Gi"


def test_zeus_worker_has_a_sized_dev_shm_in_memory(zeus_worker: Manifest) -> None:
    """zeus の worker の container の /dev/shm は medium Memory の emptyDir で、sizeLimit を持つ(既定の 64 MiB では PostgreSQL の data が入らない)。"""
    mount = _mount_at(zeus_worker, "/dev/shm")
    assert "subPath" not in mount
    assert _volume(zeus_worker, str(mount["name"]))["emptyDir"] == {"medium": "Memory", "sizeLimit": ZEUS_DEV_SHM_SIZE_LIMIT}


@pytest.mark.parametrize("name", [node for node in NODES if node != "zeus"])
def test_other_workers_keep_the_default_dev_shm(rendered: list[Manifest], name: str) -> None:
    """zeus 以外の機体の worker は /dev/shm を mount しない(container の既定の 64 MiB のまま)。"""
    mounts = [_mapping(m, "volumeMount") for m in _sequence(_container(_worker_on(rendered, name)).get("volumeMounts", []), "volumeMounts")]
    assert not [m for m in mounts if m["mountPath"] == "/dev/shm"], mounts


@pytest.mark.parametrize("name", [node for node in NODES if node != "zeus"])
def test_other_workers_keep_tmp_in_the_container(rendered: list[Manifest], name: str) -> None:
    """zeus 以外の機体の worker は /tmp を mount しない(container の書き層のまま — 機体ごとの差は機体の dir だけが持つ)。"""
    mounts = [_mapping(m, "volumeMount") for m in _sequence(_container(_worker_on(rendered, name)).get("volumeMounts", []), "volumeMounts")]
    assert not [m for m in mounts if m["mountPath"] == "/tmp"], mounts
