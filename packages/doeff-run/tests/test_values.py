"""doeff-run の値の型と effect の作る時の検め(失敗ケース先頭)。

走らせる設定(RunSpec)・取っ手・状態・出来事・effect・fake の世界は、作る時に誤りを断る。断る事で、本物の handler(docker・
process・k3s)は検めずに値を信じて backend の呼びへ訳せる。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

import doeff_run
from doeff_run import (
    FakeBehavior,
    FakeRunWorld,
    ResourceLimits,
    RunEvent,
    RunEventPage,
    RunGone,
    RunHandle,
    RunLive,
    RunPending,
    RunReadEvents,
    RunRemove,
    RunSpec,
    RunStart,
    RunStatus,
    RunStop,
    RunStopped,
    StopCause,
    VolumeMount,
)

IMAGE = "registry.invalid/app@sha256:" + "a" * 64
NOW = datetime(2026, 10, 31, tzinfo=UTC)


# --- 取っ手と名 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["", "A", "-a", "a-", "a_b", "a.b", "a b", "a" * 64, "日本語", "a/b"],
)
def test_a_name_docker_or_kubernetes_would_refuse_is_refused_up_front(name: str) -> None:
    with pytest.raises(ValueError):
        RunHandle(name=name, run_id="r1")
    with pytest.raises(AssertionError):
        RunStart(name, RunSpec(image=IMAGE))


@pytest.mark.parametrize("name", ["a", "0", "agent-env-0", "a" * 63, "c-01j9z"])
def test_a_name_both_backends_accept_is_accepted(name: str) -> None:
    assert RunHandle(name=name, run_id="r1").name == name
    assert RunStart(name, RunSpec(image=IMAGE)).name == name


def test_a_handle_needs_a_run_id() -> None:
    with pytest.raises(ValueError):
        RunHandle(name="a", run_id="")


# --- 走らせる設定 ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("image", ["", " ", "a b", "a\nb", "a\x00b"])
def test_an_image_must_be_one_token(image: str) -> None:
    with pytest.raises(ValueError):
        RunSpec(image=image)


def test_the_command_is_a_tuple_of_strings() -> None:
    assert RunSpec(image=IMAGE, command=("sleep", "3")).command == ("sleep", "3")
    with pytest.raises(TypeError):
        RunSpec(image=IMAGE, command=("sleep", 3))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        RunSpec(image=IMAGE, command="sleep 3")  # type: ignore[arg-type]


@pytest.mark.parametrize("key", ["", "1A", "A=B", "A B", "A-B", "日本"])
def test_an_env_name_must_be_a_shell_name(key: str) -> None:
    with pytest.raises(ValueError):
        RunSpec(image=IMAGE, env={key: "v"})


def test_an_env_value_must_be_a_string_without_nul() -> None:
    with pytest.raises(TypeError):
        RunSpec(image=IMAGE, env={"A": 1})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        RunSpec(image=IMAGE, env={"A": "x\x00y"})


def test_the_env_is_copied_frozen_and_never_printed() -> None:
    source = {"TOKEN": "s3cr3t-value"}
    spec = RunSpec(image=IMAGE, env=source)
    source["TOKEN"] = "changed"
    assert spec.env["TOKEN"] == "s3cr3t-value"
    assert "s3cr3t-value" not in repr(spec)
    assert "TOKEN" not in repr(spec)
    with pytest.raises((TypeError, AttributeError)):
        spec.env["TOKEN"] = "x"  # type: ignore[index]


def test_a_spec_is_frozen_and_hashable() -> None:
    spec = RunSpec(image=IMAGE, command=("a",), env={"A": "1"})
    assert hash(spec) == hash(RunSpec(image=IMAGE, command=("a",), env={"A": "1"}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.image = "x"  # type: ignore[misc]


@pytest.mark.parametrize("target", ["", "work", "./work", "/", "/a/../b", "/a//b", "/a/"])
def test_a_volume_target_is_a_clean_absolute_path_below_root(target: str) -> None:
    with pytest.raises(ValueError):
        VolumeMount(source="vol-1", target=target)


def test_a_volume_needs_a_source_and_defaults_to_writable() -> None:
    with pytest.raises(ValueError):
        VolumeMount(source="", target="/work")
    assert VolumeMount(source="vol-1", target="/work").read_only is False
    assert VolumeMount(source="vol-1", target="/work", read_only=True).read_only is True


def test_two_volumes_cannot_share_a_target() -> None:
    first = VolumeMount(source="a", target="/work")
    second = VolumeMount(source="b", target="/work")
    with pytest.raises(ValueError):
        RunSpec(image=IMAGE, volumes=(first, second))
    assert RunSpec(image=IMAGE, volumes=(first, VolumeMount(source="b", target="/home"))).volumes[1].source == "b"


@pytest.mark.parametrize("field", ["cpu_millis", "memory_bytes"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_a_limit_is_a_positive_integer_or_absent(field: str, value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        ResourceLimits(**{field: value})
    assert getattr(ResourceLimits(), field) is None
    assert getattr(ResourceLimits(**{field: 1}), field) == 1


# --- 状態と出来事 ----------------------------------------------------------------------------------------

def test_a_stop_that_ran_has_an_exit_code_and_one_that_never_began_has_none() -> None:
    assert RunStopped(exit_code=0, cause=StopCause.EXITED).exit_code == 0
    assert RunStopped(exit_code=None, cause=StopCause.REQUESTED).exit_code is None
    assert RunStopped(exit_code=143, cause=StopCause.REQUESTED).exit_code == 143
    with pytest.raises(ValueError):
        RunStopped(exit_code=None, cause=StopCause.EXITED)
    with pytest.raises(ValueError):
        RunStopped(exit_code=None, cause=StopCause.OUT_OF_MEMORY)
    with pytest.raises(ValueError):
        RunStopped(exit_code=1, cause=StopCause.NEVER_BEGAN)
    with pytest.raises(ValueError):
        RunStopped(exit_code=True, cause=StopCause.EXITED)  # type: ignore[arg-type]


def test_states_are_values_that_compare_by_content() -> None:
    assert RunLive() == RunLive()
    assert RunStopped(exit_code=1, cause=StopCause.EXITED) != RunStopped(exit_code=2, cause=StopCause.EXITED)
    assert RunGone(detail="x") != RunGone(detail="y")
    assert RunPending() != RunLive()


def test_an_event_is_the_run_entering_a_state_and_pending_is_not_an_event() -> None:
    assert RunEvent(seq=1, at=NOW, state=RunLive()).seq == 1
    with pytest.raises(ValueError):
        RunEvent(seq=0, at=NOW, state=RunLive())
    with pytest.raises(ValueError):
        RunEvent(seq=1, at=NOW, state=RunPending())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RunEvent(seq=1, at=datetime(2026, 10, 31), state=RunLive())  # noqa: DTZ001 - 時刻帯の無い時刻を断る検


def test_a_page_carries_events_the_next_seq_and_the_state() -> None:
    event = RunEvent(seq=1, at=NOW, state=RunLive())
    page = RunEventPage(events=(event,), next_seq=1, state=RunLive())
    assert page.events == (event,)
    with pytest.raises(ValueError):
        RunEventPage(events=(event,), next_seq=0, state=RunLive())


# --- effect --------------------------------------------------------------------------------------------

HANDLE = RunHandle(name="a", run_id="r1")


def test_a_stop_grace_and_a_read_wait_are_not_negative() -> None:
    assert RunStop(HANDLE, 0.0).grace_seconds == 0.0
    with pytest.raises(AssertionError):
        RunStop(HANDLE, -1.0)
    assert RunReadEvents(HANDLE, 0, 0.0).wait_up_to == 0.0
    with pytest.raises(AssertionError):
        RunReadEvents(HANDLE, 0, -0.1)
    with pytest.raises(AssertionError):
        RunReadEvents(HANDLE, -1, 1.0)


def test_effects_take_a_handle_not_a_name() -> None:
    for effect in (RunStatus, RunRemove):
        with pytest.raises(AssertionError):
            effect("a")  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        RunStop("a", 1.0)  # type: ignore[arg-type]


# --- fake の世界 ---------------------------------------------------------------------------------------

def test_a_fake_behavior_refuses_values_that_cannot_happen() -> None:
    assert FakeBehavior().runs_for is None
    for bad in ({"begin_after": -0.1}, {"runs_for": 0.0}, {"runs_for": -1.0}, {"stop_takes": -1.0},
                {"refuses_to_begin": ""}, {"exit_code": True}, {"out_of_memory": True}):
        with pytest.raises(ValueError):
            FakeBehavior(**bad)
    assert FakeBehavior(runs_for=1.0, out_of_memory=True).out_of_memory is True


def test_a_fake_world_refuses_a_bad_capacity_or_behavior_function() -> None:
    FakeRunWorld(behavior_of=lambda spec: FakeBehavior(), capacity=1)
    for capacity in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            FakeRunWorld(behavior_of=lambda spec: FakeBehavior(), capacity=capacity)
    with pytest.raises(ValueError):
        FakeRunWorld(behavior_of=None)  # type: ignore[arg-type]


# --- 入口 ---------------------------------------------------------------------------------------------

def test_the_root_names_what_the_package_shows_and_nothing_else() -> None:
    assert {"RunStart", "RunStop", "RunRemove", "RunStatus", "RunReadEvents", "RunVanish", "fake_run_handler"} <= set(
        dir(doeff_run)
    )
    with pytest.raises(AttributeError):
        doeff_run.no_such_name  # noqa: B018 - 無い名は AttributeError
