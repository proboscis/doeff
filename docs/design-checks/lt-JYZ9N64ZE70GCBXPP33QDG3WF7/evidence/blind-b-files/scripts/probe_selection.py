"""Hy を上げる便の変えた file の組で、make test-changed の選び(select_targets)が何を選ぶかを確かめるため。"""
import importlib.util
import sys
from pathlib import Path

repo = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("run_changed_tests", repo / "scripts" / "run_changed_tests.py")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
table = runner.read_contract_table(repo)
patterns = runner.collector_patterns(repo)
for files in (("uv.lock",), ("uv.lock", "packages/doeff-hy/pyproject.toml")):
    selection = runner.select_targets(table, runner.ChangeSet(base="", files=files), patterns, repo)
    print("changed:", files)
    for target in selection.targets:
        print("  ", target.origin.name, target.arg, "|", target.why)
