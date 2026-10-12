# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""uv.lock だけを変えた便で `make test-changed` が選ぶ契約の組を出す(scripts/run_changed_tests.py の select_targets をそのまま呼ぶ)。

使い方: uv run --script selection.py <作業木> [変えた file …](既定 = uv.lock)
"""

import importlib.util
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
files = tuple(sys.argv[2:]) or ("uv.lock",)
spec = importlib.util.spec_from_file_location("run_changed_tests", repo / "scripts" / "run_changed_tests.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
sys.modules["run_changed_tests"] = runner
spec.loader.exec_module(runner)
selection = runner.select_targets(
    runner.read_contract_table(repo),
    runner.ChangeSet(base="(この script が渡す)", files=files),
    runner.collector_patterns(repo),
    repo,
)
print(f"変えた file: {', '.join(files)}")
for target in selection.targets:
    print(f"{target.arg} | {target.why}")
