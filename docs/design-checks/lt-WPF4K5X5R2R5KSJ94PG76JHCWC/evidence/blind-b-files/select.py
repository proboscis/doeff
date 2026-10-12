import sys, importlib.util
from pathlib import Path
repo = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("rct", repo / "scripts/run_changed_tests.py")
m = importlib.util.module_from_spec(spec); sys.modules["rct"] = m; spec.loader.exec_module(m)
table = m.read_contract_table(repo)
patterns = m.collector_patterns(repo)
for files in [("uv.lock",), ("packages/doeff-hy/src/doeff_hy/macros.hy", "packages/doeff-hy/tests/test_val_var_lazy.py")]:
    sel = m.select_targets(table, m.ChangeSet(base="x", files=files), patterns, repo)
    print("== changed:", files)
    for t in sel.targets:
        print("  ", t.origin.value, "|", t.why, "|", t.arg)
