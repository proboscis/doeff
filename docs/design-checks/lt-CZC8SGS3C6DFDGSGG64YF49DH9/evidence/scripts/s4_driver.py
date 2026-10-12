"""S4 の検の口: 利用者の Hy の module を bytecode guard を通して import して走らせる(import)/ 残った .pyc の code を
照らさずにそのまま走らせる(stale)。設計検証 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の s4_driver.py と同じ形。"""

import hashlib
import importlib.util
import marshal
import os
import sys
import types

import doeff_hy  # noqa: F401 — bytecode guard と Hy の import を入れる


def digest(path: str) -> str | None:
    """.pyc が書き換わったかを見るため。"""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def run_main(main: object) -> None:
    """答えか落ちた形を 1 行で出すため。"""
    try:
        print("main =>", main())
    except Exception as error:  # noqa: BLE001 — 検の記録: 落ちた形を出す
        print("main !!", type(error).__name__, error)


mode, src_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, src_dir)
source = os.path.join(src_dir, "edab_s4_consumer.hy")
pyc = importlib.util.cache_from_source(source)
if mode == "import":
    before = digest(pyc)
    try:
        import edab_s4_consumer as consumer
    except Exception as error:  # noqa: BLE001 — 検の記録: import で落ちた形を出す
        print("import !!", type(error).__name__, error)
        print("pyc", pyc)
        print("pyc sha256 before", before)
        print("pyc sha256 after ", digest(pyc))
        raise SystemExit(0)
    print("pyc", pyc)
    print("pyc sha256 before", before)
    print("pyc sha256 after ", digest(pyc))
    run_main(consumer.main)
elif mode == "stale":
    with open(sys.argv[3], "rb") as handle:
        code = marshal.loads(handle.read()[16:])
    module = types.ModuleType("edab_s4_stale")
    module.__file__ = source
    sys.modules["edab_s4_stale"] = module
    try:
        exec(code, module.__dict__)
    except Exception as error:  # noqa: BLE001 — 検の記録: 古い code の module の評価で落ちた形を出す
        print("stale pyc", sys.argv[3], "sha256", digest(sys.argv[3]))
        print("module !!", type(error).__name__, error)
        raise SystemExit(0)
    print("stale pyc", sys.argv[3], "sha256", digest(sys.argv[3]))
    run_main(module.main)
