"""持ち主の package の定義の macro が `_doeff_` の別名で出す import を、型検査のための展開が「展開の合成した import」と読む事の検
(card ki-f2107259b141 — defsystem を doeff-cluster へ移した時、doeff-hy から doeff-cluster の module の綴りの表を無くした)。

doeff-hy の外の定義の macro(doeff-cluster の defsystem など)は、展開の import を `import <module> as _doeff_<名>` の形で置く。
doeff-hy-check はその綴りの表を持たず、束ねる名の頭 `_doeff_`(expansion_names.PREFIX)だけで読む:

- module の直下で同じ文が繰り返されたら最初の 1 つだけを残す(1 つの module に系を 2 つ書いても reportDuplicateImport を出さない)。
- 関数・class の本体の中の物は module の段へ移す(入れ子の定義の展開の import を外の関数の局所の名にしない)。
- 書き手の import(別名の無い `import json`・`_doeff_` の頭でない別名)は畳まず、動かさない。
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
    # 書き手が自分で 2 度書いた import(別名の無い形・`_doeff_` の頭でない別名)は畳まず、型検査の赤のまま残す。
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
