"""The public API of ``doeff_adr`` is loaded from the registry on first use — names, types, values and import failures stay
the same as before, and we check that merely loading the pytest plugin does not read the registry or YAML."""

import ast
import subprocess
import sys
from pathlib import Path

import doeff_adr
import doeff_adr._effects
import doeff_adr._pytest_plugin
import doeff_adr._registry
import doeff_adr._semgrep_effects
import doeff_adr._semgrep_hy

INIT = Path(doeff_adr.__file__)
#: Modules that define the public names (the registry, and the inner modules for effects and handlers.
#: _registry, _semgrep_hy and _pytest_plugin expose the types, exceptions and pytest plugin names).
DEFINING_MODULES = {
    "doeff_adr._registry": doeff_adr._registry,
    "doeff_adr._effects": doeff_adr._effects,
    "doeff_adr._semgrep_effects": doeff_adr._semgrep_effects,
    "doeff_adr._semgrep_hy": doeff_adr._semgrep_hy,
    "doeff_adr._pytest_plugin": doeff_adr._pytest_plugin,
}


def _type_checking_import_modules() -> dict[str, str]:
    """From the names imported under ``if TYPE_CHECKING:`` in ``__init__.py`` (the public names the type checker sees) to the defining module names."""
    tree = ast.parse(INIT.read_text())
    names: dict[str, str] = {}
    for node in tree.body:
        match node:
            case ast.If(test=ast.Name(id="TYPE_CHECKING"), body=body):
                for statement in body:
                    match statement:
                        case ast.ImportFrom(module=str(module), names=aliases) if (
                            module in DEFINING_MODULES
                        ):
                            names.update((alias.asname or alias.name, module) for alias in aliases)
                        case _:
                            pass
            case _:
                pass
    return names


def _type_checking_imports() -> set[str]:
    return set(_type_checking_import_modules())


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    """Run code in a fresh interpreter (without carrying over sys.modules pollution)."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )


def test_the_type_checked_names_are_the_runtime_names() -> None:
    """The names shown to the type checker (TYPE_CHECKING imports) and the names resolvable at run time (``_PUBLIC_API_NAMES``) are the same."""
    assert _type_checking_imports() == set(doeff_adr.__all__) - {"doeff_hy"}
    assert set(dir(doeff_adr)) >= _type_checking_imports()


def test_every_public_name_is_the_defining_module_value() -> None:
    """A public name's value is the very object from the defining module (registry, _effects or _semgrep_effects), not a copy or wrapper."""
    for name, module in _type_checking_import_modules().items():
        defined = vars(DEFINING_MODULES[module])[name]
        assert getattr(doeff_adr, name) is defined
        # A name looked up once becomes an ordinary attribute of the package (from the second time it does not go through __getattr__)
        assert vars(doeff_adr)[name] is defined


def test_unknown_names_fail_as_before() -> None:
    """A non-public name raises ``AttributeError``, and ``from doeff_adr import`` raises ``ImportError`` (never silently None)."""
    assert not hasattr(doeff_adr, "no_such_name")
    result = _run_python("from doeff_adr import no_such_name")
    assert result.returncode != 0
    assert "ImportError: cannot import name 'no_such_name'" in result.stderr


def test_loading_the_pytest_plugin_reads_neither_the_registry_nor_yaml() -> None:
    """If pytest merely loads the plugin (collection), the registry and YAML are not read. They are read only when a public name is used."""
    result = _run_python(
        "import sys, doeff_adr._pytest_plugin\n"
        "print('before', 'doeff_adr._registry' in sys.modules, 'yaml' in sys.modules)\n"
        "import doeff_adr\n"
        "doeff_adr.GetAdr\n"
        "print('after', 'doeff_adr._registry' in sys.modules)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:2] == ["before False False", "after True"]


def test_a_broken_registry_import_raises_its_own_error_at_first_use() -> None:
    """If the registry import is broken (e.g. the YAML dependency is missing), that exception is raised as is at the place of first use."""
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
        "doeff_adr.GetAdr\n"
    )
    assert "imported" in result.stdout
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'yaml'" in result.stderr
