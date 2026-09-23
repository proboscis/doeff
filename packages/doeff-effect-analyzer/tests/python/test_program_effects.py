"""Effect sets of Programs, handler clauses and env coverage, read without running.

Regression targets (agora-controllers wt/doeff-worker-pod, 2026-09-23): the Rust
core reported only ``ask:worker`` for a Python service whose effects are
project-defined classes, and nothing at all for Hy, because it matched a fixed
vocabulary of call names and did not look inside a user macro (``defservice``)
that wraps ``defk``.
"""

import importlib
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from doeff_effect_analyzer.handler_effects import analyze_env, analyze_handler, check_coverage
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class ReadBoard(EffectBase):
    prefix: str


@dataclass(frozen=True)
class WriteBoard(EffectBase):
    key: str
    value: object


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float


@dataclass(frozen=True)
class Tick(EffectBase):
    pass


class WriteFamily(EffectBase):
    pass


@dataclass(frozen=True)
class WriteAudit(WriteFamily):
    line: str
"""

MACROS_HY = """\
(defmacro defservice [name params #* body]
  ;; A project macro that wraps defk, like agora's worker `defservice`.
  (setv program-name (hy.models.Symbol (+ (str name) "-program")))
  `(do
     (require doeff-hy.macros [defk <-])
     (defk ~program-name ~params ~@body)
     (setv ~name {"program" ~program-name})))
"""

SERVICES_HY = """\
(require doeff-hy.macros [defk <-])
(require {pkg}.macros [defservice])
(import {pkg}.effects [ReadBoard WriteBoard :as Put Nap])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn])

(defk summarize [conv]
  {{:pre [(: conv str)] :post [(: % str)]}}
  (<- who str (Ask "worker"))
  (+ conv who))

(defk put-row [key]
  {{:pre [(: key str)] :post [(: % bool)]}}
  (<- ok bool (Put key {{"state" "queued"}}))
  ok)

(defservice runner [cycles]
  {{:pre [(: cycles int)] :post [(: % int)]}}
  (<- rows dict (ReadBoard "turn/"))
  (<- (put-row "turn/c1/0"))
  (<- (Spawn (summarize "c1")))
  (<- (Nap 1.0))
  (len rows))

(defn opaque [prog]
  (yield prog))
"""

PROGRAMS_PY = """\
from collections.abc import Generator

from doeff import do

from {pkg}.effects import Nap, ReadBoard, WriteAudit, WriteBoard


def helper(key):
    return (yield WriteBoard(key, 1))


@do
def placer(n):
    rows = yield from ReadBoard("turn/")
    yield from helper("turn/a")
    yield WriteAudit("placed")
    yield Nap(1.0) if n else None
    return len(rows)
"""

HANDLERS_HY = """\
(require doeff-hy.macros [defhandler <-])
(import {pkg}.effects [ReadBoard WriteBoard Nap Tick WriteFamily])
(import doeff_core_effects.effects [Await])

(defhandler board-memory [store]
  (ReadBoard [prefix] (resume (dict store)))
  (WriteBoard [key value] (resume True)))

(defhandler nap-clock []
  (Nap [seconds]
    (<- (Tick))
    (resume None)))

(defhandler ticker []
  (Tick [] (resume None)))

(defhandler audit-sink []
  (WriteFamily [] (resume None)))
"""

HANDLERS_PY = """\
import functools

from doeff import Pass, Resume, do

from {pkg}.effects import Tick


def _dispatch(prefix, effect, k):
    if isinstance(effect, Tick):
        return Resume(k, None)
    return Pass(effect, k)


def tick_runtime():
    return functools.partial(_dispatch, "x")
"""

ENVS_HY = """\
(import doeff_core_effects.handlers [reader])
(import {pkg}.handlers [board-memory nap-clock ticker audit-sink])


(defn full-env [config ctx]
  [(ticker) (reader {{"worker" "w"}}) (board-memory {{}}) (nap-clock) (audit-sink)])


(defn no-ticker-env [config ctx]
  [(reader {{"worker" "w"}}) (board-memory {{}}) (nap-clock) (audit-sink)])


(defn local-import-env [config ctx]
  (import {pkg}.handlers_py [tick-runtime])
  [(tick-runtime) (board-memory {{}}) (nap-clock) (audit-sink)])
"""


@pytest.fixture
def pkg(tmp_path: Path):
    import hy  # noqa: F401 - .hy import hook

    name = f"fx_{uuid.uuid4().hex[:8]}"
    root = tmp_path / name
    root.mkdir()
    files = {
        "__init__.py": "",
        "effects.py": EFFECTS_PY,
        "macros.hy": MACROS_HY,
        "services.hy": SERVICES_HY,
        "programs.py": PROGRAMS_PY,
        "handlers.hy": HANDLERS_HY,
        "handlers_py.py": HANDLERS_PY,
        "envs.hy": ENVS_HY,
    }
    for filename, text in files.items():
        body = text.format(pkg=name) if "{pkg}" in text else text
        (root / filename).write_text(textwrap.dedent(body), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    yield name
    sys.path.remove(str(tmp_path))
    for module in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[module]


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_project_defined_effects_are_found_in_a_python_program(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:placer")

    assert _short(report.effect_names) == {"ReadBoard", "WriteBoard", "WriteAudit", "Nap"}
    via = {use.effect.__name__: use.via for use in report.effects}
    assert via["WriteBoard"] == (f"{pkg}.programs.helper",)
    assert report.unresolved == ()


def test_hy_service_defined_by_a_user_macro_wrapping_defk(pkg: str) -> None:
    # `runner` exists only as the output of the project macro `defservice`.
    report = analyze_program(f"{pkg}.services:runner_program")

    # WriteBoard is imported under the alias `Put` and reached through `put-row`.
    assert _short(report.effect_names) == {"ReadBoard", "WriteBoard", "Spawn", "Nap"}
    [carried] = report.carried
    assert carried.carrier.__name__ == "Spawn"
    assert _short(carried.program.effect_names) == {"Ask"}
    assert report.unresolved == ()


def test_what_cannot_be_followed_is_reported_not_dropped(pkg: str) -> None:
    report = analyze_program(f"{pkg}.services:opaque")

    assert report.effects == ()
    [item] = report.unresolved
    assert item.text == "prog"


def test_handler_clauses_and_what_they_perform(pkg: str) -> None:
    board = analyze_handler(f"{pkg}.handlers:board_memory")
    clock = analyze_handler(f"{pkg}.handlers:nap_clock")

    assert {c.__name__ for c in board.handled} == {"ReadBoard", "WriteBoard"}
    [nap] = clock.clauses
    assert nap.handles.__name__ == "Nap"
    assert _short(nap.emits.effect_names) == {"Tick"}


def test_a_factory_returning_a_partial_of_a_dispatch_function(pkg: str) -> None:
    handler = analyze_handler(f"{pkg}.handlers_py:tick_runtime")

    assert {c.__name__ for c in handler.handled} == {"Tick"}


def test_env_covers_every_effect_including_what_handlers_perform(pkg: str) -> None:
    program = analyze_program(f"{pkg}.services:runner_program")
    scheduler = analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")

    coverage = check_coverage(program, [scheduler, *analyze_env(f"{pkg}.envs:full_env")])

    assert coverage.gaps == ()
    assert coverage.unknown_handlers == ()
    assert coverage.complete


def test_a_missing_handler_is_named_before_running(pkg: str) -> None:
    program = analyze_program(f"{pkg}.services:runner_program")
    scheduler = analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")

    coverage = check_coverage(program, [scheduler, *analyze_env(f"{pkg}.envs:no_ticker_env")])

    # Tick is not performed by the service; the clock handler performs it for Nap.
    assert [(gap.effect.__name__, gap.origin) for gap in coverage.gaps] == [("Tick", "nap_clock()")]


def test_a_parent_class_clause_handles_subclasses_and_carried_programs_can_be_folded(
    pkg: str,
) -> None:
    program = analyze_program(f"{pkg}.programs:placer")
    env = analyze_env(f"{pkg}.envs:full_env")

    assert check_coverage(program, env).gaps == ()  # WriteAudit ⊂ WriteFamily

    service = analyze_program(f"{pkg}.services:runner_program")
    folded = service.effect_types_with(lambda carrier: carrier.__name__ == "Spawn")
    assert "Ask" in {effect.__name__ for effect in folded}


def test_env_handlers_imported_inside_the_builder(pkg: str) -> None:
    env = analyze_env(f"{pkg}.envs:local_import_env")

    assert [handler.known for handler in env] == [True, True, True, True]
    assert {c.__name__ for c in env[0].handled} == {"Tick"}


def test_cli_coverage_exit_code_names_the_gap(pkg: str, capsys: pytest.CaptureFixture[str]) -> None:
    from doeff_effect_analyzer.cli import main

    outer = ["--outer", "doeff_core_effects.scheduler:scheduled"]
    target = f"{pkg}.services:runner_program"

    assert main(["coverage", target, "--env", f"{pkg}.envs:full_env", *outer]) == 0
    assert main(["coverage", target, "--env", f"{pkg}.envs:no_ticker_env", *outer]) == 1
    assert "no handler: Tick (performed by nap_clock())" in capsys.readouterr().out
