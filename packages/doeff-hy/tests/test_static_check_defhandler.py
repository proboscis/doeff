"""Type failure cases for the defhandler expansion.

Previously the functions produced by the defhandler expansion had no types, so strict doeff-hy-check reported about 20 reds per handler that
the writer could not fix: the clause-dispatch function's argument k and its answer (Generator[Transfer, Unknown, Unknown]), the body-receiving function's argument
__doeff_body__ and its answer (WithHandler[Unknown]), the check of how clauses end (clause_endings has no visible types in .hy),
unused Resume / Transfer imports, and "always true" isinstance checks on effects annotated with the clause type. defk's (! ...) was also
a yield value (Unknown) of an unannotated generator, producing an Unknown red at every use site.

- Under strict, samples of defhandler with arguments, with :when, with an EffectBase fence, or with <- inside a clause, plus defk using (! ...), produce 0 reds.
- Types are not escaped: a wrong clause answer type, or a wrong answer type of a Program wrapped by a handler, is still red.
- Only the expansion for type checking changes shape; the runtime expansion (no annotations, imports of Resume / Transfer / clause_endings) stays as before.
"""

import ast
import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Name [(get EffectBase str)]
  #^ str prefix)

(defclass [(dataclass :frozen True)] Pair [(get EffectBase int)]
  #^ int left
  #^ int right)
"""

PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff [EffectBase])
(import probe_effects [Tick Name Pair])

(defhandler counting
  "Answers Tick and Name."
  {:tags {:context "probe" :role "protocol"}}
  (Tick [step]
    (val doubled (* step 2))
    (resume {TICK_ANSWER}))
  (Name [prefix]
    :when (> (len prefix) 0)
    (<- n int (Tick 1))
    (resume (+ prefix (str n)))))

(defhandler scaled [#^ int factor]
  (Tick [step]
    (transfer (* step factor))))

(defhandler only-tick
  (Tick [step]
    (resume step)))

(defhandler everything-else
  (EffectBase []
    :when (not (isinstance effect Tick))
    (reperform effect)))

(defhandler left-only
  "Pair's right is unused in the body — fields are bound by name, so the writer cannot rename it."
  (Pair [{PAIR_FIELDS}]
    (resume left)))

(defk use-handlers [start]
  {:pre [(: start int)] :post [(: % {USE_TYPE})]}
  (<- n (counting ((scaled 3) (only-tick (everything-else (Tick start))))))
  n)

(defk bang-sum [start]
  {:pre [(: start int)] :post [(: % int)]}
  (+ (! (Tick start)) (! (counting (Tick 2)))))
"""

BASE = {"TICK_ANSWER": "(+ doubled 1)", "USE_TYPE": "int", "PAIR_FIELDS": "left right"}


def _render(change: dict[str, str]) -> str:
    text = PROBE
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


@needs_pyright
def test_strict_has_no_error_from_defhandler_expansions(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
def test_wrong_clause_answer_is_still_caught(tmp_path: Path) -> None:
    # Tick is an effect answered with int — resuming from a clause with str is an argument type mismatch in typed_transfer (the clause type is not escaped).
    text = _render({"TICK_ANSWER": "(str doubled)"})
    errors = _check(tmp_path, text)
    assert ("reportArgumentType", _line_of(text, "(resume (str doubled))")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_handled_program_keeps_the_body_answer_type(tmp_path: Path) -> None:
    # The answer of a Program wrapped by a handler is the body's answer (int — HandledScope[HandledAnswer]), and the annotation does not let the type escape:
    # using the bound value as the answer of a defk that returns str is red (previously only pyright's call-site inference carried it).
    text = _render({"USE_TYPE": "str"})
    errors = _check(tmp_path, text)
    assert ("reportAssignmentType", _line_of(text, "  n)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_field_the_body_does_not_use_is_not_an_unused_variable(tmp_path: Path) -> None:
    # The body does not use the clause field right in [left right]. Previously the type-checking expansion merely bound right = effect.right,
    # which gave a reportUnusedVariable red under strict (the name is the field name, so the writer cannot fix it).
    text = _render({})
    errors = _check(tmp_path, text)
    assert [d for d in errors if d["rule"] == "reportUnusedVariable"] == [], errors


@needs_pyright
def test_misspelled_field_is_still_caught(tmp_path: Path) -> None:
    # Even if an unused field is marked as "read", a misspelled field name stays red as an effect attribute read.
    text = _render({"PAIR_FIELDS": "left rihgt"})
    errors = _check(tmp_path, text)
    assert ("reportAttributeAccessIssue", _line_of(text, "(Pair [left rihgt]")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


def _expand(source: str) -> str:
    # importing doeff_hy sets up the Hy importer and macros (an import for its side effect)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_static_view_annotates_only_the_static_expansion() -> None:
    from doeff_hy._static_view import static_view

    runtime = _expand(_render({}))
    with static_view():
        static = _expand(_render({}))
    # Expansion for type checking: annotate the clause-dispatch and body-receiving functions; the clause-ending check uses typed names at the top of the module
    assert (
        "(_doeff_effect: object, _doeff_k: _doeff_Continuation) -> _doeff_ClauseRun:" in static
    ), static
    assert (
        "def __doeff_handler_fn__(__doeff_body__: _doeff_HandlerBody[_doeff_HandledAnswer])"
        " -> _doeff_HandledScope[_doeff_HandledAnswer]:"
    ) in static
    assert "doeff_hy._clause_endings" not in static
    assert "from doeff import Pass as _doeff_Pass" in static
    assert "Resume as" not in static
    assert "Transfer as" not in static
    # (! e) is the same _doeff_perform as <- (the yield value is Unknown in an unannotated generator)
    assert "_doeff_perform(Tick(start)) + _doeff_perform(counting(Tick(2)))" in static
    # Runtime expansion: no annotations; the effect is annotated with the clause type (VM narrowing); Resume / Transfer and clause_endings are imported
    assert "def __doeff_handler_fn__(__doeff_body__):" in runtime, runtime
    assert (
        "from doeff import Resume as _doeff_Resume, Transfer as _doeff_Transfer, Pass as _doeff_Pass"
        in runtime
    )
    assert "from doeff_hy._clause_endings import check_clause_endings_once" in runtime
    assert "from doeff_hy._clause_endings import fell_through" in runtime
    for name in ("_doeff_Continuation", "_doeff_ClauseRun", "_doeff_HandlerBody", "_doeff_perform"):
        assert name not in runtime
    # The "read" mark for field binding (_ = field) exists only in the type-checking expansion
    assert "right = _doeff_effect.right\n" in static, static
    assert "_ = right\n" in static, static
    assert "right = _doeff_effect.right\n" in runtime, runtime
    assert "_ = right" not in runtime, runtime


def test_every_handler_name_is_declared_in_the_stub() -> None:
    # The names in the table (static_view.HANDLER_STATIC_NAMES) are declared in static_types.pyi (without them annotations and calls become Unknown)
    from doeff_hy import _static_view as static_view

    stub = Path(static_view.__file__).with_name("static_types.pyi")
    body = ast.parse(stub.read_text(encoding="utf-8")).body
    declared = (
        {
            node.target.id
            for node in body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        | {
            target.id
            for node in body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        | {node.name for node in body if isinstance(node, ast.FunctionDef)}
    )
    assert [name for name in static_view.HANDLER_STATIC_NAMES if name not in declared] == []


# A module where the writer named the names the expansion reads (Transfer, Pass, Put) as their own effects. Previously the type-checking expansion
# imported `Pass` under its bare name, sharing one name with the writer's `Pass` effect. The expansion reads only aliases with the `_doeff_` prefix.
NAMED_EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Transfer [(get EffectBase int)]
  #^ int amount)

(defclass [(dataclass :frozen True)] Pass [(get EffectBase int)]
  #^ int reason)

(defclass [(dataclass :frozen True)] Put [(get EffectBase int)]
  #^ str key)
"""

NAMED_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import probe_effects [Transfer Pass Put])

(defhandler ledger
  (session var posted 0)
  (Transfer [amount]
    (:= posted (+ posted amount))
    (resume posted))
  (Pass [reason]
    :when (> reason 0)
    (transfer reason))
  (Put [key]
    (reperform effect)))

(defk post-twice [amount]
  {:pre [(: amount int)] :post [(: % int)]}
  (<- first int (ledger (Transfer amount)))
  (<- second int (ledger (Pass first)))
  second)
"""


@needs_pyright
def test_writer_effects_named_like_the_expansion_names_are_not_an_error(tmp_path: Path) -> None:
    from doeff_hy._static_check import main

    (tmp_path / "probe_effects.hy").write_text(NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(NAMED_PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    assert [d for d in found if d["severity"] == "error"] == []


def test_static_expansion_reads_only_aliases_of_the_expansion_names() -> None:
    """The type-checking expansion does not share a name with the writer's Transfer, Pass, and Put effects — the names it reads are `_doeff_` aliases."""
    from doeff_hy._static_view import static_view

    with static_view():
        static = _expand(NAMED_PROBE)
    assert "_doeff_Pass(_doeff_effect, _doeff_k)" in static, static
    assert "_doeff_WithHandlerType(" in static, static
    assert "_doeff_Put(" in static, static
    assert "from doeff import Pass as _doeff_Pass" in static, static


# A module where the writer named clause fields the same as the expansion's local names `k` and `effect`. Previously field binding overwrote the clause-dispatch
# function's arguments `effect` and `k`, producing a red for assigning `int` to `k` of the continuation type `Continuation`. The expansion's local names are `_doeff_effect` and `_doeff_k`;
# only a clause where the writer wrote `effect` or `k` can read that name as the clause's effect or continuation.
FIELD_NAMED_EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Kv [(get EffectBase int)]
  #^ int k
  #^ int v)

(defclass [(dataclass :frozen True)] Note [(get EffectBase int)]
  #^ int effect
  #^ int extra)

(defclass [(dataclass :frozen True)] Bump [(get EffectBase int)]
  #^ int n)
"""

FIELD_NAMED_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff [EffectBase])
(import probe_effects [Kv Note Bump])

(defhandler keyed
  (Kv [k v]
    :when (> k 5)
    (resume (+ k v)))
  (Note [effect extra]
    (resume (+ effect extra))))

(defhandler fence
  (EffectBase []
    :when (not (isinstance effect Kv))
    (reperform effect))
  (Kv [k v]
    (resume (+ k v))))

(defhandler reads-its-effect
  (Bump [n]
    (resume (+ n effect.n)))
  (Note [extra]
    (resume (+ extra (sum (lfor k [1 2 3] k))))))

(defk use-keyed [start]
  {:pre [(: start int)] :post [(: % int)]}
  (<- first int (keyed (Kv start 1)))
  (<- second int (fence (keyed (Note start 2))))
  (+ first second))
"""


@needs_pyright
def test_clause_fields_named_k_and_effect_are_not_an_error(tmp_path: Path) -> None:
    from doeff_hy._static_check import main

    (tmp_path / "probe_effects.hy").write_text(FIELD_NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(FIELD_NAMED_PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    assert [d for d in found if d["severity"] == "error"] == []


def test_static_expansion_binds_the_writers_effect_and_k_only_where_the_clause_writes_them() -> (
    None
):
    """A clause where the writer does not write `effect` or `k` does not bind the bare names even in the type-checking expansion. Only a handler with a clause that does write it binds, at
    the head of the clause-dispatch function, the value before `effect` is narrowed, and the check of the written clause also narrows `effect` to the clause type (readable with the narrowed type). `k` is declared and
    read by the clause that writes it. A clause whose field has the same name is not bound; the field takes precedence."""
    from doeff_hy._static_view import static_view

    with static_view():
        static = _expand(
            FIELD_NAMED_EFFECTS
            + FIELD_NAMED_PROBE.replace("(import probe_effects [Kv Note Bump])", "")
        )
    assert "isinstance(_doeff_effect, EffectBase) and isinstance(effect, EffectBase)" in static, (
        static
    )
    assert "isinstance(_doeff_effect, Bump) and isinstance(effect, Bump)" in static, static
    # The handlers with a clause that writes `effect` are the two: fence and reads-its-effect. keyed has only a clause whose field is `effect`, so it does not bind
    assert static.count("    effect = _doeff_effect\n") == 2, static
    assert "k: _doeff_Continuation = _doeff_k" in static, static
    # A clause with field k binds the field to `k` (does not overwrite the continuation) — the Pass that reads `_doeff_k` remains
    assert "k = _doeff_effect.k" in static, static
    assert "_doeff_Pass(_doeff_effect, _doeff_k)" in static, static


def _first_if(statements: list[ast.stmt]) -> ast.If | None:
    return next((s for s in statements if isinstance(s, ast.If)), None)


def _if_chain(clause: ast.If | None) -> list[ast.expr]:
    """The condition of this `if` and the chain of conditions of the `if`s continuing inside its `else`."""
    if clause is None:
        return []
    return [clause.test, *_if_chain(_first_if(clause.orelse))]


def _clause_tests(static: str) -> list[ast.expr]:
    """The list of clause-check expressions lined up by the clause-dispatch function of the type-checking expansion (first argument `_doeff_effect`). The clause checks are the `if` directly under the function
    and the chain of `if`s continuing inside its `else` (an `if` inside the writer's body is not counted)."""
    return [
        test
        for function in ast.walk(ast.parse(static))
        if isinstance(function, ast.FunctionDef)
        and function.args.args
        and function.args.args[0].arg == "_doeff_effect"
        for test in _if_chain(_first_if(function.body))
    ]


def _isinstance_subjects(test: ast.expr) -> list[str]:
    """The list of spellings of the value (first argument) narrowed by `isinstance` in the check expression."""
    return [
        ast.unparse(node.args[0])
        for node in ast.walk(test)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
    ]


# The clause check must not narrow the same value twice. Previously the check of a clause that writes `effect` was
# `isinstance((effect := _doeff_effect), T) and isinstance(_doeff_effect, T)`, and with pyright (1.1.414; 1.1.411 narrows only the
# left side), which also narrows the walrus right-hand side, the second became "always true" (reportUnnecessaryIsInstance) — one per clause that reads `effect`, and the writer could not fix it
# (25 in the 3 cli_host check files of agora-controllers). The walrus narrowing changes with the pyright version, so the check does not use walrus and
# narrows a separate name once each. This test reads the shape of the expansion without invoking pyright (no red appears with the pyright version in this repo's lock).
@pytest.mark.parametrize(
    ("label", "clauses"),
    [("field-named", 6), ("generic", 5), ("probe", 6), ("named", 3)],
)
def test_static_clause_test_narrows_each_name_once(label: str, clauses: int) -> None:
    from doeff_hy._static_view import static_view

    sources = {
        "field-named": FIELD_NAMED_EFFECTS
        + FIELD_NAMED_PROBE.replace("(import probe_effects [Kv Note Bump])", ""),
        "generic": _generic({}),
        "probe": EFFECTS + _render({}).replace("(import probe_effects [Tick Name Pair])", ""),
        "named": NAMED_EFFECTS
        + NAMED_PROBE.replace("(import probe_effects [Transfer Pass Put])", ""),
    }
    with static_view():
        static = _expand(sources[label])
    tests = _clause_tests(static)
    assert len(tests) == clauses, static
    for test in tests:
        shown = ast.unparse(test)
        assert not [node for node in ast.walk(test) if isinstance(node, ast.NamedExpr)], shown
        subjects = _isinstance_subjects(test)
        assert "_doeff_effect" in subjects, shown
        assert len(subjects) == len(set(subjects)), shown


@needs_pyright
def test_writers_effect_keeps_the_clause_effect_type(tmp_path: Path) -> None:
    # Types are not escaped: the writer's `effect` reads as the clause's effect type, and reading a missing field is red (not degraded to Any or object).
    from doeff_hy._static_check import main

    text = FIELD_NAMED_PROBE.replace("(resume (+ n effect.n))", "(resume (+ n effect.missing))")
    (tmp_path / "probe_effects.hy").write_text(FIELD_NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    errors = [d for d in found if d["severity"] == "error"]
    assert ("reportAttributeAccessIssue", _line_of(text, "effect.missing")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


# Clauses headed by a generic effect (Spawn[_T, _E], Race[_T], Wait[_T]). pyright makes the type arguments of the type that the clause's `isinstance`
# narrows an `object` effect to Unknown (`Race[Unknown]`, `Spawn[Unknown, Unknown]`), so previously field binding
# (`tasks = _doeff_effect.tasks`) and the effect argument of typed_resume / typed_transfer were partly red as Unknown, which the writer could not fix
# (2 check files and 9 reds in agora-controllers' agora_sim — field names are decided by the effect definition). The expansion rebinds the clause's effect with a constructor call and
# resolves the type arguments with Any (`Race[Any]`), so field types stay as the types in the effect definition.
GENERIC_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff_core_effects._scheduler [Spawn Race Wait Task])

(defn takes-int [#^ int n]
  n)

(defhandler reads-generic-fields [#^ (get Task int) made]
  {:tags {:context "probe" :role "protocol"}}
  (Spawn [program]
    {SPAWN_USE}
    (resume {SPAWN_ANSWER}))
  (Race [tasks]
    {RACE_USE}
    (<- first object (Wait (get tasks 0)))
    (resume first))
  (Wait [task]
    (resume (takes-int (len (repr task))))))

(defhandler race-answers-at-once
  (Race []
    (resume "")))

(defhandler spawn-is-only-counted [#^ (get list int) seen]
  (Spawn []
    (.append seen 1)
    (reperform effect)))
"""

GENERIC_BASE = {
    "SPAWN_USE": "(takes-int (len (repr program)))",
    "SPAWN_ANSWER": "made",
    "RACE_USE": "(takes-int (len tasks))",
}


def _generic(change: dict[str, str]) -> str:
    text = GENERIC_PROBE
    for key, value in (GENERIC_BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _errors_at(errors: list[dict[str, object]], line: int) -> list[dict[str, object]]:
    return [d for d in errors if d["line"] == line]


@needs_pyright
def test_generic_effect_clauses_have_no_unknown_type_error(tmp_path: Path) -> None:
    # 0 reds for each of: a clause binding fields (program, tasks, task), a clause with no fields that only resumes, and a clause that reads nothing.
    assert _check(tmp_path, _generic({})) == []


@needs_pyright
def test_generic_effect_field_keeps_the_type_of_its_definition(tmp_path: Path) -> None:
    # Fields do not escape to Any: Race's tasks has the defined type `tuple[Task[object] | Future[object], ...]` (since 25f1dcd02, which made Task and Future covariant,
    # Race's implementation __init__ accepts this type), and Spawn's program is `Program[Any, Any]`, so passing them to an int argument
    # is red (the container shape remains).
    race = _generic({"RACE_USE": "(takes-int tasks)"})
    race_errors = _errors_at(_check(tmp_path, race), _line_of(race, "(takes-int tasks)"))
    assert [d["rule"] for d in race_errors] == ["reportArgumentType"], race_errors
    assert "Task[object] | Future[object]" in str(race_errors[0]["message"]), race_errors

    spawn = _generic({"SPAWN_USE": "(takes-int program)"})
    spawn_errors = _errors_at(_check(tmp_path, spawn), _line_of(spawn, "(takes-int program)"))
    assert [d["rule"] for d in spawn_errors] == ["reportArgumentType"], spawn_errors
    assert "Program[Any, Any]" in str(spawn_errors[0]["message"]), spawn_errors


@needs_pyright
def test_wrong_answer_to_a_generic_effect_is_still_caught(tmp_path: Path) -> None:
    # Spawn is an effect answered with Task (`EffectBase[Task[_T]]`) — resuming from a clause with Task gives 0 reds (test_generic_effect_clauses_have_no_
    # unknown_type_error), and resuming with int is an argument type mismatch in typed_transfer (the trailing resume becomes transfer).
    text = _generic({"SPAWN_ANSWER": "1"})
    errors = _errors_at(_check(tmp_path, text), _line_of(text, "(resume 1)"))
    assert [d["rule"] for d in errors] == ["reportArgumentType"], errors


def test_static_expansion_rebinds_the_effect_only_where_the_clause_reads_it() -> None:
    """Only when a clause binds fields or reads the effect with typed_resume, rebind the narrowed `_doeff_effect` to a type whose type arguments are all Any.
    Clauses that read nothing and the runtime expansion are unchanged."""
    from doeff_hy._static_view import static_view

    runtime = _expand(_generic({}))
    with static_view():
        static = _expand(_generic({}))
    rebind = "_doeff_effect = _doeff_any_specialized({}, *_doeff_any_args, **_doeff_any_kwargs)"
    for effect in ("Spawn", "Race", "Wait"):
        assert rebind.format(effect) in static, static
    # A clause with no fields that only resumes (typed_resume reads the effect) is also rebound. `(Spawn [] ... (reperform effect))`, which reads nothing, is not rebound
    assert static.count("_doeff_any_specialized(") == 4, static
    assert "any_specialized" not in runtime, runtime
    # The rebinding comes before field binding (so that fields are read with the defined types)
    assert static.index(rebind.format("Race")) < static.index("tasks = _doeff_effect.tasks"), static
