"""S4 の反例の確かめ: pkg.user を import し、展開された値と .pyc の記録を出す。

引数: <根の dir> <prefix|fixed>
- prefix = 直す前の包みの代役(局所の状態を開く口を包まない — 局所の表を覚えない。他は今の包みのまま)
- fixed  = 今の包み(作業木の source のまま)
"""
import glob
import marshal
import os
import sys

root, mode = sys.argv[1], sys.argv[2]
sys.dont_write_bytecode = True  # 包みと Hy の .pyc を作業木・venv に書かない
import doeff_hy_bytecode_guard  # noqa: E402,F401
from doeff_hy_bytecode_guard import (  # noqa: E402,F401
    bound_source, called_code, code_store, dataclass_code, expansion, import_gc, import_scope,
    loader_hooks, macro_use, records,
)

assert loader_hooks.installed(), "起動の .pth が包みを入れていない"
if mode == "prefix":
    macro_use._LOCAL_WATCH["installed"] = True  # 直す前の包みの代役: 局所の表を覚えない(Hy を読む前に)
import hy  # noqa: E402
from hy.compiler import HyASTCompiler  # noqa: E402
sys.dont_write_bytecode = False  # 使い手の pkg の .pyc だけ書く(/tmp の下)
sys.path.insert(0, root)
import pkg.user  # noqa: E402

print("mode", mode, "hy", hy.__version__, "new_local_state wrapped:",
      HyASTCompiler.new_local_state.__qualname__ != "HyASTCompiler.new_local_state")
print("value", pkg.user.value)
for pyc in sorted(glob.glob(os.path.join(root, "pkg", "__pycache__", "user.*.pyc"))):
    record = records.record_of(marshal.loads(open(pyc, "rb").read()[records.PYC_HEADER_BYTES:]))
    if record is None:
        print("pyc-record None")
    else:
        print("pyc-record macros", [(m.table, m.module, m.name) for m in record.macros],
              "providers", record.providers, "absent-count", len(record.absent))
store = os.environ.get("DOEFF_HY_CODE_STORE", "off")
if store not in ("", "off"):
    print("store-code-entries", len(glob.glob(os.path.join(store, "*", "*", "*.code"))))
