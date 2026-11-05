"""Check that the type-check expansion reads the import that a macro defined in the owner package emits under a `_doeff_` alias as an "import synthesized by expansion"
(when defsystem moved to doeff-cluster, doeff-hy lost its table of doeff-cluster module spellings).

Macros defined outside doeff-hy (such as doeff-cluster's defsystem) place the expansion's imports in the form `import <module> as _doeff_<name>`.
doeff-hy-check has no table of those spellings and reads them only by the bundled-name prefix `_doeff_` (expansion_names.PREFIX):

- If the same statement is repeated directly under a module, only the first is kept (writing two systems in one module does not produce reportDuplicateImport).
- Those inside function and class bodies are moved to module level (the expansion's imports of nested definitions do not become local names of the outer function).
- The author's own imports (a plain `import json` with no alias, an alias not starting with `_doeff_`) are not folded or moved.
"""

import ast

from doeff_hy._static_check import with_module_level_imports, without_bookkeeping


def test_a_repeated_reserved_alias_import_is_folded_to_one() -> None:
    tree = ast.parse(
        "import owner.model as _doeff_owner_model\n"
        "a = _doeff_owner_model.A\n"
        "import owner.model as _doeff_owner_model\n"
        "b = _doeff_owner_model.B\n"
    )
    projected = ast.unparse(without_bookkeeping(tree)).splitlines()
    assert projected == [
        "import owner.model as _doeff_owner_model",
        "a = _doeff_owner_model.A",
        "b = _doeff_owner_model.B",
    ], projected


def test_a_repeated_writer_import_stays() -> None:
    # An import the author wrote twice (the form without an alias, an alias not starting with `_doeff_`) is not folded and stays as a type-check error.
    source = "import json\nimport json\nimport owner.model as model\nimport owner.model as model\nx = (json, model)\n"
    projected = ast.unparse(without_bookkeeping(ast.parse(source))).splitlines()
    assert projected.count("import json") == 2, projected
    assert projected.count("import owner.model as model") == 2, projected


def test_a_nested_reserved_alias_import_moves_to_the_module_level() -> None:
    tree = ast.parse(
        "def outer():\n"
        "    import owner.model as _doeff_owner_model\n"
        "    import json\n"
        "    return (_doeff_owner_model.A, json)\n"
    )
    projected = ast.unparse(with_module_level_imports(tree)).splitlines()
    assert projected[0] == "import owner.model as _doeff_owner_model", projected
    assert "    import json" in projected, projected
    assert "    import owner.model as _doeff_owner_model" not in projected, projected
