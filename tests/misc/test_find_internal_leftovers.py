"""The ``--fail-on`` check of the inventory of what private tooling left in the tree.

``scripts/find_internal_leftovers.py`` lists six kinds of leftovers. With ``--fail-on 1,2,4`` it is
the ``leftovers`` stage of the daily verification of main (``.agents/land-queue.toml``): a hit of
those kinds that ``KEEP`` does not keep turns the run red. These tests pin when it fails and what it
names, on small trees that are not git work trees (the script then walks every file).
"""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "find_internal_leftovers.py"
LAND_QUEUE = ROOT / ".agents" / "land-queue.toml"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("find_internal_leftovers", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


leftovers = _load_script()


def _tree(tmp_path: Path, text: str) -> Path:
    (tmp_path / "notes.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_fail_on_names_an_unkept_tracker_id_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, "intro\nsee ki-0123456789ab for the reason\n")

    assert leftovers.main([str(root), "--jobs", "1", "--fail-on", "1,2,4"]) == 1

    err = capsys.readouterr().err
    assert "1 hit(s) of kind 1, 2, 4 not kept" in err
    assert "notes.md:2  kind 2 (ki- (card))  ki-0123456789ab" in err


def test_fail_on_passes_a_tree_without_those_kinds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, "the zeus machine runs the check\n")

    assert leftovers.main([str(root), "--jobs", "1", "--fail-on", "1,2,4"]) == 0
    assert capsys.readouterr().err == ""


def test_a_kept_hit_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path, "see ki-0123456789ab\n")
    monkeypatch.setattr(
        leftovers, "KEEP", (leftovers.Keep("notes.md", "2", "ki- (card)", "a test fake"),)
    )

    assert leftovers.main([str(root), "--jobs", "1", "--fail-on", "2"]) == 0


def test_without_fail_on_the_exit_status_stays_0(tmp_path: Path) -> None:
    root = _tree(tmp_path, "see ki-0123456789ab\n")

    assert leftovers.main([str(root), "--jobs", "1"]) == 0


@pytest.mark.parametrize("value", ["7", "1,x", ","])
def test_fail_on_rejects_a_value_that_is_not_kind_keys(tmp_path: Path, value: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        leftovers.main([str(tmp_path), "--jobs", "1", "--fail-on", value])

    assert stopped.value.code == 2


def test_the_daily_verification_runs_the_check_as_a_stage_that_does_not_stop_the_rest() -> None:
    stages = tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]
    stage = next((s for s in stages if s["name"] == "leftovers"), None)
    assert stage is not None, [s["name"] for s in stages]

    words = shlex.split(stage["run"])
    assert words[words.index("scripts/find_internal_leftovers.py") + 1 :] == ["--fail-on", "1,2,4"]
    assert not stage.get("blocks_rest")
    assert stage["pytest"] is False
