"""盲検 A の commit 1: _reads_itself・_rewrapped_seeds・_wraps を body_facts.py へそのまま移す(写しの上だけ)。"""
import ast, sys
from pathlib import Path
package = Path(sys.argv[1])
pe = package / "program_effects.py"
source = pe.read_text()
tree = ast.parse(source)
names = ("_reads_itself", "_rewrapped_seeds", "_wraps")
lines = source.splitlines(keepends=True)
spans = sorted((node.lineno - 1 - len(node.decorator_list), node.end_lineno) for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name in names)
assert len(spans) == 3, spans
moved = "".join("".join(lines[a:b]) + "\n\n" for a, b in spans)
kept = "".join(line for i, line in enumerate(lines) if not any(a <= i < b for a, b in spans))
kept = kept.replace("from doeff_effect_analyzer import env_places\n",
                    "from doeff_effect_analyzer import env_places\nfrom doeff_effect_analyzer.body_facts import _reads_itself, _rewrapped_seeds, _wraps\n", 1)
pe.write_text(kept)
(package / "body_facts.py").write_text('"""Body facts helpers split out of program_effects (blind A commit 1)."""\n\nimport ast\nfrom collections.abc import Sequence\n\n\n' + moved)
