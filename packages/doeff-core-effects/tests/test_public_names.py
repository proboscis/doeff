"""doeff_core_effects の頭の名(Ask・Get・reader など)が、型の check に公開の名として見える事の検。

py.typed の package では、`__init__.py` で import しただけの名は公開と見なされない(`__all__` か `import X as X` が要る)。
README の Quick Start の `from doeff_core_effects import Ask, Get, Put, Tell` を写した人の editor と pyright が
「"Ask" is not exported from module」(reportPrivateImportUsage)を出していた(card ki-02e317783af0 の抜け 1)。

`__all__` は実行時の `from doeff_core_effects import *` にも効く。HTTP の名(`_LAZY_EXPORTS` — Hy の module で httpx を引く)は
`__getattr__` で初めて読むので、`__all__` に載せると核だけの環境の star import が ImportError になる。だから載せない。

失敗ケース: `__all__` を消す・名を 1 つ足し忘れる・lazy な名を載せると赤。
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import doeff_core_effects
from doeff_hy.static_check import python_without_bytecode

PACKAGE_DIR = Path(doeff_core_effects.__file__).resolve().parent

#: README の Quick Start が package の頭から取る名(README.md の 21・58・130 行目の例)。
README_NAMES = ("Ask", "Get", "Put", "Tell")


def _names_the_head_binds_from_submodules() -> set[str]:
    """`__init__.py` の頭で `from doeff_core_effects.<sub> import name` と結んである名(Hy の module を読まない、実行時に結ぶ名)。"""
    tree = ast.parse((PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("doeff_core_effects.")
        for alias in node.names
    }


def test_all_lists_every_name_the_head_binds() -> None:
    bound = _names_the_head_binds_from_submodules()
    assert set(doeff_core_effects.__all__) == bound, set(doeff_core_effects.__all__) ^ bound


def test_all_has_no_duplicates() -> None:
    names = list(doeff_core_effects.__all__)
    assert len(names) == len(set(names)), sorted(name for name in set(names) if names.count(name) > 1)


def test_all_names_are_bound_without_the_lazy_getattr() -> None:
    # `__all__` の名が module の dict に無いと、star import が `__getattr__` を通って Hy の module(httpx)を読む。
    unbound = sorted(name for name in doeff_core_effects.__all__ if name not in vars(doeff_core_effects))
    assert unbound == [], unbound
    assert set(doeff_core_effects.__all__).isdisjoint(doeff_core_effects._LAZY_EXPORTS)


def test_star_import_does_not_load_the_hy_modules() -> None:
    code = (
        "import sys\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        "loaded = sorted(m for m in ('httpx', 'doeff_core_effects.http_handlers', 'doeff_core_effects.http_effects') if m in sys.modules)\n"
        "assert 'Ask' in namespace and 'reader' in namespace and 'HttpRequest' not in namespace, sorted(namespace)\n"
        "assert not loaded, loaded\n"
    )
    done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr


def _pyright_errors(tmp_path: Path, source: str, mode: str) -> list[str]:
    """tmp_path の 1 file を、この環境の python に入った doeff_core_effects(使い手と同じ、py.typed の package)に対して検める。"""
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": mode}), encoding="utf-8")
    sample = tmp_path / "head_imports.py"
    sample.write_text(source, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", str(python_without_bytecode(tmp_path, sys.executable)), "--outputjson", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    report = json.loads(done.stdout)
    return [f"{item['range']['start']['line'] + 1}: {item['message']}" for item in report["generalDiagnostics"]
            if item["severity"] == "error"]


def test_pyright_accepts_the_readme_imports(tmp_path: Path) -> None:
    source = (
        f"from doeff_core_effects import {', '.join(README_NAMES)}\n"
        "from doeff_core_effects import reader, state, writer\n"
        "\n"
        f"_ = ({', '.join(README_NAMES)}, reader, state, writer)\n"
    )
    assert _pyright_errors(tmp_path, source, "standard") == []


def test_pyright_accepts_every_name_in_all(tmp_path: Path) -> None:
    names = sorted(doeff_core_effects.__all__)
    source = "from doeff_core_effects import (\n" + "".join(f"    {name},\n" for name in names) + ")\n"
    assert _pyright_errors(tmp_path, source, "standard") == []
