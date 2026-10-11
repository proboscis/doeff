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
    seen, real = [], pe._expand_hy
    monkeypatch.setattr(pe, "_expand_hy", lambda s, f, m: seen.append(m) or real(s, f, m))
    return seen

def a_new_process():
    for memo in (pe._MODULE_CACHE, pe._DEFINITION_INDEX, pe._BODY_FACTS, pe._BODY_NODES): memo.clear()

def test_v1_d1_reads_the_tree_the_previous_reader_stored(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")          # 長い run と同じく gensym の数えを進める
    raw = hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    assert "_hy_gensym_" in ast.dump(raw.tree)                 # 前提
    before = ast.dump(raw.tree)
    # D1 の前の _expanded が置く物そのもの。D1 は hy_expansion.py に触れないので鍵は同じ
    hy_expansion._store_expansion(hy_expansion._expansion_key(CHECKED, "c").place(cache_dir), raw)
    seen = counted(monkeypatch)
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert seen == [] and warm == before                       # 展開 0 回・前の読み手の木
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off"); a_new_process()
    cold = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert warm != cold                                        # 同じ code・同じ source で木が置き場の履歴で変わる

def test_v2_a_reverted_reader_reads_the_tree_d1_renumbered(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    seen = counted(monkeypatch)
    with_d1 = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    monkeypatch.setattr(pe, "_reader_digest", lambda: "reader=D1 reverted")   # revert した読み手
    monkeypatch.setattr(pe, "_canonical_gensyms", lambda expanded: expanded)
    a_new_process()
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert seen == ["c"] and warm == with_d1                   # 展開せず、D1 が形を決めた木を読む
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off"); a_new_process()
    assert warm != ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
