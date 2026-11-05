"""pytest plugin for doeff-hy (entry point `pytest11`, name `doeff_hy`).

pytest loads plugins and conftests at startup, and on Python 3.14 reads fixture annotations with
`annotation_format=Format.STRING`. If Hy is imported before that (from another plugin or a conftest),
Hy's replacement of `ast.unparse` recurses without stopping there
(doeff_hy/_ast_unparse.py). This plugin applies the replacement at pytest startup, without waiting for `import doeff_hy`.

Its other role is the time limit for Hy tests (doeff_hy_pytest/_budget.py - does nothing when unconfigured). The hooks are
re-exported from this module so that they ride on the single entry point (`doeff_hy`).

The public names are only the two per-test declarations (`declare_step_budget`, `declare_process_tree_cpu`) and the entry point
(doeff-hy-pytest's share of the rule "the public API a doeff subpackage shows users is only effects and handlers").
Hooks are picked up by pytest from module attributes, so they are not in `__all__`. The measuring internals (`_budget`,
`_python_calls`, `_tree_cpu`) are internal; users read the summary lines the plugin prints.

The reason this module lives in a distribution separate from doeff-hy is the note in pyproject.toml (pytest's assert-rewrite mark is
put on every module of a distribution and would break doeff_hy's .hy files).
"""

from doeff_hy._ast_unparse import install

from doeff_hy_pytest._budget import declare_process_tree_cpu as declare_process_tree_cpu
from doeff_hy_pytest._budget import declare_step_budget as declare_step_budget
from doeff_hy_pytest._budget import pytest_addoption as pytest_addoption
from doeff_hy_pytest._budget import pytest_configure as pytest_configure
from doeff_hy_pytest._budget import pytest_doeff_import_hy_module as pytest_doeff_import_hy_module
from doeff_hy_pytest._budget import pytest_runtest_call as pytest_runtest_call
from doeff_hy_pytest._budget import pytest_runtest_makereport as pytest_runtest_makereport
from doeff_hy_pytest._budget import pytest_terminal_summary as pytest_terminal_summary
from doeff_hy_pytest._budget import pytest_unconfigure as pytest_unconfigure

__all__ = ["declare_process_tree_cpu", "declare_step_budget"]

install()
