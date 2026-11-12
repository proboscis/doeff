"""The guard names the modules a Hy .pyc's record depends on, so the bytecode preparation tool can keep a .pyc carried over from the tree of a root with the same
dependencies without the full check when none of those modules changed (ADR-DOE-BUILD-002 R3c).

The worker's baking tool takes the function from the module owning the guard's get_code hook (like bytecode_is_current — R7) and falls back to the full check when the
owner does not offer it. So the answer must cover every module the check reads — the defining module of a used macro and the modules its closure depended on (a helper the
macro calls) — or the tool would keep a stale .pyc. A .pyc whose record it cannot judge answers None (the tool then checks fully), and Python source answers the empty set.
The compile runs in a child process (installing the guard and importing the package do not leak into this process).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PROBE = """\
import importlib.machinery
import importlib.util
import json
import marshal
import sys

sys.path.insert(0, sys.argv[1])
import hy  # noqa: F401 — the guard's .pth wraps the loader; Hy's importer is needed for .hy sources

from doeff_hy_bytecode_guard._loader_hooks import install

install()
owner = sys.modules[importlib.machinery.SourceFileLoader.get_code.__module__]


def pyc_of(name):
    spec = importlib.util.find_spec(name)
    with open(spec.origin, "rb") as source:
        data = source.read()
    code = owner.source_to_code_as_import(spec.loader, data, spec.origin)
    head = importlib.util.MAGIC_NUMBER + (3).to_bytes(4, "little") + importlib.util.source_hash(data)
    return spec.origin, head + marshal.dumps(code)


def answer(path, pyc):
    found = owner.bytecode_dependency_modules(path, pyc)
    return None if found is None else sorted(found)


user = pyc_of("pkg.user")
plain = pyc_of("pkg.plain")
print(json.dumps({
    "user": answer(*user),
    "plain": answer(*plain),
    "python": answer(sys.argv[1] + "/pkg/__init__.py", b"anything"),
    "broken": answer(user[0], user[1][:16] + b"not marshal"),
}))
"""


def _write_package(root: Path) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/helpers.hy": "(defn base [] 10)\n",
        "pkg/macros.hy": "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 1))\n",
        "pkg/user.hy": "(require pkg.macros [answer])\n(setv value (answer))\n",
        "pkg/plain.hy": "(setv value 3)\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_the_answer_covers_the_macro_module_and_its_helper_and_refuses_what_it_cannot_judge(tmp_path: Path) -> None:
    """The user of a macro depends on the macro's module and the helper module its closure calls (a change to either must send the .pyc to the full check); a Hy source
    without macros and Python source depend on nothing; a .pyc whose body is not code cannot be judged (None)."""
    _write_package(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    answers = json.loads(completed.stdout.splitlines()[-1])
    assert {"pkg.macros", "pkg.helpers"} <= set(answers["user"]), answers
    assert answers["plain"] == [] and answers["python"] == [], answers
    assert answers["broken"] is None, answers


def test_the_owner_of_the_get_code_hook_offers_the_answer() -> None:
    """The baking tool looks the function up on the owner of the get_code hook (R7) — the owner must export it under this name with (path, pyc)."""
    import importlib.machinery
    import inspect

    from doeff_hy_bytecode_guard._loader_hooks import install

    install()
    owner = sys.modules[importlib.machinery.SourceFileLoader.get_code.__module__]
    entry = getattr(owner, "bytecode_dependency_modules", None)
    assert callable(entry)
    assert list(inspect.signature(entry).parameters) == ["path", "pyc"]
