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


@pytest.fixture(scope="module")
def rendered() -> list[Manifest]:
    """deploy/k8s を kubectl kustomize で組み立てた物の全部(1 回だけ組む)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, (
        "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    )
    done = subprocess.run(
        [kubectl, "kustomize", str(K8S)], capture_output=True, text=True, timeout=25, check=False
    )
    assert done.returncode == 0, f"kubectl kustomize {K8S} が失敗した: {done.stderr.strip()}"
    return [
        _mapping(doc, "宣言の 1 つ") for doc in yaml.safe_load_all(done.stdout) if doc is not None
    ]


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


@pytest.fixture(scope="module")
def zeus_worker(rendered: list[Manifest]) -> Manifest:
    on_zeus = [doc for doc in _deployments(rendered, "worker") if _node(doc) == "zeus"]
    assert len(on_zeus) == 1, (
        f"zeus の worker の Deployment はちょうど 1 つ: {[d['metadata'] for d in on_zeus]!r}"
    )
    return on_zeus[0]


def test_zeus_worker_is_one_deployment_with_100gi_memory_limit(zeus_worker: Manifest) -> None:
    """zeus の worker は 1 つで、資源の上限は今の値(memory 100Gi・CPU 16)と取り分(CPU 4・memory 8Gi)を引き継ぐ。"""
    spec = _mapping(zeus_worker["spec"], "spec")
    assert spec["replicas"] == 1
    assert _mapping(spec["strategy"], "strategy")["type"] == "Recreate"
    resources = _mapping(_container(zeus_worker)["resources"], "resources")
    assert resources["limits"] == {"cpu": "16", "memory": "100Gi"}
    assert resources["requests"] == {"cpu": "4", "memory": "8Gi"}


def test_worker_names_itself_after_its_node(zeus_worker: Manifest) -> None:
    """worker の名と NODE_NAME は k8s の Node の名(fieldRef spec.nodeName)。能力は host-<Node の名> と host-systemd-readable を含む。"""
    assert _env_value(zeus_worker, "WORKER_NAME")["valueFrom"] == NODE_NAME_FIELD
    assert _env_value(zeus_worker, "NODE_NAME")["valueFrom"] == NODE_NAME_FIELD
    names = [entry["name"] for entry in _env(zeus_worker)]
    # k8s は $(名) を、前の行で定義した env だけから展開する — NODE_NAME は WORKER_PROVIDES より前に要る。
    assert names.index("NODE_NAME") < names.index("WORKER_PROVIDES")
    provides = str(_env_value(zeus_worker, "WORKER_PROVIDES")["value"]).split(",")
    assert "host-$(NODE_NAME)" in provides
    assert "host-systemd-readable" in provides


def test_worker_reads_host_systemd_units_read_only(zeus_worker: Manifest) -> None:
    """機体の /etc/systemd/system を読み取り専用で /host/etc/systemd/system に置き、その path を env で渡す。作業の root は /work。"""
    mount = _mount_at(zeus_worker, HOST_SYSTEMD_ROOT)
    assert mount.get("readOnly") is True
    volume = _volume(zeus_worker, str(mount["name"]))
    assert _mapping(volume["hostPath"], "hostPath")["path"] == "/etc/systemd/system"
    assert _env_value(zeus_worker, "WORKER_HOST_SYSTEMD_ROOT")["value"] == HOST_SYSTEMD_ROOT
    assert _env_value(zeus_worker, "WORK_DIR")["value"] == "/work"


def test_worker_runs_as_fixed_account_with_projected_token(
    rendered: list[Manifest], zeus_worker: Manifest
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
    pod = _pod_spec(zeus_worker)
    assert pod["serviceAccountName"] == WORKER_ACCOUNT
    assert pod["automountServiceAccountToken"] is False
    mount = _mount_at(zeus_worker, TOKEN_DIR)
    assert mount.get("readOnly") is True
    sources = _sequence(
        _mapping(_volume(zeus_worker, str(mount["name"]))["projected"], "projected")["sources"],
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


def test_coordinator_is_one_deployment_on_atlas(rendered: list[Manifest]) -> None:
    """coordinator は 1 つで atlas に置く(状態の file を 2 つが同時に書かないよう Recreate)。"""
    coordinators = _deployments(rendered, "coordinator")
    assert [_node(doc) for doc in coordinators] == ["atlas"]
    spec = _mapping(coordinators[0]["spec"], "spec")
    assert spec["replicas"] == 1
    assert _mapping(spec["strategy"], "strategy")["type"] == "Recreate"


def test_namespace_stays_with_the_deploying_side(rendered: list[Manifest]) -> None:
    """Namespace は宣言しない — 配備する側が持つ(ここで持つと、外した時に prune が Namespace ごと Secret を消す)。"""
    assert [doc for doc in rendered if doc["kind"] == "Namespace"] == []


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
