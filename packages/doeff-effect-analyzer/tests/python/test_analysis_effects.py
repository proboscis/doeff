"""The analysis as effects (ADR-DOE-API-001 R24・card ki-593f9f44c501): a Program asks with ``AnalyzeProgram`` /
``AnalyzeHandler`` / ``AnalyzeEnv`` / ``CheckCoverage`` and the handler ``effect_analysis()`` answers with what the
functions inside the package read; the root shows the effects, the factory and the answer types only.

Failure cases: a target that cannot be analyzed is thrown back where the effect was performed (a Program can
catch it); bindings for a Program read before are refused; ``FoldCarried()`` folds no carried Program in; with
``result_cache=True`` a second coverage check of the same named Program is read from the store, and a change of
the Program's module (or an unnamed target) analyzes again.
"""

import functools
import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar
from unittest import mock

import doeff_effect_analyzer
import pytest
from doeff_core_effects import try_handler
from doeff_effect_analyzer import (
    AnalyzeEnv,
    AnalyzeHandler,
    AnalyzeProgram,
    CheckCoverage,
    Coverage,
    FoldCarried,
    ProgramEffects,
    effect_analysis,
)
from doeff_effect_analyzer import _effects as effects
from doeff_effect_analyzer import _handler_effects as he
from doeff_effect_analyzer import _program_effects as pe

from doeff import EffectBase, EffectGenerator, Try, do, run
from doeff.result import Result

_Answer = TypeVar("_Answer")

ROOT_NAMES = {
    "AnalyzeEnv",
    "AnalyzeHandler",
    "AnalyzeProgram",
    "Basis",
    "CheckCoverage",
    "Coverage",
    "FoldCarried",
    "Gap",
    "HandlerEffects",
    "ProgramEffects",
    "SuppliedDeclaration",
    "effect_analysis",
}

JOB = """
from doeff import do
from doeff_core_effects import Ask, Spawn, Try, reader

@do
def child():
    value = yield Ask("child")
    return value

@do
def job():
    first = yield Ask("x")
    yield Spawn(child())
    tried = yield Try(child())
    return first, tried

def env():
    return [reader()]
"""


def answered(effect: EffectBase[_Answer], **options: bool) -> _Answer:
    """Read one analysis effect under the analyzer's handler."""
    return run(effect_analysis(**options)(effect))


def test_the_root_shows_the_effects_the_factory_and_the_answer_types_only() -> None:
    shown = {name for name in dir(doeff_effect_analyzer) if not name.startswith("_")}
    assert shown - {"TYPE_CHECKING", "import_module"} == ROOT_NAMES
    with pytest.raises(AttributeError):
        _ = doeff_effect_analyzer.analyze  # the Rust core stays in _native


def test_the_old_paths_keep_only_the_names_the_other_repos_import() -> None:
    old_program = importlib.import_module("doeff_effect_analyzer.program_effects")
    old_handler = importlib.import_module("doeff_effect_analyzer.handler_effects")
    assert {name for name in vars(old_program) if not name.startswith("_")} == {
        "ProgramEffects",
        "analyze_program",
        "qualified_name",
        "runs_where_performed",
    }
    assert {name for name in vars(old_handler) if not name.startswith("_")} == {"check_coverage"}
    assert old_program.analyze_program is pe.analyze_program
    assert old_handler.check_coverage is he.check_coverage


def test_the_effects_answer_what_the_functions_inside_read(
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    report = answered(AnalyzeProgram(f"{pkg}.jobs:job"))
    assert isinstance(report, ProgramEffects)
    assert report.effect_names == pe.analyze_program(f"{pkg}.jobs:job").effect_names
    scheduler = answered(
        AnalyzeHandler("doeff_core_effects._scheduler:scheduled", name="scheduled")
    )
    assert scheduler.name == "scheduled"
    assert (
        scheduler.handled == he.analyze_handler("doeff_core_effects._scheduler:scheduled").handled
    )
    env = answered(AnalyzeEnv(f"{pkg}.jobs:env"))
    assert isinstance(env, tuple)
    assert [h.name for h in env] == [h.name for h in he.analyze_env(f"{pkg}.jobs:env")]
    coverage = answered(
        CheckCoverage(report, env=(scheduler, *env), fold=FoldCarried(declared=True))
    )
    assert isinstance(coverage, Coverage)
    assert coverage == he.check_coverage(report, [scheduler, *env], include=pe.runs_where_performed)


def test_a_program_read_here_answers_like_one_read_before(
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    before = answered(AnalyzeProgram(f"{pkg}.jobs:job"))
    assert answered(CheckCoverage(f"{pkg}.jobs:job")) == answered(CheckCoverage(before))


def test_the_fold_counts_only_the_carriers_it_names(
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    report = answered(AnalyzeProgram(f"{pkg}.jobs:job"))
    carriers = {pe.qualified_name(c.carrier).rsplit(".", 1)[1] for c in report.carried}
    assert carriers >= {"Spawn", "Try"}
    folds = {
        "none": FoldCarried(),
        "declared": FoldCarried(declared=True),
        "name": FoldCarried(names=("Spawn",)),
        "qualified": FoldCarried(
            names=(
                pe.qualified_name(
                    next(c.carrier for c in report.carried if c.carrier.__name__ == "Spawn")
                ),
            )
        ),
    }
    counted = {
        key: {c.carrier.__name__ for c in report.carried if fold(c.carrier)}
        for key, fold in folds.items()
    }
    assert counted == {
        "none": set(),
        "declared": {"Spawn", "Try"},
        "name": {"Spawn"},
        "qualified": {"Spawn"},
    }
    assert report.residual_with(FoldCarried(declared=True)) == report.residual_with(
        pe.runs_where_performed
    )


def test_a_target_that_cannot_be_analyzed_is_thrown_back_where_it_was_asked() -> None:
    @do
    def asks() -> EffectGenerator[Result[ProgramEffects]]:
        tried = yield Try(AnalyzeProgram("no_such_module_here:job"))
        return tried

    outcome = run(try_handler(effect_analysis()(asks())))
    assert outcome.is_err()
    assert isinstance(outcome.error, ModuleNotFoundError)
    with pytest.raises(TypeError, match="not a Program function"):
        answered(AnalyzeProgram(len))


def test_bindings_for_a_program_read_before_are_refused(
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    report = answered(AnalyzeProgram(f"{pkg}.jobs:job"))
    with pytest.raises(TypeError, match="read already"):
        CheckCoverage(report, bindings={"foundation": None})


def test_the_analyzer_reads_its_own_handler() -> None:
    read = answered(AnalyzeHandler(effect_analysis))
    assert read.known, read.unresolved
    assert read.handled == frozenset({AnalyzeProgram, AnalyzeHandler, AnalyzeEnv, CheckCoverage})


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "trees"
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(directory))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_RESULT_CACHE", raising=False)
    return directory


def counting_reads(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """The handler's reads of a Program (the analysis run in full, not read from the store)."""
    reads = mock.Mock(wraps=effects.analyze_program)
    monkeypatch.setattr(effects, "analyze_program", reads)
    return reads


def test_a_kept_coverage_answer_is_read_again_while_the_program_is_the_same(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    reads = counting_reads(monkeypatch)
    jobs = importlib.import_module(f"{pkg}.jobs")
    asked = CheckCoverage(jobs.job, fold=FoldCarried(declared=True))
    first = answered(asked, result_cache=True)
    second = answered(asked, result_cache=True)
    assert first == second
    assert reads.call_count == 1
    answered(asked)  # without the store: read in full
    assert reads.call_count == 2
    answered(CheckCoverage(functools.partial(jobs.job)), result_cache=True)
    answered(CheckCoverage(functools.partial(jobs.job)), result_cache=True)
    assert reads.call_count == 4  # a partial has no name to key it on


def test_a_changed_program_is_analyzed_again(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_package: Callable[[Mapping[str, str]], str],
) -> None:
    pkg = make_package({"jobs.py": JOB})
    reads = counting_reads(monkeypatch)
    jobs = importlib.import_module(f"{pkg}.jobs")
    answered(CheckCoverage(jobs.job), result_cache=True)
    source = Path(jobs.__file__)
    source.write_text(source.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    answered(CheckCoverage(jobs.job), result_cache=True)
    assert reads.call_count == 2
