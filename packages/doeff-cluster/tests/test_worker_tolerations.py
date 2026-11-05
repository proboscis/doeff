"""worker の Pod が受ける taint(deploy/k8s/worker/worker.yaml の tolerations)。

決まり: worker は置く Node を nodeSelector で 1 つに決めるので、cordon 中の Node にも置き、Node が落ちても他へ動かさない。ただし Node の
状態の taint のうち、その状態の間 kubelet が Pod を入口で断る物(kubelet の eviction manager の Admit — DiskPressure か PIDPressure の
Node は critical でない Pod を全部断る)は受けない。受けると scheduler は断る Node へ置き続け、ReplicaSet は断られた Pod の代わりを
上限なく作る。受けなければ代わりの Pod は Pending の 1 本で待つ。

確かめ方: ``kubectl kustomize`` で deploy/k8s を組み立てた結果(Flux が当てる物と同じ)の worker の Deployment を、k8s の
Toleration.ToleratesTaint と同じ判定で読む。API server には触れない。kubectl が無い機体では赤にする(飛ばさない)。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[1] / "deploy" / "k8s"
NODES = tuple(sorted(p.name for p in (K8S / "nodes").iterdir() if p.is_dir()))
WORKER_APP = "doeff-worker"

Manifest = dict[str, object]

# kubelet が入口で Pod を断る Node の状態の taint。
REFUSING_TAINTS = (
    {"key": "node.kubernetes.io/disk-pressure", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/pid-pressure", "effect": "NoSchedule"},
)
# worker が受け続ける taint: cordon 中の Node(eos・k3s-2)・落ちた Node(置ける Node は 1 つ)・memory-pressure(その taint を受ける
# Pod を kubelet は断らない)。
KEPT_TAINTS = (
    {"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/not-ready", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/not-ready", "effect": "NoExecute"},
    {"key": "node.kubernetes.io/unreachable", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/unreachable", "effect": "NoExecute"},
    {"key": "node.kubernetes.io/memory-pressure", "effect": "NoSchedule"},
)


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{where} が mapping でない: {value!r}")
    return value


@pytest.fixture(scope="module")
def workers() -> dict[str, Manifest]:
    """deploy/k8s を kubectl kustomize で組み立てた物の worker の Deployment(Node の名 → Deployment)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    done = subprocess.run([kubectl, "kustomize", str(K8S)], capture_output=True, text=True, timeout=25, check=False)
    assert done.returncode == 0, f"kubectl kustomize {K8S} が失敗した: {done.stderr.strip()}"
    found: dict[str, Manifest] = {}
    for doc in yaml.safe_load_all(done.stdout):
        if not doc or doc.get("kind") != "Deployment":
            continue
        template = _mapping(_mapping(doc["spec"], "spec")["template"], "template")
        if _mapping(template["metadata"], "metadata").get("labels", {}).get("app") != WORKER_APP:
            continue
        selector = _mapping(_mapping(template["spec"], "Pod の spec").get("nodeSelector", {}), "nodeSelector")
        found[str(selector.get("kubernetes.io/hostname"))] = doc
    assert sorted(found) == list(NODES), f"worker の Deployment は機体の dir ごとに 1 つ: {sorted(found)!r}"
    return found


def _tolerations(deployment: Manifest) -> list[dict[str, object]]:
    pod = _mapping(_mapping(_mapping(deployment["spec"], "spec")["template"], "template")["spec"], "Pod の spec")
    listed = pod.get("tolerations", [])
    assert isinstance(listed, list), f"tolerations が list でない: {listed!r}"
    return [_mapping(t, "toleration") for t in listed]


def _tolerates(toleration: dict[str, object], taint: dict[str, str]) -> bool:
    """toleration 1 つが taint を受けるか(k8s の Toleration.ToleratesTaint と同じ: effect が空か同じ・key が空か同じ・operator が
    Exists か、Equal(空も Equal)で value が同じ)。"""
    if toleration.get("effect") and toleration["effect"] != taint["effect"]:
        return False
    if toleration.get("key") and toleration["key"] != taint["key"]:
        return False
    match_op = toleration.get("operator") or "Equal"
    if match_op == "Exists":
        return True
    return match_op == "Equal" and (toleration.get("value") or "") == taint.get("value", "")


@pytest.mark.parametrize("node", NODES)
def test_worker_does_not_tolerate_the_node_states_where_kubelet_refuses_pods(workers: dict[str, Manifest], node: str) -> None:
    """worker は DiskPressure と PIDPressure の taint を受けない — 代わりの Pod はその Node で Pending の 1 本で待ち、断られては作り直される
    嵐にならない。"""
    tolerations = _tolerations(workers[node])
    for taint in REFUSING_TAINTS:
        assert not any(_tolerates(t, taint) for t in tolerations), f"{node}: {taint['key']} を受ける toleration が在る: {tolerations!r}"


@pytest.mark.parametrize("node", NODES)
def test_worker_still_tolerates_cordon_and_a_lost_node_without_a_time_limit(workers: dict[str, Manifest], node: str) -> None:
    """worker は cordon 中の Node に置かれ、Node が落ちても時限なく留まる(NoExecute の toleration に tolerationSeconds を置かない)。"""
    tolerations = _tolerations(workers[node])
    for taint in KEPT_TAINTS:
        matching = [t for t in tolerations if _tolerates(t, taint)]
        assert matching, f"{node}: {taint['key']}:{taint['effect']} を受ける toleration が無い: {tolerations!r}"
        assert all("tolerationSeconds" not in t for t in matching), f"{node}: {taint['key']} に時限が在る: {matching!r}"
