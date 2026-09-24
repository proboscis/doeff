"""E5: 事前の主張 S1 / S3 / S4 の最小実験(pins.py の試作の固定のテストを使う)。"""
import shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/tmp/ki08ec-proto")
import pins
T = Path("/tmp/ki08ec-proto/tree"); decl = T / "land-queue.proposed.toml"
files = Path("/tmp/ki08ec-proto/files.txt").read_text().splitlines()
def show(label, errs): print(f"== {label}: {'緑' if not errs else '赤 ' + ' / '.join(errs)}")
text = decl.read_text()
with tempfile.TemporaryDirectory() as d:
    # S1: 日次の宿を zeus から別の node へ(全段の前置きを変える)
    p = Path(d, "s1.toml"); p.write_text(text.replace("--node zeus", "--node hera")); show("S1 陽性: 全段の --node を hera へ(宣言の pin)", pins.pin1(p))
    # S4: rust の段だけ別の node へ
    lines = text.splitlines(); lines = [l.replace("--node zeus", "--node hera") if '"rust"' in l else l for l in lines]
    p = Path(d, "s4.toml"); p.write_text("\n".join(lines)); show("S4 陽性: rust の段だけ --node hera(宣言の pin)", pins.pin1(p))
    # S1/S4/S5 陰性: rust の段を build の段の成果に頼らせる(make sync を抜く)
    lines = text.splitlines(); lines = [l.replace("make sync && ", "", 1) if '"rust"' in l else l for l in lines]
    p = Path(d, "s4neg.toml"); p.write_text("\n".join(lines)); show("S1/S4/S5 陰性: rust の段から make sync を抜く(宣言の pin)", pins.pin1(p))
    # S0 陰性の補: root と packages を 1 段へ畳む
    import re
    p = Path(d, "merge.toml"); p.write_text(text.replace("uv run --no-sync pytest -q -m 'not e2e'\\\"\" },", "uv run --no-sync pytest -q -m 'not e2e' \\u0026\\u0026 make test-packages\\\"\" },", 1)); show("S0 陰性: root の段に make test-packages も載せる(宣言の pin)", pins.pin1(p))
# S3 (a) 陽性: 新しい package を足す(宣言も Makefile も編集しない)
work = Path(tempfile.mkdtemp()); shutil.copytree(T, work / "t", symlinks=True); W = work / "t"
(W / "packages/doeff-newpkg/tests").mkdir(parents=True); (W / "packages/doeff-newpkg/tests/test_new.py").write_text("def test_new():\n    assert True\n")
files_a = files + ["packages/doeff-newpkg/tests/test_new.py"]
show("S3(a) 陽性: 新しい package doeff-newpkg(網羅)", pins.pin2(W, "Makefile.proposed"))
show("S3(a) 陽性: 新しい package doeff-newpkg(完全性)", pins.pin3(W, files_a, pins.EXCLUDED))
# S3 (c) 陰性: 母集団の外にテストを置く
files_c = files + ["packages/doeff-agents/scripts/test_x.py"]
show("S3(c) 陰性: packages/doeff-agents/scripts/test_x.py を足す(完全性)", pins.pin3(T, files_c, pins.EXCLUDED))
# A の副次(package が自前の pytest 設定を持つ)陰性
pp = W / "packages/doeff-time/pyproject.toml"; pp.write_text(pp.read_text() + "\n[tool.pytest.ini_options]\nasyncio_mode = \"auto\"\n")
show("A 副次 陰性: doeff-time が [tool.pytest.ini_options] を持つ(完全性)", pins.pin3(W, files, pins.EXCLUDED))
shutil.rmtree(work)
