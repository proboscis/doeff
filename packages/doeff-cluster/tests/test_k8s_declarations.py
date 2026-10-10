"""k8s の宣言(deploy/k8s)は、機体ごとに静的な worker 1 つと coordinator 1 つを持ち、上に載る系の値を名の固定した ConfigMap で受ける。

利用者の決定(2026-10-09 17:03 原文 "we are not supposed to frequently restart doeff worker! it must be very static."): worker の Pod の
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


# 受ける数が既定(WORKER_CAPACITY 2・WORKER_TASK_RESERVE 0)と違う機体 — (受ける数, task に空けておく数)。atlas は機体の仕事に加えて、
# 上に載る系の日次の全体検証の task(:needs host-atlas・同時に 2 本まで)を受ける(2026-10-10 調整役 cisco-c8 の値)。
HOST_ONLY_ROOM = {"atlas": ("3", "2")}
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
        assert _mapping(spec["strategy"], "strategy")["type"] == "Recreate"
        selector = _mapping(_mapping(spec["selector"], "selector")["matchLabels"], "matchLabels")
        assert selector.get("doeff.dev/node") == node


def test_zeus_worker_is_one_deployment_with_100gi_memory_limit(zeus_worker: Manifest) -> None:
    """zeus の worker は 1 つで、資源の上限は今の値(memory 100Gi・CPU 16)と取り分(CPU 4・memory 8Gi)を引き継ぐ。"""
    spec = _mapping(zeus_worker["spec"], "spec")
    assert spec["replicas"] == 1
    assert _mapping(spec["strategy"], "strategy")["type"] == "Recreate"
    resources = _mapping(_container(zeus_worker)["resources"], "resources")
    assert resources["limits"] == {"cpu": "16", "memory": "100Gi"}
    assert resources["requests"] == {"cpu": "4", "memory": "8Gi"}


# worker の doeff の版(WORKER_DOEFF_COMMIT)の定義元は機体の dir の version.yaml 1 つ(JSON patch の add 1 行)— 雛形には版の行を置かない。
# 機体ごとに 1 台ずつ版を上げられるように(7 台が 1 行を共有すると、1 回の main 入りで 7 台が同時に替わる)。版の行は env の WORK_DIR の
# 直後に入る(行の場所が変わると Pod の template が変わり、版を変えなくても Pod が作り直される)。
VERSION_PATCH = "version.yaml"
SHA = re.compile(r"^[0-9a-f]{40}$")


def _version_adds(node: str) -> list[dict[str, object]]:
    path = K8S / "nodes" / node / VERSION_PATCH
    assert path.is_file(), f"{node} の機体の dir に版の file {VERSION_PATCH} が無い"
    ops = [_mapping(op, f"{path} の操作") for op in _sequence(yaml.safe_load(path.read_text()), str(path))]
    return [op for op in ops if op["op"] == "add"]


def test_template_has_no_version_line() -> None:
    """雛形(worker/worker.yaml)には版の行を置かない — 古い値や代わりの値を base に残さない。"""
    template = _build(K8S / "worker")
    assert [d for d in template if d["kind"] == "Deployment" and any(e["name"] == "WORKER_DOEFF_COMMIT" for e in _env(d))] == []


@pytest.mark.parametrize("node", NODES)
def test_worker_version_comes_from_its_node_dir(rendered: list[Manifest], node: str) -> None:
    """機体の worker の版は、その機体の dir の version.yaml の add 1 行の値(40 桁)で、env の WORK_DIR の直後に入る。"""
    adds = _version_adds(node)
    assert len(adds) == 1, f"{node} の版の add はちょうど 1 つ: {adds!r}"
    added = _mapping(adds[0]["value"], "add の値")
    assert added["name"] == "WORKER_DOEFF_COMMIT"
    assert SHA.match(str(added["value"])), f"{node} の版が 40 桁の 16 進でない: {added['value']!r}"
    worker = _worker_on(rendered, node)
    assert _env_value(worker, "WORKER_DOEFF_COMMIT")["value"] == added["value"]
    names = [entry["name"] for entry in _env(worker)]
    assert names[names.index("WORK_DIR") + 1] == "WORKER_DOEFF_COMMIT", f"{node} の版の行が WORK_DIR の直後に無い: {names!r}"


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
    """zeus でない機体の worker は、機体の能力 2 つだけを名乗り(上に載る系の能力の残り WORKER_PROVIDES_EXTRA を足さない)、受ける数
    (WORKER_CAPACITY 2・task に空けておく数 WORKER_TASK_RESERVE 0)を env で決める(env は envFrom の worker-env の同じ名より優先する)。
    上に載る系の準備の script(worker-prepare)は置かない — それは上に載る系の仕事を受ける worker の準備で、空の機体では通らない。
    反例: worker-env の能力の残りを名乗ると、その能力だけを要る上に載る系の job が memory 4GiB の機体へ置かれ得る。"""
    host_only = _worker_on(rendered, node)
    capacity, reserve = HOST_ONLY_ROOM.get(node, ("2", "0"))
    assert _env_value(host_only, "WORKER_PROVIDES")["value"] == HOST_ONLY_PROVIDES
    assert _env_value(host_only, "WORKER_CAPACITY")["value"] == capacity
    assert _env_value(host_only, "WORKER_TASK_RESERVE")["value"] == reserve
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


def test_zeus_worker_keeps_upper_system_capabilities(zeus_worker: Manifest) -> None:
    """zeus の worker は今のまま: 能力の残り(WORKER_PROVIDES_EXTRA)を足し、受ける数は worker-env から受ける(env で決めない)。"""
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


# zeus の worker の作業の root(volume work — WORK_DIR=/work)は、機体の root の disk でなく USB の SSD の下に置く(2026-10-10 15:0x の利用者の
# 決定 原文 "the strategy is to replace the volume consuming part with usb ssd on zeus" — 同じ日の 13:39 JST に root の disk の空きが準備の最低
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


def test_other_workers_keep_the_default_work_root(rendered: list[Manifest]) -> None:
    """zeus 以外の機体の worker の作業の root は今のまま(機体ごとの差は機体の dir だけが持つ)。"""
    others = [doc for doc in rendered if doc.get("kind") == "Deployment" and _mapping(doc["metadata"], "metadata").get("name") not in (
        "doeff-worker-zeus", "coordinator")]
    assert others
    for doc in others:
        assert _work_volume(doc)["path"] == DEFAULT_WORK_ROOT, _mapping(doc["metadata"], "metadata")["name"]
