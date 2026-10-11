"""別々の解析が、process の覚え(_absent_receiver の functools.cache)を通して同じ節と同じ _Reader を持つかを見る。

2 つの Program(p1・p2 — 別の関数)を別々に analyze_program し、``(<- x e :absent F)`` の受け手の節を比べる。
mode=race なら、2 つの thread が各自の解析(analyze_program + check_coverage)を同時に走らせ、答えを出す。
正しい答え: どちらも env なしで gaps に Raise が在る(受け手の Absent の節が Raise を出す)。
使い方: python absent_share.py <fixture の dir> <share|race>
"""

import importlib
import sys
import textwrap
import threading
import uuid
from pathlib import Path

import doeff_effect_analyzer
from doeff_effect_analyzer.handler_effects import check_coverage
from doeff_effect_analyzer.program_effects import analyze_program

PROGRAMS = """
from doeff import do
from doeff_core_effects.effects import Absent
from doeff_core_effects.outcomes import open_bind


@do
def maybe_one():
    yield Absent("none")


@do
def p1():
    x = yield open_bind(maybe_one(), lambda: "p1 needs one")
    return x


@do
def p2():
    y = yield open_bind(maybe_one(), lambda: "p2 needs one")
    return y
"""

root = Path(sys.argv[1])
mode = sys.argv[2]
root.mkdir(parents=True, exist_ok=True)
name = f"fxb_{uuid.uuid4().hex[:8]}"
(root / name).mkdir()
(root / name / "__init__.py").write_text("")
(root / name / "programs.py").write_text(textwrap.dedent(PROGRAMS))
sys.path.insert(0, str(root))
importlib.invalidate_caches()


def receiver_clause(report: object) -> object:
    [scope] = report.handled  # type: ignore[attr-defined]
    [handler] = scope.handlers
    [clause] = handler.clauses
    return clause


def held_reader(clause: object) -> object:
    held = clause.__dict__["_emits"]  # 読む前の関数(後の版だけ)
    if not callable(held):
        return None
    cells = dict(zip(held.__code__.co_freevars, (cell.cell_contents for cell in held.__closure__)))
    return cells.get("reader")


if mode == "share":
    print("analyzer:", doeff_effect_analyzer.__file__)
    r1 = analyze_program(f"{name}.programs:p1")
    r2 = analyze_program(f"{name}.programs:p2")
    c1, c2 = receiver_clause(r1), receiver_clause(r2)
    print("p1 と p2 の受け手の節は同じ object:", c1 is c2)
    print("節が握る _Reader:", type(held_reader(c1)).__name__, "同じ object:", held_reader(c1) is held_reader(c2))
    print("p1 gaps:", [g.effect.__name__ for g in check_coverage(r1, []).gaps])
    print("p2 gaps:", [g.effect.__name__ for g in check_coverage(r2, []).gaps])
else:
    if mode == "race-warm":
        analyze_program(f"{name}.programs:p1")  # 受け手を先に 1 度読む(節はまだ引かない)
    barrier = threading.Barrier(2)
    out: dict[str, list[str]] = {}

    def analysis(program: str) -> None:
        barrier.wait()
        report = analyze_program(f"{name}.programs:{program}")
        out[program] = [g.effect.__name__ for g in check_coverage(report, []).gaps]

    threads = [threading.Thread(target=analysis, args=(p,)) for p in ("p1", "p2")]
    if mode == "race-hook":
        from doeff_effect_analyzer import program_effects as pe

        analyze_program(f"{name}.programs:p1")  # 受け手を先に 1 度読む(節はまだ引かない)
        first_in = threading.Event()
        other_done = threading.Event()
        original = pe._locate
        first: list[str] = []

        def scheduling_point(function, bound=pe._NO_BINDINGS):
            """_raise を探す最初の thread を、もう一方が終わるまで止める(順だけを決める)。"""
            if function.__name__ == "_raise" and not first:
                first.append(threading.current_thread().name)
                first_in.set()
                other_done.wait(timeout=5)
            return original(function, bound)

        pe._locate = scheduling_point

        def analysis(program: str) -> None:
            report = analyze_program(f"{name}.programs:{program}")
            if program == "p2":
                first_in.wait(timeout=2)
            out[program] = [g.effect.__name__ for g in check_coverage(report, []).gaps]
            if program == "p2":
                other_done.set()

        threads = [threading.Thread(target=analysis, args=(p,), name=p) for p in ("p1", "p2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(out)
