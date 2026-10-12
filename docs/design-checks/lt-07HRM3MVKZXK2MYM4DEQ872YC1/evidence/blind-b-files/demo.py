"""盲検 B の失敗例の確かめ(作業用 — 共有の source を書かない)。

使い方(作業木の根で): uv run --no-sync python /tmp/blind-b-lt07hr/demo.py nested|threads|pool

- nested : 関数の中の require を持たない module A の展開の中で、関数の中の require を持つ module B が compile される時、
           A の記録に B の局所の macro が混ざるか(S3・S4)。
- threads: 2 つの thread で、別々の module の展開(effect analyzer の _expand_hy と同じ形 — macro_recording + hy_compile)が
           重なり、先に始まった方が先に終わる順の時、後の module の関数の中の require の macro が記録に入るか(S3)。
- pool   : 順を決めずに thread pool で並べた時に、記録から漏れる module が出るか(実測の頻度)。
"""

import importlib.machinery
import os
import sys
import tempfile
import threading
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["DOEFF_HY_CODE_STORE"] = "off"
sys.dont_write_bytecode = True

import hy  # noqa: E402
import hy.compiler  # noqa: E402
from doeff_hy_bytecode_guard import (  # noqa: E402
    macro_recording,
    record_is_current_here,
    records,
    source_to_code_as_import,
)

WORK = Path("/tmp/blind-b-lt07hr")


def make_package() -> tuple[str, Path]:
    root = Path(tempfile.mkdtemp(dir=WORK, prefix="pkg-"))
    package = f"blindb_{uuid.uuid4().hex[:8]}"
    directory = root / package
    directory.mkdir()
    (directory / "__init__.py").write_text("")
    (directory / "m.hy").write_text("(defmacro answer [] 11)\n")
    (directory / "b.hy").write_text(
        f"(defmacro wrap [x] x)\n(defn f []\n  (require {package}.m [answer])\n  (answer))\n"
    )
    (directory / "a.hy").write_text(f"(require {package}.b [wrap])\n(setv value (wrap 1))\n")
    sys.path.insert(0, str(root))
    return package, directory


def forget(package: str) -> None:
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        del sys.modules[name]


def change_answer(directory: Path) -> None:
    (directory / "m.hy").write_text("(defmacro answer [] 12345)\n")  # 大きさも替える(file の sha256 の覚えの鍵)


def shown(record: records.MacroRecord, package: str) -> list[tuple[str, str]]:
    return [(used.module.replace(package, "<pkg>"), used.name) for used in record.macros]


def nested() -> None:
    package, directory = make_package()
    path = directory / "a.hy"
    loader = importlib.machinery.SourceFileLoader(f"{package}.a", str(path))
    code = source_to_code_as_import(loader, path.read_bytes(), str(path))
    record = records.record_of(code)
    assert record is not None
    print("A(関数の中の require なし)の記録の macro:", shown(record, package))
    print("A の記録の全部 require の提供元:", [(p.module.replace(package, "<pkg>"), p.prefix) for p in record.providers])
    forget(package)
    change_answer(directory)
    print("A が使わない <pkg>.m の answer を替えた後、A の記録は今に合うか:", record_is_current_here(record))
    forget(package)


def expand(name: str, source: str, filename: str, enter=None, before=None) -> records.MacroRecord:
    """effect analyzer の _expand_hy と同じ形(macro_recording(scratch) の中で hy_compile)— enter / before は順を決める待ち。"""
    scratch = types.ModuleType(name)
    scratch.__file__ = filename
    with macro_recording(scratch) as recording:
        if enter is not None:
            enter()
        if before is not None:
            before()
        hy.compiler.hy_compile(hy.read_many(source, filename=filename), scratch, filename=filename, source=source)
    return recording.record(source)


def threads() -> None:
    package, directory = make_package()
    quiet = "(setv value 1)\n"
    local = f"(defn f []\n  (require {package}.m [answer])\n  (answer))\n(setv value (f))\n"
    first_entered, second_entered, first_exited = threading.Event(), threading.Event(), threading.Event()
    out: dict[str, records.MacroRecord] = {}

    def run_quiet() -> None:
        out["quiet"] = expand(
            f"{package}.quiet", quiet, str(directory / "quiet.hy"),
            enter=first_entered.set, before=lambda: second_entered.wait(10),
        )
        first_exited.set()

    def run_local() -> None:
        first_entered.wait(10)
        out["local"] = expand(
            f"{package}.local", local, str(directory / "local.hy"),
            enter=second_entered.set, before=lambda: first_exited.wait(10),
        )

    workers = [threading.Thread(target=run_quiet), threading.Thread(target=run_local)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    print("local(関数の中の require あり)の記録の macro:", shown(out["local"], package))
    print("quiet(macro なし)の記録の macro:", shown(out["quiet"], package))
    from hy.compiler import HyASTCompiler

    port = HyASTCompiler.new_local_state
    cells = [type(cell.cell_contents).__name__ for cell in (port.__closure__ or ())]
    print("両方の展開が終わった後の HyASTCompiler.new_local_state:", port.__module__, port.__qualname__, cells)
    forget(package)
    change_answer(directory)
    print("local が使った <pkg>.m の answer を替えた後、local の記録は今に合うか:", record_is_current_here(out["local"]))
    forget(package)


def pool() -> None:
    package, directory = make_package()
    # 長めの quiet(局所の状態を多く開く)と、関数の中の require を持つ local を交互に並べる。
    quiet = "".join(f"(defn q{i} [x] (lfor y (range x) (fn [] y)))\n" for i in range(200))
    local = f"(defn f []\n  (require {package}.m [answer])\n  (answer))\n(setv value (f))\n"
    jobs = [(f"{package}.{kind}{i}", src, str(directory / f"{kind}{i}.hy"))
            for i in range(40) for kind, src in (("quiet", quiet), ("local", local))]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda job: (job[0], expand(*job)), jobs))
    locals_ = [(name, record) for name, record in results if ".local" in name]
    missed = [name for name, record in locals_ if not any(used.name == "answer" for used in record.macros)]
    quiet_mixed = [name for name, record in results if ".quiet" in name and any(used.name == "answer" for used in record.macros)]
    print(f"local の記録 {len(locals_)} 本のうち answer の漏れ: {len(missed)} 本")
    print(f"quiet の記録 {len(results) - len(locals_)} 本のうち answer の混入: {len(quiet_mixed)} 本")
    forget(package)


if __name__ == "__main__":
    print("GIL:", sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else "n/a", " Hy:", hy.__version__)
    {"nested": nested, "threads": threads, "pool": pool}[sys.argv[1]]()
