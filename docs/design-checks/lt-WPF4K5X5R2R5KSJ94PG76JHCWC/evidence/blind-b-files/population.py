import sys, importlib.util
from pathlib import Path
import hy  # noqa
from doeff import run
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("adr008", root / "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
rows = run(m.scan_hy_reliances(root))
for r in rows:
    print(r)
