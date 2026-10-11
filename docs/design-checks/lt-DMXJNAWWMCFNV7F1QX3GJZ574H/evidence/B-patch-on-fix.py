"""B の D1 を直した版へ当てる 2 つの形(写しの上だけ)。store = 読み手が加工した木を展開の置き場へ書く / legit = 読み手の側だけで加工する。"""
import sys
from pathlib import Path
p = Path(sys.argv[1]); mode = sys.argv[2]; s = p.read_text()
helpers = '''

def _canonical_gensyms(expanded: _Expanded) -> _Expanded:
    """Hy's gensym names renumbered in module order (blind B's D1)."""
    from doeff_hy_bytecode_guard import gensym_renaming

    nodes = tuple(ast.walk(expanded.tree))
    rename = gensym_renaming(
        text for node in nodes for _, value in ast.iter_fields(node) for text in _field_texts(value)
    )
    for node in nodes:
        for name, value in ast.iter_fields(node):
            if isinstance(value, str):
                setattr(node, name, rename(value))
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                setattr(node, name, [rename(item) for item in value])
    return expanded


def _field_texts(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()
'''
old = "        expanded = _expansion(source, filename, module_name, _hy_cache_dir())\n"
assert old in s
if mode == "store":
    new = ("        expanded = _canonical_gensyms(_expansion(source, filename, module_name, _hy_cache_dir()))\n"
           "        directory = _hy_cache_dir()\n"
           "        if directory is not None:\n"
           "            _store_expansion(_expansion_key(source, module_name).place(directory), expanded)\n")
    s = s.replace("    _type_check_expansion,\n)", "    _type_check_expansion,\n)\nfrom doeff_effect_analyzer.hy_expansion import _store_expansion\n", 1)
else:
    new = "        expanded = _canonical_gensyms(_expansion(source, filename, module_name, _hy_cache_dir()))\n"
s = s.replace(old, new, 1)
s = s.replace("\n\n# Who wants to know when an expansion missed the cache", helpers + "\n\n# Who wants to know when an expansion missed the cache", 1)
p.write_text(s)
