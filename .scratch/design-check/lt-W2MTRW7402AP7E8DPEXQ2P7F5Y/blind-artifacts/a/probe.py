"""盲検 A の試し: Hy の source の拡張子を 1 つ足す変更(.hym)を、宣言された定義点だけで行った時の照らしの結末。

使い方: probe.py <variant> <phase> <root>
  variant = hy(対照: 今ある拡張子 .hy)| hym-defpoint(doeff_hy の登録と code_store.HY_SOURCE_SUFFIXES だけ足す)
          | hym-full(bound_source・macro_use・records の写しにも足す)
  phase   = setup(package を書く)| first(import して .pyc を書く)| edit(helper の method の本体を替える)| second(読み直す)
"""

import importlib
import importlib.machinery
import importlib.util
import json
import marshal
import os
import sys
from pathlib import Path

variant, phase, root = sys.argv[1], sys.argv[2], Path(sys.argv[3])
NEW = ".hym"
PKG = "sepx_pkg"
ext = ".hy" if variant == "hy" else NEW
pkg_dir = root / PKG

if phase == "setup":
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / f"macros{ext}").write_text(
        "(defclass Scaler []\n"
        "  (defn factor [self] 3))\n"
        '(setv TABLE {"a" 1})\n'
        "(defmacro scaled [] (.factor (Scaler)))\n"
        '(defmacro looks [] (get TABLE "a"))\n',
        encoding="utf-8",
    )
    (pkg_dir / "user.hy").write_text(
        f"(require {PKG}.macros [scaled])\n(setv value (scaled))\n", encoding="utf-8"
    )
    print(json.dumps({"variant": variant, "phase": phase, "macros": f"macros{ext}"}))
    sys.exit(0)

if phase == "edit":
    path = pkg_dir / f"macros{ext}"
    text = path.read_text(encoding="utf-8")
    assert "(defn factor [self] 3)" in text
    path.write_text(text.replace("(defn factor [self] 3)", "(defn factor [self] 30)"), encoding="utf-8")
    print(json.dumps({"variant": variant, "phase": phase, "edited": str(path.name)}))
    sys.exit(0)

import hy  # noqa: E402
import hy.importer  # noqa: E402

import doeff_hy  # noqa: E402,F401 — .hyk・.hyp の登録と guard の install
from doeff_hy_bytecode_guard import (  # noqa: E402
    bound_source,
    code_store,
    loader_hooks,
    macro_dependencies,
    macro_use,
    record_is_current_here,
    records,
)

if variant.startswith("hym"):
    # (1) doeff_hy/__init__.py の 2 か所(SOURCE_SUFFIXES への登録と _could_be_hy_src の _HY_EXTENSIONS)に .hym を足したのと同じ。
    importlib.machinery.SOURCE_SUFFIXES.insert(0, NEW)
    hy_extensions = {".hy", ".hyk", ".hyp", NEW}

    def _could_be_hy_src(filename: str) -> bool:
        return os.path.isfile(filename) and (
            os.path.splitext(filename)[1] not in set(importlib.machinery.SOURCE_SUFFIXES) - hy_extensions
        )

    hy.importer._could_be_hy_src = _could_be_hy_src
    # (2) guard の宣言された定義点(code_store.HY_SOURCE_SUFFIXES — loader_hooks は名を写して読むので、source を直せば両方に効く)。
    code_store.HY_SOURCE_SUFFIXES = (*code_store.HY_SOURCE_SUFFIXES, NEW)
    loader_hooks.HY_SOURCE_SUFFIXES = code_store.HY_SOURCE_SUFFIXES

# (3) 定義点の外の写し 3 つ(bound_source._HY_SUFFIXES・macro_use._python_source の literal・records._SOURCE_SUFFIXES)— 変種の名で選ぶ。
patched = {"bs", "mu", "rc"} if variant == "hym-full" else set(variant.split("-")[1:]) & {"bs", "mu", "rc"}
if "bs" in patched:
    bound_source._HY_SUFFIXES = (*bound_source._HY_SUFFIXES, NEW)
if "mu" in patched:

    def _python_source(module: object) -> bool:
        # macro_use の拡張子の写しに .hym を足した形(class の method の本体を辿るかを決める)。
        file = None if module is None else vars(module).get("__file__")
        return isinstance(file, str) and file.endswith((".py", ".hy", ".hyk", ".hyp", ".pyc", NEW))

    macro_use._python_source = _python_source
if "rc" in patched:
    records._SOURCE_SUFFIXES = (*records._SOURCE_SUFFIXES, NEW)

sys.path.insert(0, str(root))
user_path = str(pkg_dir / "user.hy")
macros_path = str(pkg_dir / f"macros{ext}")
answer: dict[str, object] = {"variant": variant, "phase": phase, "patched": sorted(patched)}

# 読み直す前に、木の .pyc に載った記録を今の環境で照らす(読みの口と同じ照らし方)。
pyc = importlib.util.cache_from_source(user_path)
if os.path.exists(pyc):
    code = marshal.loads(Path(pyc).read_bytes()[records.PYC_HEADER_BYTES :])
    record = records.record_of(code)
    answer["pyc_record_macros"] = None if record is None else [
        (m.module, m.name, (m.digest or "")[:12]) for m in record.macros
    ]
    answer["pyc_record_is_current_here"] = None if record is None else record_is_current_here(record)
    for name in [n for n in sys.modules if n == PKG or n.startswith(f"{PKG}.")]:
        del sys.modules[name]

user = importlib.import_module(f"{PKG}.user")
answer["user.value"] = user.value
macros = sys.modules[f"{PKG}.macros"]
answer["macros.__file__"] = os.path.basename(macros.__file__)
key = macro_use.UsedMacroKey(records.MACRO_TABLE, f"{PKG}.macros", "scaled")
scaled = dict.__getitem__(vars(macros)[records.MACRO_TABLE], "scaled")
answer["scaled_digest_now"] = (macro_use.macro_digest(key, scaled, loader_hooks.file_sha256).digest or "")[:12]
answer["macro_dependencies(user)"] = [os.path.basename(d.file) for d in macro_dependencies(user, user_path)]
binding = bound_source.binding_of(macros_path, "TABLE")
answer["binding_of(macros, TABLE)"] = None if binding is None else [s.text[:40] for s in binding.statements]
print(json.dumps(answer, ensure_ascii=False))
