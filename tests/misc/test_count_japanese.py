"""The Japanese-line count (``scripts/count_japanese.py``)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "count_japanese.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("count_japanese", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


count_japanese = _load_script()


# Japanese letters for the fixtures, written as escapes so that this file stays English: a kanji word (U+65E5 U+672C, "Japan"),
# a katakana word (U+30AB U+30BF U+30AB U+30CA, "katakana") and a kanji word (U+8A18 U+4E8B, "article").
KANJI = "\u65e5\u672c"
KATAKANA = "\u30ab\u30bf\u30ab\u30ca"
ARTICLE = "\u8a18\u4e8b"


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text(f"x = 1\n# {KANJI} note\ny = 2\n# {KATAKANA}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("English only\n", encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\0\x89PNG" + KANJI.encode())
    (tmp_path / "publications").mkdir()
    (tmp_path / "publications" / "c.md").write_text(f"{ARTICLE}\n", encoding="utf-8")
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
