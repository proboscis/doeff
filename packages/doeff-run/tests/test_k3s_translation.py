"""k3s の handler の純な訳(cluster の無い機体でも走る — 失敗ケース先頭)。

k3s の handler は、守れない設定を断り、RunSpec を Pod と Secret の宣言へ訳し、Pod の答え(JSON)から状態と出来事を引く。
ここはその訳だけを、cluster を呼ばずに確かめる。cluster を呼ぶ筋書きは契約の検(test_contract.hy の解釈器 k3s)。

Pod の形は実際の k3s(v1.34)で確かめた物: 止めた Pod の跡は finalizer で残り、起きなかった Pod は削除の後 terminated の
ContainerStatusUnknown(startedAt が null)に、命令の無い Pod は StartError(startedAt が 1970-01-01)になる。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff_run import (
    K3sRunHost,
    ResourceLimits,
    RunHandle,
    RunLive,
    RunPending,
    RunSpec,
    RunStopped,
    StopCause,
    VolumeMount,
)
from doeff_run._k3s import (
    ended_annotation,
    k8s_time,
    pod_manifest,
    read_pod,
    secret_manifest,
    secret_name_of,
    unsupported_of,
    watch_script,
)

IMAGE = "registry.invalid/app@sha256:" + "a" * 64
HANDLE = RunHandle(name="contract-run", run_id="k3s-0123456789abcdef")
HOST = K3sRunHost(namespace="doeff-run-contract", host_id="h1")
STARTED = "2026-10-31T16:51:26Z"
FINISHED = "2026-10-31T16:51:53Z"
ASKED = datetime(2026, 10, 31, 16, 51, 50, 250000, tzinfo=UTC)
NEVER = "2026-10-31T16:52:02Z"


def pod(
    *,
    state: dict | None = None,
    phase: str = "Pending",
    ended: dict | None = None,
    deleting: bool = False,
    node: str | None = "helios",
    conditions: list[dict] | None = None,
    status_reason: str = "",
) -> dict:
    """Pod の答え(JSON)の、状態の読みが見る所だけ。state が None なら containerStatuses が無い(まだ kubelet が報告しない)。"""
    metadata: dict = {
        "name": HANDLE.name,
        "uid": "u-1",
        "resourceVersion": "100",
        "creationTimestamp": "2026-10-31T16:51:22Z",
        "labels": {"doeff-run.run-id": HANDLE.run_id},
    }
    if deleting:
        metadata["deletionTimestamp"] = FINISHED
    if ended is not None:
        metadata["annotations"] = {"doeff-run/ended": ended_annotation(**ended)}
    status: dict = {"phase": phase, "conditions": conditions or []}
    if status_reason:
        status["reason"] = status_reason
    if state is not None:
        status["containerStatuses"] = [{"name": "run", "state": state}]
    spec: dict = {"nodeName": node} if node else {}
    return {"metadata": metadata, "spec": spec, "status": status}


def terminated(exit_code: int, *, started: str | None = STARTED, finished: str | None = FINISHED, reason: str = "Error", message: str = "") -> dict:
    done: dict = {"exitCode": exit_code, "reason": reason, "startedAt": started, "finishedAt": finished}
    if message:
        done["message"] = message
    return {"terminated": done}


def waiting(reason: str, message: str = "") -> dict:
    return {"waiting": {"reason": reason, "message": message}}


# --- 守れない設定 -------------------------------------------------------------------------------------------


def test_a_volume_source_that_is_a_path_is_unsupported() -> None:
    # k3s の volume は PVC の名で指す — path を hostPath に読む道は作らない(node の dir を会話に見せてしまう)。
    spec = RunSpec(image=IMAGE, volumes=(VolumeMount(source="/srv/data", target="/data"),))
    assert "PVC" in unsupported_of(spec)


def test_a_volume_source_that_is_not_a_resource_name_is_unsupported() -> None:
    spec = RunSpec(image=IMAGE, volumes=(VolumeMount(source="Data_V1", target="/data"),))
    assert "PVC" in unsupported_of(spec)


def test_env_too_big_for_a_secret_is_unsupported() -> None:
    # env は Secret に置く(Secret は 1 MiB まで)— 入らない量は走らせる前に断る。
    spec = RunSpec(image=IMAGE, env={"BLOB": "x" * (600 * 1024)})
    assert "env" in unsupported_of(spec)


def test_a_plain_spec_is_supported() -> None:
    spec = RunSpec(
        image=IMAGE,
        env={"A": "1"},
        volumes=(VolumeMount(source="conv-1.data", target="/data", read_only=True),),
        limits=ResourceLimits(cpu_millis=500, memory_bytes=1024 * 1024),
    )
    assert unsupported_of(spec) is None


# --- Pod と Secret の宣言 -----------------------------------------------------------------------------------


def test_env_values_never_appear_in_the_pod_manifest() -> None:
    # env は資格を運びうる。会話は ServiceAccount で Pod を読めるので、Pod の宣言に平文で書かない(Secret に置いて名で引く)。
    spec = RunSpec(image=IMAGE, env={"TOKEN": "s3cret-value", "A_NAME": "x"})
    manifest = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, spec)
    assert "s3cret-value" not in json.dumps(manifest)
    env = manifest["spec"]["containers"][0]["env"]
    assert [item["name"] for item in env] == ["A_NAME", "TOKEN"]
    assert env[1]["valueFrom"]["secretKeyRef"] == {"name": secret_name_of(HANDLE.run_id), "key": "TOKEN"}


def test_the_pod_carries_no_cluster_identity_and_is_not_restarted() -> None:
    # 隔離: Pod に ServiceAccount の token を渡さず(中の process が cluster の API を呼べない)、勝手に再起動しない
    # (止まった跡の終了 code を、再起動が上書きしない)。
    spec = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, RunSpec(image=IMAGE))["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["restartPolicy"] == "Never"


def test_the_pod_keeps_its_trace_until_it_is_removed() -> None:
    # 止めた Pod の跡(終了 code)は片付けるまで残す約束 — Pod は削除されても finalizer で残る。
    manifest = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, RunSpec(image=IMAGE))
    assert manifest["metadata"]["finalizers"] == ["doeff-run/keep-trace"]
    assert manifest["metadata"]["name"] == HANDLE.name
    assert manifest["metadata"]["labels"]["doeff-run.run-id"] == HANDLE.run_id
    assert manifest["metadata"]["labels"]["doeff-run.host"] == "h1"


def test_the_command_replaces_the_image_default_arguments_and_keeps_its_entrypoint() -> None:
    # docker create <image> <命令...> と同じ: 命令は args(ENTRYPOINT は残す)。空なら image の既定。
    with_command = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, RunSpec(image=IMAGE, command=("contract-workload", "live")))
    container = with_command["spec"]["containers"][0]
    assert container["args"] == ["contract-workload", "live"] and "command" not in container
    default = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, RunSpec(image=IMAGE))["spec"]["containers"][0]
    assert "args" not in default and default["image"] == IMAGE


def test_limits_become_equal_requests_and_limits_and_volumes_become_claims() -> None:
    spec = RunSpec(
        image=IMAGE,
        volumes=(VolumeMount(source="conv-1", target="/work"), VolumeMount(source="conv-1", target="/ro", read_only=True)),
        limits=ResourceLimits(cpu_millis=1500, memory_bytes=64 * 1024 * 1024),
    )
    manifest = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, spec)
    resources = manifest["spec"]["containers"][0]["resources"]
    assert resources["limits"] == resources["requests"] == {"cpu": "1500m", "memory": str(64 * 1024 * 1024)}
    mounts = manifest["spec"]["containers"][0]["volumeMounts"]
    assert [(m["mountPath"], m.get("readOnly", False)) for m in mounts] == [("/work", False), ("/ro", True)]
    claims = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in manifest["spec"]["volumes"]}
    assert [claims[m["name"]] for m in mounts] == ["conv-1", "conv-1"]


def test_a_spec_without_limits_or_volumes_declares_neither() -> None:
    container = pod_manifest(HOST, HANDLE.name, HANDLE.run_id, RunSpec(image=IMAGE))["spec"]["containers"][0]
    assert "resources" not in container and "volumeMounts" not in container


def test_the_secret_is_owned_by_the_pod_so_it_goes_when_the_pod_goes() -> None:
    spec = RunSpec(image=IMAGE, env={"TOKEN": "s3cret-value"})
    manifest = secret_manifest(HOST, HANDLE.run_id, spec, HANDLE.name, "u-1")
    assert manifest["metadata"]["name"] == secret_name_of(HANDLE.run_id)
    assert manifest["stringData"] == {"TOKEN": "s3cret-value"}
    owner = manifest["metadata"]["ownerReferences"][0]
    assert (owner["kind"], owner["name"], owner["uid"]) == ("Pod", HANDLE.name, "u-1")


# --- Pod の答えから状態と出来事 -----------------------------------------------------------------------------


def test_a_running_pod_is_live_since_it_started() -> None:
    state, events, settle = read_pod(HANDLE, pod(state={"running": {"startedAt": STARTED}}, phase="Running"))
    assert state == RunLive() and [(e.seq, type(e.state)) for e in events] == [(1, RunLive)]
    assert events[0].at == datetime(2026, 10, 31, 16, 51, 26, tzinfo=UTC) and settle is None


def test_a_pod_that_ended_by_itself_is_stopped_as_exited_with_its_code() -> None:
    state, events, _ = read_pod(HANDLE, pod(state=terminated(3), phase="Failed"))
    assert state == RunStopped(exit_code=3, cause=StopCause.EXITED, detail="Error")
    assert [(e.seq, type(e.state)) for e in events] == [(1, RunLive), (2, RunStopped)]
    assert events[1].at == datetime(2026, 10, 31, 16, 51, 53, tzinfo=UTC)


def test_an_oom_kill_is_out_of_memory_even_after_a_stop_request() -> None:
    state, _, _ = read_pod(
        HANDLE, pod(state=terminated(137, reason="OOMKilled"), phase="Failed", ended={"cause": "requested", "at": ASKED}, deleting=True)
    )
    assert state.cause == StopCause.OUT_OF_MEMORY and state.exit_code == 137


def test_a_run_that_ended_after_the_stop_request_was_stopped_as_requested() -> None:
    ended = {"cause": "requested", "at": ASKED}
    state, events, _ = read_pod(HANDLE, pod(state=terminated(143), phase="Failed", ended=ended, deleting=True))
    assert state.cause == StopCause.REQUESTED and state.exit_code == 143
    assert [type(e.state) for e in events] == [RunLive, RunStopped]


def test_a_run_that_ended_before_the_stop_request_ended_by_itself() -> None:
    # 自分で終わった後に止めを頼まれた(止めは冪等 — 終わり方は変わらない)。
    ended = {"cause": "requested", "at": datetime(2026, 10, 31, 16, 59, 0, tzinfo=UTC)}
    state, _, _ = read_pod(HANDLE, pod(state=terminated(7), phase="Failed", ended=ended, deleting=True))
    assert state.cause == StopCause.EXITED and state.exit_code == 7


def test_a_pod_being_stopped_is_still_live_until_its_container_ends() -> None:
    # 止めの途中(猶予の間): 削除は頼まれたが、container はまだ走っている。
    state, _, _ = read_pod(HANDLE, pod(state={"running": {"startedAt": STARTED}}, phase="Running", ended={"cause": "requested", "at": ASKED}, deleting=True))
    assert state == RunLive()


def test_a_pod_stopped_before_it_began_is_requested_without_an_exit_code() -> None:
    # 起きる前に止めた Pod: kubelet は terminated の ContainerStatusUnknown(startedAt は null)にする。
    unknown = terminated(137, started=None, finished=None, reason="ContainerStatusUnknown", message="The container could not be located")
    state, events, _ = read_pod(HANDLE, pod(state=unknown, phase="Failed", ended={"cause": "requested", "at": ASKED}, deleting=True))
    assert state == RunStopped(exit_code=None, cause=StopCause.REQUESTED)
    assert [type(e.state) for e in events] == [RunStopped] and events[0].at == ASKED


def test_a_pod_stopped_before_it_was_scheduled_is_requested_at_once() -> None:
    # node の無い Pod は kubelet が報告しないので、削除の印が付いたら止まったと読む。
    state, events, _ = read_pod(HANDLE, pod(node=None, ended={"cause": "requested", "at": ASKED}, deleting=True))
    assert state == RunStopped(exit_code=None, cause=StopCause.REQUESTED) and len(events) == 1


@pytest.mark.parametrize("reason", ["ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"])
def test_an_image_that_cannot_be_pulled_never_began_and_asks_to_be_settled(reason: str) -> None:
    # 取り寄せられない image の Pod は backoff で待ち続ける — 起きなかった止まりと読み、Pod を止めて印を残す(settle)よう求める。
    state, events, settle = read_pod(HANDLE, pod(state=waiting(reason, "no such host")))
    assert state.cause == StopCause.NEVER_BEGAN and state.exit_code is None
    assert "no such host" in settle and "no such host" in state.detail and [type(e.state) for e in events] == [RunStopped]


def test_a_settled_pod_reads_the_recorded_reason_and_time() -> None:
    ended = {"cause": "never-began", "at": ASKED, "detail": "no such host"}
    state, events, settle = read_pod(HANDLE, pod(state=waiting("ImagePullBackOff", "other words"), ended=ended))
    assert state == RunStopped(exit_code=None, cause=StopCause.NEVER_BEGAN, detail="no such host") and settle is None
    assert events[0].at == ASKED
    # kubelet が削除の後に container を ContainerStatusUnknown に替えても、読みは変わらない。
    unknown = terminated(137, started=None, finished=None, reason="ContainerStatusUnknown")
    again, again_events, _ = read_pod(HANDLE, pod(state=unknown, phase="Failed", ended=ended, deleting=True))
    assert (again, again_events) == (state, events)


def test_a_command_that_cannot_start_never_began() -> None:
    # 命令の無い Pod は StartError(startedAt は 1970-01-01)— 起きなかった。
    start_error = terminated(128, started="1970-01-01T00:00:00Z", reason="StartError", message='exec: "x": executable file not found')
    state, events, settle = read_pod(HANDLE, pod(state=start_error, phase="Failed"))
    assert state.cause == StopCause.NEVER_BEGAN and state.exit_code is None and "executable file not found" in state.detail
    assert [type(e.state) for e in events] == [RunStopped] and settle is None


def test_a_missing_volume_claim_never_began_and_asks_to_be_settled() -> None:
    message = '0/8 nodes are available: persistentvolumeclaim "no-such-claim" not found. not found'
    unschedulable = [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": message}]
    state, _, settle = read_pod(HANDLE, pod(node=None, conditions=unschedulable))
    assert state.cause == StopCause.NEVER_BEGAN and "no-such-claim" in state.detail and "no-such-claim" in settle


def test_a_pod_that_cannot_be_placed_for_lack_of_room_stays_pending() -> None:
    # 空きが無くて置けない Pod は、後で置かれるかもしれない — 起きなかったとは言わない。
    message = "0/8 nodes are available: 8 Insufficient memory."
    unschedulable = [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": message}]
    state, events, settle = read_pod(HANDLE, pod(node=None, conditions=unschedulable))
    assert state == RunPending() and events == () and settle is None


@pytest.mark.parametrize("reason", ["ContainerCreating", "PodInitializing", "CreateContainerConfigError"])
def test_a_pod_that_is_still_getting_ready_is_pending(reason: str) -> None:
    # CreateContainerConfigError は Secret を作る前の短い間にも出る — Pending のまま待つ。
    state, events, settle = read_pod(HANDLE, pod(state=waiting(reason)))
    assert state == RunPending() and events == () and settle is None


def test_a_pod_without_container_status_is_pending() -> None:
    state, events, _ = read_pod(HANDLE, pod())
    assert state == RunPending() and events == ()


def test_a_failed_pod_without_container_status_never_began_with_the_reason() -> None:
    state, events, _ = read_pod(HANDLE, pod(phase="Failed", status_reason="Evicted"))
    assert state.cause == StopCause.NEVER_BEGAN and "Evicted" in state.detail and len(events) == 1


def test_the_events_of_a_stopped_pod_are_the_same_every_time() -> None:
    first = read_pod(HANDLE, pod(state=terminated(3), phase="Failed"))
    assert read_pod(HANDLE, pod(state=terminated(3), phase="Failed")) == first


# --- 時刻・印・読みの待ち ------------------------------------------------------------------------------------


def test_k8s_times_are_seconds_and_null_or_epoch_is_none() -> None:
    assert k8s_time(STARTED) == datetime(2026, 10, 31, 16, 51, 26, tzinfo=UTC)
    assert k8s_time(None) is None and k8s_time("1970-01-01T00:00:00Z") is None and k8s_time("0001-01-01T00:00:00Z") is None
    assert k8s_time("not a time") is None


def test_the_ended_annotation_round_trips_and_caps_a_long_detail() -> None:
    text = ended_annotation(cause="never-began", at=ASKED, detail="d" * 5000)
    loaded = json.loads(text)
    assert loaded["cause"] == "never-began" and len(loaded["detail"]) <= 500
    assert datetime.fromisoformat(loaded["at"]) == ASKED


def test_the_watch_asks_for_changes_of_that_pod_after_the_version_that_was_read() -> None:
    # 読んだ版から見張るので、読みと見張りの間に起きた変化も取りこぼさない(kubectl get -w は版を渡せないので --raw)。
    script = watch_script(HOST, HANDLE.name, "178999114", 2.2)
    assert script.startswith("exec head -n 1 < <(")
    assert "get --raw" in script and "/api/v1/namespaces/doeff-run-contract/pods" in script
    assert "watch=1" in script and "metadata.name%3Dcontract-run" in script
    assert "resourceVersion=178999114" in script and "timeoutSeconds=3" in script


# --- host ---------------------------------------------------------------------------------------------------


def test_the_host_refuses_an_empty_kubectl_command() -> None:
    with pytest.raises(ValueError, match="kubectl"):
        K3sRunHost(namespace="ns", kubectl=())


def test_the_host_refuses_a_namespace_that_is_not_a_resource_name() -> None:
    # namespace は API の path と命令に入る — 形を確かめてから使う。
    with pytest.raises(ValueError, match="namespace"):
        K3sRunHost(namespace="not/a-namespace")
