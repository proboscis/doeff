"""process の handler の純な訳(失敗ケース先頭)。

process の handler は、守れない設定を断り、image の名を dir に引き、子の PATH を組み、子の終了 code を docker と同じ数へ直す。
ここはその訳だけを、子 process を立てずに確かめる。子を立てる筋書きは契約の検(test_contract.hy)と process の検
(test_process_handler.hy)の解釈器 process。
"""

from __future__ import annotations

from collections.abc import Mapping

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff_run import ProcessRunHost, ResourceLimits, RunSpec, VolumeMount
from doeff_run._process import child_path, exit_code_of, image_dir_of, unsupported_of

TOOLBOX = "/opt/toolbox/bin"


def spec(
    *,
    env: Mapping[str, str] | None = None,
    volumes: tuple[VolumeMount, ...] = (),
    limits: ResourceLimits | None = None,
) -> RunSpec:
    return RunSpec(
        image="local/toolbox",
        command=("tool", "go"),
        env=env or {},
        volumes=volumes,
        limits=limits or ResourceLimits(),
    )


# --- 守れない設定 -------------------------------------------------------------------------------------------


def test_a_plain_spec_is_kept() -> None:
    assert unsupported_of(spec()) is None


def test_a_volume_is_not_kept() -> None:
    reason = unsupported_of(spec(volumes=(VolumeMount(source="data", target="/data"),)))
    assert reason is not None and "volume" in reason and "data" in reason


def test_a_memory_limit_is_not_kept() -> None:
    reason = unsupported_of(spec(limits=ResourceLimits(memory_bytes=64 * 1024 * 1024)))
    assert reason is not None and "メモリ" in reason


def test_a_cpu_limit_is_not_kept() -> None:
    reason = unsupported_of(spec(limits=ResourceLimits(cpu_millis=500)))
    assert reason is not None and "CPU" in reason


def test_the_reason_never_carries_an_env_value() -> None:
    reason = unsupported_of(spec(env={"TOKEN": "s3cret-value"}, limits=ResourceLimits(cpu_millis=500)))
    assert reason is not None and "s3cret-value" not in reason


# --- 終了 code ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "shell_code"),
    [(0, 0), (3, 3), (255, 255), (-9, 137), (-15, 143), (-6, 134)],
)
def test_a_signal_death_becomes_128_plus_the_signal_number(returncode: int, shell_code: int) -> None:
    assert exit_code_of(returncode) == shell_code


# --- image の dir と PATH -----------------------------------------------------------------------------------


def test_an_image_is_read_through_the_hosts_table() -> None:
    host = ProcessRunHost(images={"local/toolbox": TOOLBOX})
    assert image_dir_of(host, spec()) == TOOLBOX
    assert image_dir_of(host, RunSpec(image="local/other", command=("x",))) is None


def test_the_image_directory_leads_the_path() -> None:
    assert child_path(TOOLBOX, "/usr/bin:/bin") == f"{TOOLBOX}:/usr/bin:/bin"


def test_a_missing_path_still_gets_the_image_directory_and_the_system_defaults() -> None:
    path = child_path(TOOLBOX, None)
    assert path.startswith(f"{TOOLBOX}:") and "/usr/bin" in path.split(":") and "/bin" in path.split(":")


# --- host の作り -------------------------------------------------------------------------------------------


def test_a_host_copies_its_table() -> None:
    images = {"local/toolbox": TOOLBOX}
    host = ProcessRunHost(images=images)
    images["local/other"] = "/elsewhere"
    assert image_dir_of(host, RunSpec(image="local/other", command=("x",))) is None


@pytest.mark.parametrize("images", [{"": TOOLBOX}, {"has space": TOOLBOX}, {"local/toolbox": ""}, {"local/toolbox": "relative/bin"}])
def test_a_host_refuses_a_table_that_cannot_name_a_directory(images: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="images"):
        ProcessRunHost(images=images)


def test_a_host_refuses_a_table_that_is_not_a_mapping_of_strings() -> None:
    with pytest.raises(TypeError, match="images"):
        ProcessRunHost(images=[("local/toolbox", TOOLBOX)])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="images"):
        ProcessRunHost(images={"local/toolbox": 3})  # type: ignore[dict-item]


def test_a_new_host_has_no_live_child() -> None:
    assert ProcessRunHost(images={}).live_pids == ()
