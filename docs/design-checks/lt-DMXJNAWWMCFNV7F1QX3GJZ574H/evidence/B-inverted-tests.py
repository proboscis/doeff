"""盲検 B の違反の検を、直した版の正しい形(読み手が自分の側だけで木を加工する)に当てた物 — 置き場の履歴で木が変わらない事を見る。"""
import ast
from pathlib import Path
import pytest
pytest.importorskip("hy"); pytest.importorskip("doeff_hy")
from doeff_effect_analyzer import hy_expansion  # noqa: E402
from doeff_effect_analyzer import program_effects as pe  # noqa: E402

CHECKED = """
(require doeff-hy.macros [defk <-])
(defk checked [x]
  {:pre [(: x int) (check > x 0)] :post [(: % int)]}
  x)
"""

@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "trees"; monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(d)); return d

def counted(monkeypatch):
    seen, real = [], hy_expansion._expand_hy
    monkeypatch.setattr(hy_expansion, "_expand_hy", lambda s, f, m: seen.append(m) or real(s, f, m))
    return seen

def a_new_process():
    for memo in (pe._MODULE_CACHE, pe._DEFINITION_INDEX, pe._BODY_FACTS, pe._BODY_NODES): memo.clear()

def test_v1_the_reader_reshapes_the_tree_an_earlier_version_stored(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    raw = hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    assert "_hy_gensym_" in ast.dump(raw.tree)
    hy_expansion._store_expansion(hy_expansion._expansion_key(CHECKED, "c").place(cache_dir), raw)
    seen = counted(monkeypatch)
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert seen == []
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off"); a_new_process()
    cold = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert warm == cold   # B の v1 は warm != cold(置き場の履歴で木が変わる)を違反とした

def test_v2_a_reverted_reader_reads_the_tree_as_it_was_expanded(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    seen = counted(monkeypatch)
    with_d1 = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    monkeypatch.setattr(pe, "_reader_digest", lambda: "reader=D1 reverted")
    monkeypatch.setattr(pe, "_canonical_gensyms", lambda expanded: expanded)
    a_new_process()
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    stored = hy_expansion._stored_expansion(hy_expansion._expansion_key(CHECKED, "c").place(cache_dir))
    assert seen == ["c"]
    assert stored is not None and warm == ast.dump(stored.tree)   # 展開した形のまま(B の v2 は D1 の形を読んだ)
    assert warm != with_d1
