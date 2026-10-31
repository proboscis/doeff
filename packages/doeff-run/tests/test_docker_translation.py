"""docker の handler の純な訳(docker の無い機体でも走る — 失敗ケース先頭)。

docker の handler は、守れない設定を断り、RunSpec を docker create の命令へ訳し、container の inspect の答えから状態と出来事を
引く。ここはその訳だけを、docker を呼ばずに確かめる。docker を呼ぶ筋書きは契約の検(test_contract.hy の解釈器 docker)。
"""

from __future__ import annotations

from datetime import UTC, datetime

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff_run import (
    DockerRunHost,
    ResourceLimits,
    RunGone,
    RunHandle,
    RunLive,
    RunPending,
    RunSpec,
    RunStopped,
    StopCause,
    VolumeMount,
)
from doeff_run._docker import create_argv, docker_time, read_container, unsupported_of, watch_script

IMAGE = "registry.invalid/app@sha256:" + "a" * 64
HANDLE = RunHandle(name="contract-run", run_id="docker-1")
CREATED = "2026-10-31T01:00:00.000000001Z"
STARTED = "2026-10-31T01:00:01.123456789Z"
FINISHED = "2026-10-31T01:00:05.5Z"
ZERO = "0001-01-01T00:00:00Z"


def inspect(status: str, *, started: str = ZERO, finished: str = ZERO, exit_code: int = 0, oom: bool = False, error: str = "") -> dict:
    return {
        "Id": "c0ffee",
        "Created": CREATED,
        "Config": {"Labels": {"doeff-run.run-id": HANDLE.run_id}},
        "State": {
            "Status": status,
            "StartedAt": started,
            "FinishedAt": finished,
            "ExitCode": exit_code,
            "OOMKilled": oom,
            "Error": error,
        },
    }


# --- 守れない設定 -------------------------------------------------------------------------------------------


def test_a_volume_source_that_is_a_path_is_unsupported() -> None:
    # docker は / を含む source を bind mount の path に読む — named volume しか約束しないので断る。
    spec = RunSpec(image=IMAGE, volumes=(VolumeMount(source="/srv/data", target="/data"),))
    assert "named volume" in unsupported_of(spec)


def test_a_volume_target_with_a_comma_is_unsupported() -> None:
    spec = RunSpec(image=IMAGE, volumes=(VolumeMount(source="data", target="/a,b"),))
    assert "," in unsupported_of(spec)


def test_a_memory_limit_below_the_docker_minimum_is_unsupported() -> None:
    spec = RunSpec(image=IMAGE, limits=ResourceLimits(memory_bytes=1024 * 1024))
    assert "下限" in unsupported_of(spec)


def test_a_plain_spec_is_supported() -> None:
    spec = RunSpec(
        image=IMAGE,
        volumes=(VolumeMount(source="data.v1", target="/data", read_only=True),),
        limits=ResourceLimits(cpu_millis=500, memory_bytes=64 * 1024 * 1024),
    )
    assert unsupported_of(spec) is None


# --- docker create の命令 -----------------------------------------------------------------------------------


def test_create_passes_env_names_but_never_values() -> None:
    # env の値は資格を運びうる — 命令の引数(ps に写る)に載せず、名だけを --env で渡す。
    spec = RunSpec(image=IMAGE, env={"TOKEN": "s3cret", "A_NAME": "x"})
    argv = create_argv(DockerRunHost(host_id="h1"), "contract-run", "docker-1", spec)
    assert "s3cret" not in " ".join(argv)
    assert argv[argv.index("--env") + 1] == "A_NAME"
    assert "TOKEN" in argv


def test_create_places_labels_mounts_limits_then_image_and_command() -> None:
    spec = RunSpec(
        image=IMAGE,
        command=("contract-workload", "live"),
        volumes=(VolumeMount(source="data", target="/data", read_only=True),),
        limits=ResourceLimits(cpu_millis=1500, memory_bytes=64 * 1024 * 1024),
    )
    argv = create_argv(DockerRunHost(host_id="h1"), "contract-run", "docker-1", spec)
    assert argv[:4] == ("docker", "create", "--name", "contract-run")
    assert "doeff-run.run-id=docker-1" in argv and "doeff-run.host=h1" in argv
    assert "type=volume,source=data,target=/data,readonly" in argv
    # swap を上限と同じに — 上限を越えた run は swap へ逃げず OOM で止まる。
    assert argv[argv.index("--memory") + 1] == argv[argv.index("--memory-swap") + 1] == str(64 * 1024 * 1024)
    assert argv[argv.index("--cpus") + 1] == "1.500"
    assert argv[-3:] == (IMAGE, "contract-workload", "live")


# --- inspect の答えから状態と出来事 ---------------------------------------------------------------------------


def test_an_unknown_container_status_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="知らない"):
        read_container(DockerRunHost(), HANDLE, inspect("bogus"))


def test_a_container_that_could_not_start_never_began() -> None:
    # 命令が無い container は docker が created のまま State.Error に訳を残す。
    state, events = read_container(DockerRunHost(), HANDLE, inspect("created", error="exec: no such file", exit_code=127))
    assert state == RunStopped(exit_code=None, cause=StopCause.NEVER_BEGAN, detail="exec: no such file")
    assert [type(event.state) for event in events] == [RunStopped]


def test_a_container_being_removed_is_gone() -> None:
    state, events = read_container(DockerRunHost(), HANDLE, inspect("removing", started=STARTED))
    assert isinstance(state, RunGone) and events == ()


def test_an_out_of_memory_kill_is_out_of_memory_even_after_a_stop_request() -> None:
    host = DockerRunHost()
    host.requested[HANDLE.run_id] = datetime(2026, 10, 31, 1, 0, 2, tzinfo=UTC)
    state, _ = read_container(host, HANDLE, inspect("exited", started=STARTED, finished=FINISHED, exit_code=137, oom=True))
    assert state.cause == StopCause.OUT_OF_MEMORY and state.exit_code == 137


def test_a_run_that_ended_before_the_stop_request_ended_by_itself() -> None:
    host = DockerRunHost()
    host.requested[HANDLE.run_id] = datetime(2026, 10, 31, 1, 0, 9, tzinfo=UTC)
    state, _ = read_container(host, HANDLE, inspect("exited", started=STARTED, finished=FINISHED, exit_code=3))
    assert state == RunStopped(exit_code=3, cause=StopCause.EXITED)


def test_a_run_that_ended_after_the_stop_request_was_stopped_as_requested() -> None:
    host = DockerRunHost()
    host.requested[HANDLE.run_id] = datetime(2026, 10, 31, 1, 0, 2, tzinfo=UTC)
    state, events = read_container(host, HANDLE, inspect("exited", started=STARTED, finished=FINISHED, exit_code=143))
    assert state == RunStopped(exit_code=143, cause=StopCause.REQUESTED)
    assert [(event.seq, type(event.state)) for event in events] == [(1, RunLive), (2, RunStopped)]
    assert events[0].at == datetime(2026, 10, 31, 1, 0, 1, 123456, tzinfo=UTC)
    assert events[1].at == datetime(2026, 10, 31, 1, 0, 5, 500000, tzinfo=UTC)


def test_a_created_container_is_pending_until_a_stop_is_requested() -> None:
    host = DockerRunHost()
    state, events = read_container(host, HANDLE, inspect("created"))
    assert state == RunPending() and events == ()
    host.requested[HANDLE.run_id] = datetime(2026, 10, 31, 1, 0, 2, tzinfo=UTC)
    state, _ = read_container(host, HANDLE, inspect("created"))
    assert state == RunStopped(exit_code=None, cause=StopCause.REQUESTED)


def test_a_running_container_is_live_since_it_started() -> None:
    state, events = read_container(DockerRunHost(), HANDLE, inspect("running", started=STARTED))
    assert state == RunLive() and [event.seq for event in events] == [1]


# --- 時刻と読みの待ち -----------------------------------------------------------------------------------------


def test_docker_times_keep_microseconds_and_zero_is_none() -> None:
    assert docker_time(STARTED) == datetime(2026, 10, 31, 1, 0, 1, 123456, tzinfo=UTC)
    assert docker_time(ZERO) is None
    assert docker_time("not a time") is None


def test_the_watch_waits_for_the_first_event_of_that_container_only() -> None:
    script = watch_script(DockerRunHost(), "c0ffee", 1761872400.25)
    assert script.startswith("exec head -n 1 < <(")
    assert "--since 1761872400.250000" in script and "container=c0ffee" in script
    assert "event=start" in script and "event=die" in script and "event=destroy" in script


def test_the_host_refuses_an_empty_docker_command() -> None:
    with pytest.raises(ValueError, match="docker"):
        DockerRunHost(docker=())
