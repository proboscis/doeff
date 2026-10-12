"""使い捨ての環境に入った Hy が代役(local_state を替えた物)か PyPI の物かを、検の file の補助で確かめるため。"""
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("guard_test_helpers", Path(sys.argv[1]))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
PROBE = (
    "import inspect, hy.compiler\n"
    "print('variant-local-state' if 'append(dict(macros' in inspect.getsource(hy.compiler.HyASTCompiler.local_state)"
    " else 'pypi-local-state')\n"
)
with tempfile.TemporaryDirectory(dir=sys.argv[2]) as tmp:
    print("disposable-env-hy:", helpers._probe_on_pypi_hy(Path(tmp), "before-hy", PROBE).strip())
