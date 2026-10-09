"""@do の関数が返す Expand は、pyright がどの順で file を検めても Program として受け取れる(agora-redesign #4346)。

agora-controllers の日次の型検査(pyright 1.1.414・1 process で全 file)で、defk の関数を
`Callable[..., Program]` の引数へ渡す所が reportArgumentType で落ち、赤になる file が実行のたびに入れ替わっていた。
同じ形は 2 つの file で再現する(下の _TRIGGER と _PROBE):

- 先に検める file で、@do の呼びを union の期待型の下で `Program[T, ...]` の引数へ渡す
  (`answer: str | None = run(make(1))`。doeff-hy の `(<- x (| str None) …)` の展開も同じ形)。
- 後に検める file で、@do の関数を `tuple[Callable[..., Program], ...]` へ渡す。

pyright(1.1.411 と 1.1.414 で実測)は、protocol への代入が失敗すると、型引数を外した形
(`Expand[_T_co, _E_co]` → `Program[_ProgramResult, _ProgramEffects]`)でも試し、それも通らなければ
「Expand はどの型引数でも Program に合わない」と Expand の class の cache に残す。後の file の代入はこの cache を引いて
落ちる。後の file だけを検めると 0 件、逆の順でも 0 件。Expand が Program を明示の基底に持てば、pyright は
protocol の照合でなく継承で判じるので、この cache を通らない。

pyright は引数に並べた順に file を検めるので、ここでは引き金の file を先に並べて 1 process で検める。
"""

import json
import subprocess
import sys
from pathlib import Path

_TRIGGER = """\
from doeff import do, run


@do
def make(x: int) -> str | None:
    return None


def judge() -> str | None:
    answer: str | None = run(make(1))
    return answer
"""

_PROBE = """\
from collections.abc import Callable

from doeff import Expand, Program, do


@do
def assemble(parts: int) -> int:
    return parts


def declared(parts: int) -> Expand[int, None]: ...


def count(parts: int) -> int:
    return parts


def receiver(host_side: tuple[Callable[..., Program], ...]) -> None: ...


receiver((assemble,))
receiver((declared,))
# ---- mistakes (each line below must be an error) ----
receiver((count,))
"""

_MISTAKE_MARKER = "# ---- mistakes"


def _pyright_in_order(tmp_path: Path, files: tuple[tuple[str, str], ...]) -> dict:
    """files を並べた順に 1 つの pyright の process で検め、--outputjson の結果を返す。"""
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(root / "pyrightconfig.json"),
        "extraPaths": [str(root), *(str(root / path) for path in original["extraPaths"])],
    }))
    for name, source in files:
        (tmp_path / name).write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", sys.executable, "--outputjson", *(str(tmp_path / name) for name, _ in files)],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    return json.loads(result.stdout)


def test_do_function_is_a_program_even_after_a_union_expected_bind(tmp_path: Path) -> None:
    report = _pyright_in_order(tmp_path, (("a_trigger.py", _TRIGGER), ("b_probe.py", _PROBE)))
    assert report["summary"]["filesAnalyzed"] == 2, report["summary"]
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    trigger_errors = [e for e in errors if Path(e["file"]).name == "a_trigger.py"]
    assert trigger_errors == [], trigger_errors
    probe_lines = _PROBE.splitlines()
    first_mistake = next(i for i, line in enumerate(probe_lines) if line.startswith(_MISTAKE_MARKER))
    probe_errors = [e for e in errors if Path(e["file"]).name == "b_probe.py"]
    clean_part = [e for e in probe_errors if e["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {probe_lines[e["range"]["start"]["line"]].strip() for e in probe_errors}
    assert flagged == {"receiver((count,))"}, probe_errors
