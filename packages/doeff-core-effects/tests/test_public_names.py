"""doeff_core_effects の頭の名(Ask・Get・reader など)が、型の check に公開の名として見える事の検。

py.typed の package では、`__init__.py` で import しただけの名は公開と見なされない(`__all__` か `import X as X` が要る)。
README の Quick Start の `from doeff_core_effects import Ask, Get, Put, Tell` を写した人の editor と pyright が
「"Ask" is not exported from module」(reportPrivateImportUsage)を出していた。

この repo は `__all__` を置かない(doeff-linter の DOEFF021 — `doeff/__init__.py` も `X as X`)ので、頭の名は `X as X` で結ぶ。
HTTP の名(`_LAZY_EXPORTS` — Hy の module で httpx を引く)は `__getattr__` で初めて読むので、`X as X` で結べない
(結ぶと核だけの環境の import が落ちる)。star import が `__getattr__` を通って Hy の module を読まない事も、ここで守る。

失敗ケース: 名を `X as X` にし忘れる・HTTP の名を頭で結ぶと赤。
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import doeff_core_effects
from doeff_hy._static_check import python_without_bytecode

PACKAGE_DIR = Path(doeff_core_effects.__file__).resolve().parent

#: README と入門の章(docs/01-getting-started.md)の例が package の頭から取る名。
README_NAMES = ("Ask", "Get", "Put", "Tell")


def _head_imports() -> list[ast.alias]:
    """`__init__.py` の頭で `from doeff_core_effects.<sub> import name` と結んである名(実行時に結ぶ、Hy を読まない名)。"""
    tree = ast.parse((PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"))
    return [
        alias
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("doeff_core_effects.")
        for alias in node.names
    ]


def _head_names() -> list[str]:
    return sorted(alias.name for alias in _head_imports())


def test_the_head_binds_the_readme_names() -> None:
    assert set(README_NAMES) <= set(_head_names())


def test_every_name_the_head_binds_is_a_redundant_alias_reexport() -> None:
    # `import X as X` が py.typed の package の再 export の印。印の無い名が型の check に「公開していない名」と読まれる。
    plain = sorted(alias.name for alias in _head_imports() if alias.asname != alias.name)
    assert plain == [], f"X as X にする: {plain}"


def test_the_head_does_not_bind_the_lazy_names() -> None:
    # 頭で結ぶと、核だけの環境(Hy の module と httpx が無い)の `import doeff_core_effects` が落ちる。
    assert set(_head_names()).isdisjoint(doeff_core_effects._LAZY_EXPORTS)


def test_every_head_name_is_bound_in_the_module() -> None:
    unbound = [name for name in _head_names() if name not in vars(doeff_core_effects)]
    assert unbound == [], unbound


def test_star_import_does_not_load_the_hy_modules() -> None:
    code = (
        "import sys\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        "loaded = sorted(m for m in ('httpx', 'doeff_core_effects._http_handlers', 'doeff_core_effects._http_effects') if m in sys.modules)\n"
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


def test_pyright_accepts_every_name_the_head_binds(tmp_path: Path) -> None:
    source = "from doeff_core_effects import (\n" + "".join(f"    {name},\n" for name in _head_names()) + ")\n"
    assert _pyright_errors(tmp_path, source, "standard") == []
