# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Hy を上げて表の名の変更に合わせる便の差分で、make test-changed の契約の組と変えた検を選ぶ(逆依存は linter が要るので除く)。"""
import importlib.util, sys
from pathlib import Path

repo = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("rct", repo / "scripts" / "run_changed_tests.py")
rct = importlib.util.module_from_spec(spec)
sys.modules["rct"] = rct
spec.loader.exec_module(rct)
files = tuple(sys.argv[2:])
sel = rct.select_targets(rct.read_contract_table(repo), rct.ChangeSet("x", files), rct.collector_patterns(repo), repo)
for t in sel.targets:
    print(t.origin.name, t.arg, "|", t.why)
