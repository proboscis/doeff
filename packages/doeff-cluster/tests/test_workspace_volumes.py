"""作業場所の volume の宣言(deploy/k8s/workspace-volumes)が、決まり(同じ dir の README.md)の通りに組み立てられる事の検。

確かめる事:
  (1) 級 longhorn-workspace は replica 1・札 workspace の disk だけ(hot を名指さない)・attach した node へ寄せる(best-effort)・Pod 無しで
      結ばれる(Immediate)・既定の級でない・消して中身が消えない(Retain)・Flux が外しても消さない。
  (2) 級の backup の先と群が、同じ dir で宣言した BackupTarget と backup の RecurringJob を名指し、ほかの volume と同じ群 default も持つ。
  (3) backup の RecurringJob は 1 時間ごとに backup を取り、volume ごとに数を決めて残す。
  (4) worker が読む値(ConfigMap workspace-volume)は file と同じで、級を名指し、名の頭と長さの上限が Longhorn の volume の名の上限
      (40 字)に収まり、大きさは 10 GiB。
  (5) cloud の VPS の disk に札 workspace を付けない(この repo で disk の札を宣言している disk-share の宣言と組み立て)。
  (6) worker の組(Flux が当てる dir)がこの dir を組み立てに入れる。
失敗ケース(赤になる事の検): replica 2・札 hot の級・札の無い級・WaitForFirstConsumer・既定の級・Delete・宣言に無い BackupTarget・backup で
ない群・群 default の欠け・1 日 1 度の backup・残す数 0。
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
K8S = PACKAGE / "deploy" / "k8s"
HERE = K8S / "workspace-volumes"
VALUES_FILE = HERE / "workspace-volume.json"
SHARES = K8S / "disk-share" / "shares.yaml"

CLASS_NAME = "longhorn-workspace"
WORKSPACE_TAG = "workspace"
DEFAULT_GROUP = "default"
VALUES_CONFIGMAP = "workspace-volume"
NAMESPACE = "agent-worker"
PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"
DEFAULT_CLASS_ANNOTATION = "storageclass.kubernetes.io/is-default-class"
# Longhorn は 40 字を越える volume の名を、頭の 31 字と hash に縮めて別の名にする(2026-11-01 の dry-run)。
LONGHORN_NAME_LIMIT = 40
GIB = 1024**3

Manifest = dict[str, object]


def _build(directory: Path) -> list[Manifest]:
    """directory を kubectl kustomize で組み立てた物(Flux が当てる物と同じ)。kubectl が無ければ赤(飛ばさない)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    done = subprocess.run(
        [kubectl, "kustomize", str(directory)], capture_output=True, text=True, timeout=25, check=False
    )
    assert done.returncode == 0, f"kubectl kustomize {directory} が失敗した: {done.stderr.strip()}"
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc is not None]


@pytest.fixture(scope="module")
def workers() -> list[Manifest]:
    return _build(K8S / "workers")


@pytest.fixture(scope="module")
def declared() -> list[Manifest]:
    return _build(HERE)


def _of_kind(docs: list[Manifest], kind: str) -> list[Manifest]:
    return [doc for doc in docs if doc["kind"] == kind]


def _one_class(docs: list[Manifest]) -> Manifest:
    found = [doc for doc in _of_kind(docs, "StorageClass") if doc["metadata"]["name"] == CLASS_NAME]
    assert len(found) == 1, f"級 {CLASS_NAME} がちょうど 1 つ無い: {len(found)}"
    return found[0]


def _groups(storage_class: Manifest) -> set[str]:
    """級の recurringJobSelector が名指す群の名(群でない job の名指しは決まりの外なので数えない)。"""
    selector = json.loads(str(storage_class["parameters"].get("recurringJobSelector", "[]")))
    return {str(item["name"]) for item in selector if item.get("isGroup")}


def class_problems(storage_class: Manifest) -> list[str]:
    """級 1 つが決まり(1)を外れている所。"""
    params = storage_class.get("parameters", {})
    annotations = storage_class["metadata"].get("annotations", {})
    tags = [tag for tag in str(params.get("diskSelector", "")).split(",") if tag]
    checks = (
        (storage_class.get("provisioner") == "driver.longhorn.io", "Longhorn の級でない"),
        (params.get("numberOfReplicas") == "1", "replica が 1 でない"),
        (tags == [WORKSPACE_TAG], "札 workspace の disk だけに置かない"),
        (params.get("dataLocality") == "best-effort", "attach した node へ寄せない"),
        (storage_class.get("volumeBindingMode") == "Immediate", "Pod 無しで結ばれない"),
        (annotations.get(DEFAULT_CLASS_ANNOTATION) == "false", "既定の級を名乗りうる"),
        (storage_class.get("reclaimPolicy") == "Retain", "消すと中身が消える"),
        (annotations.get(PRUNE_ANNOTATION) == "disabled", "Flux が外すと消える"),
    )
    return [problem for holds, problem in checks if not holds]


def backup_problems(storage_class: Manifest, docs: list[Manifest]) -> list[str]:
    """級の backup の先と群が、宣言した BackupTarget と backup の RecurringJob を名指しているか(決まり(2)(3))。"""
    targets = {doc["metadata"]["name"]: doc for doc in _of_kind(docs, "BackupTarget")}
    jobs = _of_kind(docs, "RecurringJob")
    groups = _groups(storage_class)
    backup_groups = {
        group for job in jobs if job["spec"].get("task") == "backup" for group in job["spec"].get("groups", [])
    }
    target = storage_class.get("parameters", {}).get("backupTargetName")
    target_kept = target in targets and (
        targets[target]["metadata"].get("annotations", {}).get(PRUNE_ANNOTATION) == "disabled"
    )
    checks = (
        (target in targets, f"backup の先 {target} が宣言に無い"),
        (target not in targets or target_kept, "Flux が外すと backup の先が消える"),
        (bool(groups & backup_groups), "backup の群に入らない"),
        (DEFAULT_GROUP in groups, "群 default(snapshot の片付けと trim)に入らない"),
    )
    used_jobs = [
        job
        for job in jobs
        if job["spec"].get("task") == "backup" and set(job["spec"].get("groups", [])) & groups
    ]
    job_checks = tuple(
        check
        for job in used_jobs
        for check in (
            (_hourly(str(job["spec"].get("cron", ""))), f"{job['metadata']['name']} が 1 時間ごとでない"),
            (int(job["spec"].get("retain", 0)) >= 1, f"{job['metadata']['name']} が backup を残さない"),
        )
    )
    return [problem for holds, problem in checks + job_checks if not holds]


def _hourly(cron: str) -> bool:
    """cron が毎時の決まった分か(`<分> * * * *`)。"""
    fields = cron.split()
    return len(fields) == 5 and fields[0].isdigit() and fields[1:] == ["*", "*", "*", "*"]


def test_class_is_one_replica_on_workspace_disks_near_the_attach(declared: list[Manifest]) -> None:
    assert class_problems(_one_class(declared)) == []


def test_class_backs_up_hourly_to_the_declared_target(declared: list[Manifest]) -> None:
    assert backup_problems(_one_class(declared), declared) == []


def _mutated(storage_class: Manifest, **params: object) -> Manifest:
    changed = copy.deepcopy(storage_class)
    for key, value in params.items():
        if key in ("volumeBindingMode", "reclaimPolicy"):
            changed[key] = value
        else:
            changed["parameters"][key] = value
    return changed


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"numberOfReplicas": "2"}, "replica が 1 でない"),
        ({"diskSelector": "hot"}, "札 workspace の disk だけに置かない"),
        ({"diskSelector": "workspace,hot"}, "札 workspace の disk だけに置かない"),
        ({"diskSelector": ""}, "札 workspace の disk だけに置かない"),
        ({"dataLocality": "disabled"}, "attach した node へ寄せない"),
        ({"volumeBindingMode": "WaitForFirstConsumer"}, "Pod 無しで結ばれない"),
        ({"reclaimPolicy": "Delete"}, "消すと中身が消える"),
    ],
    ids=["replica-2", "hot", "workspace-and-hot", "no-tag", "no-locality", "wait-for-pod", "delete"],
)
def test_class_out_of_the_rules_is_named(declared: list[Manifest], change: dict[str, object], problem: str) -> None:
    assert problem in class_problems(_mutated(_one_class(declared), **change))


def test_default_class_is_named(declared: list[Manifest]) -> None:
    changed = copy.deepcopy(_one_class(declared))
    changed["metadata"]["annotations"][DEFAULT_CLASS_ANNOTATION] = "true"
    assert "既定の級を名乗りうる" in class_problems(changed)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"backupTargetName": "default"}, "backup の先 default が宣言に無い"),
        ({"recurringJobSelector": '[{"name":"default","isGroup":true}]'}, "backup の群に入らない"),
        (
            {"recurringJobSelector": '[{"name":"workspace-backup","isGroup":true}]'},
            "群 default(snapshot の片付けと trim)に入らない",
        ),
    ],
    ids=["undeclared-target", "no-backup-group", "no-default-group"],
)
def test_backup_out_of_the_rules_is_named(declared: list[Manifest], change: dict[str, object], problem: str) -> None:
    assert problem in backup_problems(_mutated(_one_class(declared), **change), declared)


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [("cron", "17 3 * * *", "が 1 時間ごとでない"), ("retain", 0, "が backup を残さない")],
    ids=["daily", "keeps-none"],
)
def test_backup_job_out_of_the_rules_is_named(declared: list[Manifest], field: str, value: object, problem: str) -> None:
    docs = copy.deepcopy(declared)
    for job in _of_kind(docs, "RecurringJob"):
        if job["spec"].get("task") == "backup":
            job["spec"][field] = value
    assert any(found.endswith(problem) for found in backup_problems(_one_class(docs), docs))


def test_values_name_the_class_and_fit_longhorn_names(declared: list[Manifest]) -> None:
    values = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
    assert values["storageClass"] == _one_class(declared)["metadata"]["name"]
    assert values["nameMaxLength"] == LONGHORN_NAME_LIMIT
    assert values["namePrefix"] == values["namePrefix"].lower()
    assert len(values["namePrefix"]) < values["nameMaxLength"]
    assert values["sizeBytes"] == 10 * GIB
    assert 0 < values["warmHours"] < values["keepDays"] * 24
    assert values["spareVolumes"] >= 1


def test_values_reach_the_worker_as_the_fixed_named_configmap(workers: list[Manifest]) -> None:
    found = [
        doc
        for doc in _of_kind(workers, "ConfigMap")
        if doc["metadata"]["name"] == VALUES_CONFIGMAP and doc["metadata"].get("namespace") == NAMESPACE
    ]
    assert len(found) == 1, f"ConfigMap {NAMESPACE}/{VALUES_CONFIGMAP} がちょうど 1 つ無い: {len(found)}"
    assert json.loads(found[0]["data"][VALUES_FILE.name]) == json.loads(VALUES_FILE.read_text(encoding="utf-8"))


def test_workers_apply_the_class_and_its_backup(workers: list[Manifest]) -> None:
    """Flux が当てる worker の組に、級と backup の先と job が在る(この dir を resources から外していない)。"""
    names = {(doc["kind"], doc["metadata"]["name"]) for doc in workers}
    assert {("StorageClass", CLASS_NAME), ("BackupTarget", "garage-workspace"), ("RecurringJob", "workspace-backup")} <= names


def test_no_vps_disk_carries_the_workspace_tag(workers: list[Manifest]) -> None:
    """cloud の VPS の disk(disk-share の宣言の機体)に札 workspace を付けない — 宣言と、Flux が当てる Longhorn の Node の両方で。"""
    shares = yaml.safe_load(SHARES.read_text(encoding="utf-8"))
    declared_tags = {
        node: list(entry.get("longhornDiskTags", [])) for node, entry in shares["nodes"].items()
    }
    assert declared_tags, "disk-share の宣言に機体が無い"
    assert {node: tags for node, tags in declared_tags.items() if WORKSPACE_TAG in tags} == {}
    applied = {
        (doc["metadata"]["name"], disk): list(spec.get("tags", []))
        for doc in workers
        if doc["kind"] == "Node" and str(doc["apiVersion"]).startswith("longhorn.io/")
        for disk, spec in doc["spec"]["disks"].items()
    }
    assert applied, "worker の組に Longhorn の Node が無い"
    assert {key: tags for key, tags in applied.items() if WORKSPACE_TAG in tags} == {}
