"""``doeff_adr`` の公開 API は最初の利用時に registry から読む(agora-redesign #1551)— 名・型・値・import の失敗は
今までと同じで、pytest の plugin を読むだけでは registry と YAML を読まないことを確かめる。"""

import ast
import subprocess
import sys
from pathlib import Path

import doeff_adr
import doeff_adr.registry

INIT = Path(doeff_adr.__file__)


def _type_checking_imports() -> set[str]:
    """``__init__.py`` の ``if TYPE_CHECKING:`` の下で registry から import する名(型の検査器が見る公開の名)。"""
    tree = ast.parse(INIT.read_text())
    names: set[str] = set()
    for node in tree.body:
        match node:
            case ast.If(test=ast.Name(id="TYPE_CHECKING"), body=body):
                for statement in body:
                    match statement:
                        case ast.ImportFrom(module="registry", names=aliases):
                            names.update(alias.asname or alias.name for alias in aliases)
                        case _:
                            pass
            case _:
                pass
    return names


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    """新しい interpreter で code を走らせる(sys.modules の汚れを持ち込まない)。"""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)


def test_the_type_checked_names_are_the_runtime_names() -> None:
    """型の検査器に見せる名(TYPE_CHECKING の import)と、実行の時に引ける名(``__all__``)が同じ。"""
    assert _type_checking_imports() == set(doeff_adr.__all__) - {"doeff_hy"}
    assert set(dir(doeff_adr)) >= set(doeff_adr.__all__)


def test_every_public_name_is_the_registry_value() -> None:
    """公開の名の値は registry の物そのもの(写しや包みではない)。"""
    registry_names = vars(doeff_adr.registry)
    for name in set(doeff_adr.__all__) - {"doeff_hy"}:
        assert getattr(doeff_adr, name) is registry_names[name]
        # 1 度引いた名は package の普通の属性になる(2 回目から __getattr__ を通らない)
        assert vars(doeff_adr)[name] is registry_names[name]


def test_unknown_names_fail_as_before() -> None:
    """公開でない名は ``AttributeError``、``from doeff_adr import`` では ``ImportError``(黙って None にしない)。"""
    assert not hasattr(doeff_adr, "no_such_name")
    result = _run_python("from doeff_adr import no_such_name")
    assert result.returncode != 0
    assert "ImportError: cannot import name 'no_such_name'" in result.stderr


def test_loading_the_pytest_plugin_reads_neither_the_registry_nor_yaml() -> None:
    """pytest が plugin を読むだけ(収集)なら registry と YAML は読まない。公開の名を使った時に初めて読む。"""
    result = _run_python(
        "import sys, doeff_adr.pytest_plugin\n"
        "print('before', 'doeff_adr.registry' in sys.modules, 'yaml' in sys.modules)\n"
        "import doeff_adr\n"
        "doeff_adr.get_adr\n"
        "print('after', 'doeff_adr.registry' in sys.modules)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:2] == ["before False False", "after True"]


def test_a_broken_registry_import_raises_its_own_error_at_first_use() -> None:
    """registry の import が壊れていれば(依存の YAML が無い等)、その例外が最初の利用の場所でそのまま上がる。"""
    result = _run_python(
        "import sys\n"
        "class NoYaml:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'yaml':\n"
        "            raise ModuleNotFoundError(\"No module named 'yaml'\", name='yaml')\n"
        "        return None\n"
        "sys.meta_path.insert(0, NoYaml())\n"
        "import doeff_adr\n"
        "print('imported')\n"
        "doeff_adr.get_adr\n"
    )
    assert "imported" in result.stdout
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'yaml'" in result.stderr
