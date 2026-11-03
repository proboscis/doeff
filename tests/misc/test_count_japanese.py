"""The Japanese-line count (``scripts/count_japanese.py``) and its daily stage."""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType

import tomllib

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "count_japanese.py"
LAND_QUEUE = ROOT / ".agents" / "land-queue.toml"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("count_japanese", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


count_japanese = _load_script()


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\n# 日本語の注\ny = 2\n# カタカナ\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("English only\n", encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\0\x89PNG" + "日本".encode())
    (tmp_path / "publications").mkdir()
    (tmp_path / "publications" / "c.md").write_text("記事\n", encoding="utf-8")
    return tmp_path


def test_counts_lines_with_japanese_and_skips_binary_and_publications(
    tmp_path: Path, capsys
) -> None:
    assert count_japanese.main([str(_tree(tmp_path))]) == 0

    out = capsys.readouterr().out
    assert "**Total: 2 lines in 1 files**" in out
    assert "| `.py` | 2 | 1 |" in out


def test_fail_on_any_exits_1_only_when_the_total_is_not_zero(tmp_path: Path) -> None:
    assert count_japanese.main([str(_tree(tmp_path)), "--fail-on-any"]) == 1

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert count_japanese.main([str(clean), "--fail-on-any"]) == 0


def test_the_daily_verification_runs_the_count_as_a_stage_that_does_not_stop_the_rest() -> None:
    stages = tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]
    stage = next((s for s in stages if s["name"] == "japanese"), None)
    assert stage is not None, [s["name"] for s in stages]

    assert "scripts/count_japanese.py" in shlex.split(stage["run"])
    assert not stage.get("blocks_rest")
    assert stage["pytest"] is False
