"""doeff-hy-check (doeff_hy/_static_check.py) statically catches deliberate mistakes in Hy.

A measurement on 2026-09-23 (an experiment branch of agora-controllers) showed that the typed Python version of doeff
caught 10 kinds with pyright, while the Hy version caught none statically. Here the same kinds of
mistakes are written as small self-contained samples to pin down which kinds are caught and that there are no false positives.
The effect answer type and the handler answer type are caught only when the effect declares its answer type with `(get EffectBase T)`
(EffectBase[T] of doeff core). The kinds not caught (env sets, effect sets) are listed in the table of
packages/doeff-hy/docs/static-check.md.
"""

import json
import re
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

EFFECTS = """\
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] SleepSeconds [EffectBase]
  #^ float seconds)

(defclass [(dataclass :frozen True)] ReadShared [(get EffectBase dict)]
  #^ str prefix)

(defclass [(dataclass :frozen True)] WriteShared [(get EffectBase bool)]
  #^ str key)

(defk summarize [conv results]
  {:pre [(: conv str) (: results list)] :post [(: % dict)]}
  {"conversation" conv "turns" (len results)})
"""

JOB = """\
(require doeff-hy.macros [defk <- defhandler])
(import probe_effects [SleepSeconds ReadShared WriteShared summarize])

(defn untyped [x] (.frobnicate x))

(defhandler answer
  (WriteShared [key] (resume {ANSWER})))

(defk job [conv]
  {:pre [(: conv str)] :post [(: % {RET})]}
  {READ}
  {SLEEP}
  (untyped conv)
  (<- digest dict {TASK})
  (<- again {AGAIN} (summarize conv []))
  (<- got {GOT} (ReadShared "x"))
  (setv n (len rows))
  digest)
"""

BASE = {
    "RET": "dict",
    "READ": '(<- rows dict (ReadShared "turn/"))',
    "SLEEP": "(<- (SleepSeconds 1.0))",
    "TASK": "(summarize conv [])",
    "AGAIN": "dict",
    "GOT": "dict",
    "ANSWER": "True",
}

# name -> (replacement, expected rule, expected line). Lines are 1-based within JOB.
# The last 2 kinds are caught because the effect declares its answer type (`(get EffectBase dict)`).
CAUGHT = {
    "wrong task argument type": ({"TASK": "(summarize 1 [])"}, "reportArgumentType", 14),
    "missing task argument": ({"TASK": "(summarize conv)"}, "reportCallIssue", 14),
    "wrong return type": ({"RET": "int"}, "reportAssignmentType", 18),
    "forgotten <- (value is used)": (
        {"READ": '(setv rows (ReadShared "turn/"))'},
        "reportArgumentType",
        17,
    ),
    "forgotten <- (statement only)": ({"SLEEP": "(SleepSeconds 1.0)"}, "doeff-hy-unperformed", 12),
    "wrong effect argument type": ({"SLEEP": '(<- (SleepSeconds "1"))'}, "reportArgumentType", 12),
    "wrong return type of a bound defk": ({"AGAIN": "int"}, "reportAssignmentType", 15),
    "wrong effect answer type": ({"GOT": "int"}, "reportAssignmentType", 16),
    "wrong handler answer type": ({"ANSWER": '"yes"'}, "reportArgumentType", 7),
}


def _render(change: dict[str, str]) -> str:
    text = JOB
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[dict[str, object]]]:
    from doeff_hy._static_check import main

    root: Path = tmp_path_factory.mktemp("static_check")
    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    files = {"baseline": root / "job_base.hy"}
    files["baseline"].write_text(_render({}), encoding="utf-8")
    for index, (name, (change, _, _)) in enumerate(CAUGHT.items()):
        files[name] = root / f"job_{index}.hy"
        files[name].write_text(_render(change), encoding="utf-8")
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", *map(str, files.values())])
    diagnostics: list[dict[str, object]] = json.loads(out.getvalue())
    return {
        name: [d for d in diagnostics if d["path"] == path.name and d["severity"] == "error"]
        for name, path in files.items()
    }


@needs_pyright
def test_the_clean_module_has_no_error(results: dict[str, list[dict[str, object]]]) -> None:
    # `(untyped conv)` = place a value of unknown type in statement position. Do not presume it is an unrun Program.
    assert results["baseline"] == []


@needs_pyright
@pytest.mark.parametrize("name", list(CAUGHT))
def test_each_mistake_is_caught_on_its_line(
    results: dict[str, list[dict[str, object]]], name: str
) -> None:
    _, rule, line = CAUGHT[name]
    found = [(d["rule"], d["line"]) for d in results[name]]
    assert (rule, line) in found, found


def test_static_view_does_not_leak_into_the_runtime_expansion() -> None:
    import ast

    import doeff_hy  # noqa: F401
    from doeff_hy._static_view import static_view

    source = (
        "(require doeff-hy.macros [defk <-])\n"
        "(defk f [x] {:pre [(: x int)] :post [(: % int)]} (<- y int (g x)) y)"
    )

    def expand() -> str:
        return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))

    runtime = expand()
    with static_view():
        static = expand()
    assert "_doeff_perform" not in runtime
    assert "static_types" not in runtime
    # The bind yields through open-bind (ADR-DOE-CORE-EFFECTS-003 R6 - for an effect without a declaration, return the effect itself)
    # The lookup is an expression without statements (a form that compiles even inside ! in an assignment target - outcome_forms.open-form).
    # If the open-bind answer is Pure (the answer of calling an effect-free @do on the spot), use that value; otherwise yield (#844).
    # Do not look up the builtin type by name (it broke on a caller's parameter named type - cleanup after L562)
    # The bind in a defk body looks up the name _doeff_outcomes that defk places in the module's globals (no __import__
    # per bind - #844 option a).
    assert re.search(
        r"y = (?P<b>_hy_gensym_bound_\d+)\.value if \((?P=b) := "
        r"_doeff_outcomes\.open_bind\(g\(x\)\)\)\.__class__ is _doeff_outcomes\.Pure "
        r"else \(yield (?P=b)\)",
        runtime,
    ), runtime
    assert "__import__" not in runtime
    # A <- outside a defk (where the name cannot be assumed to be in globals) still looks it up with __import__ as before.
    outside = ast.unparse(
        hy.compiler.hy_compile(
            hy.read_many("(require doeff-hy.macros [<-])\n(defn h [x] (<- y (g x)) y)"), "__main__"
        )
    )
    assert re.search(
        r"\((?P<m>_hy_gensym_outcomes_\d+) := __import__\('doeff_core_effects\._outcomes', "
        r"fromlist=\('open_bind',\)\)\)\.open_bind\(g\(x\)\)\)\.__class__ is (?P=m)\.Pure",
        outside,
    ), outside
    # The type-checking expansion has the same `x = perform(e)` shape as Python's @effectful (emits no yield)
    assert "y: 'int' = _doeff_perform(g(x))" in static
    assert "yield" not in static
    # The static expansion emits no helper imports - doeff-hy-check places them once at the top of the module (#1686)
    assert "static_types" not in static
    # Type annotations are attached at runtime too (as strings = not evaluated at definition time). The result variable is declared without a value at the top of the function, and the exit assignment is a
    # one-element tuple assignment without annotation (early-return exits read the type from the same declaration - _exit-binding in macros.hy).
    assert "def f(x: 'int')" in runtime
    assert re.search(r"^\s*_contract_result: 'int'$", runtime, re.MULTILINE), runtime
    assert "_contract_result, = (y,)" in runtime


def _many_definitions(count: int, mistake: bool) -> str:
    """A sample with `count` defks and one defk with `count` `<-`. The last defk passes a defk call to Spawn."""
    lines = ["(require doeff-hy.macros [defk <-])", "(import doeff [Spawn Task])", ""]
    for index in range(count):
        lines += [
            f"(defk step-{index} [x]",
            "  {:pre [(: x int)] :post [(: % int)]}",
            '  "one step"',
            "  (+ x 1))",
            "",
        ]
    binds = [f"  (<- v{index} int (step-{index} {'v' + str(index - 1) if index else 'x'}))" for index in range(count)]
    lines += [
        "(defk chain [x]",
        "  {:pre [(: x int)] :post [(: % int)]}",
        '  "many binds"',
        *binds,
        f"  (<- last {'str' if mistake else 'int'} (step-0 v{count - 1}))",
        "  (<- task Task (Spawn (step-0 x)))",
        "  x)",
    ]
    return "\n".join(lines) + "\n"


@needs_pyright
@pytest.mark.parametrize("mistake", [False, True])
def test_defk_calls_stay_programs_past_sixty_four_definitions(tmp_path: Path, mistake: bool) -> None:
    """Counterexample: back when the static expansion emitted helper imports per defk / `<-`, once the declarations of one name (`_doeff_do`,
    `_doeff_perform`) exceeded 64 in a module / function, pyright stopped inferring types and made it Unknown, and a defk call looked like the :post type
    rather than a Program - `(Spawn (defk call))` was reportArgumentType, and a bind type mismatch passed silently. Helper imports are
    placed once at the top of the module (static_check.with_static_helpers)."""
    import contextlib
    import io

    from doeff_hy._static_check import main

    (tmp_path / "many.hy").write_text(_many_definitions(70, mistake), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(tmp_path), "--json", str(tmp_path / "many.hy")])
    errors = [(d["rule"], d["message"]) for d in json.loads(out.getvalue()) if d["severity"] == "error"]
    if mistake:
        assert [rule for rule, _ in errors] == ["reportAssignmentType"], errors
    else:
        assert errors == [], errors


def _many_mcp_tools(count: int, mistake: bool) -> str:
    """A sample with `count` defmcp-tools followed by 2 defks. The last defk passes a defk call to Spawn and binds it."""
    lines = [
        "(require doeff-hy.macros [defk defmcp-tool <-])",
        "(import doeff [Pure Spawn Task])",
        "",
    ]
    for index in range(count):
        lines += [
            f"(defmcp-tool tool-{index}",
            f'  "tool {index}"',
            '  [{:name "text" :type "string" :description "text"}]',
            "  (<- r (Pure text))",
            "  r)",
            "",
        ]
    lines += [
        "(defk step [x]",
        "  {:pre [(: x int)] :post [(: % int)]}",
        '  "one step"',
        "  (+ x 1))",
        "",
        "(defk chain [x]",
        "  {:pre [(: x int)] :post [(: % int)]}",
        '  "after the tools"',
        f"  (<- last {'str' if mistake else 'int'} (step x))",
        "  (<- task Task (Spawn (step x)))",
        "  x)",
        "",
        # type of the tool value: McpToolDef's name is str. mistake annotates it as int (a type mismatch)
        f"(defn #^ {'int' if mistake else 'str'} last-tool-name [] (. tool-{count - 1} name))",
    ]
    return "\n".join(lines) + "\n"


def test_defmcp_tool_imports_nothing_of_its_own_in_the_static_view(tmp_path: Path) -> None:
    """The type-checking expansion of defmcp-tool emits no imports at all - the handler decorator `_doeff_do` (`_helper-imports` in macros.hy -
    the same table as defk) and the tool value's McpToolDef / McpParamSchema (`_doeff_<name>`) are read from the helper imports that doeff-hy-check
    places once at the top of the module (static_view.STATIC_HELPER_IMPORTS). Wherever two are written directly in the module, in a class
    body, or in a function body, the expansion's import statements exist only directly in the module and each name is declared once (importing
    per tool would push declarations past 64 in a module or class with 65 tools, and pyright would make the name Unknown)."""
    import ast

    from doeff_hy._static_check import Projection, project

    tool = (
        "(defmcp-tool {name}\n"
        '  "tool"\n'
        '  [{{:name "text" :type "string" :description "text"}}]\n'
        "  (<- r (Pure text))\n"
        "  r)\n"
    )
    source = (
        "(require doeff-hy.macros [defmcp-tool <-])\n(import doeff [Pure])\n\n"
        + tool.format(name="at-module")
        + tool.format(name="also-at-module")
        + "\n(defclass Box []\n"
        + tool.format(name="in-class")
        + ")\n\n(defn outer []\n"
        + tool.format(name="in-function")
        + "  in-function)\n"
    )
    (tmp_path / "tools.hy").write_text(source, encoding="utf-8")
    projection = project(tmp_path, [tmp_path], tmp_path / "tools.hy")
    assert isinstance(projection, Projection), projection
    tree = ast.parse(projection.text)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [ast.unparse(node) for node in imports if node not in tree.body] == [], projection.text
    bound = [alias.asname or alias.name for node in imports for alias in node.names]
    assert sorted(bound) == sorted(set(bound)), projection.text
    assert {"_doeff_do", "_doeff_McpToolDef", "_doeff_McpParamSchema"} <= set(bound), (
        projection.text
    )


@needs_pyright
@pytest.mark.parametrize("mistake", [False, True])
def test_defk_calls_stay_programs_past_sixty_four_mcp_tools(tmp_path: Path, mistake: bool) -> None:
    """Counterexample: back when the type-checking expansion of defmcp-tool imported `do` from `doeff.do` into `_doeff_do` per tool, in a module with
    65 tools the declarations of `_doeff_do` exceeded 64, pyright made it Unknown, and a later defk call looked like the :post
    type rather than a Program - `(Spawn (defk call))` was reportArgumentType (same reason as the defk counterexample of #1686). Back when McpToolDef of doeff.mcp was imported
    per tool, the tool value became Unknown for the same reason, and a mismatch of the tool's name type (reportReturnType) passed
    silently."""
    import contextlib
    import io

    from doeff_hy._static_check import main

    (tmp_path / "tools.hy").write_text(_many_mcp_tools(70, mistake), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(tmp_path), "--json", str(tmp_path / "tools.hy")])
    errors = [
        (d["rule"], d["message"]) for d in json.loads(out.getvalue()) if d["severity"] == "error"
    ]
    if mistake:
        assert sorted(rule for rule, _ in errors) == ["reportAssignmentType", "reportReturnType"], (
            errors
        )
    else:
        assert errors == [], errors


def _typed_mcp_tool(mistake: bool) -> str:
    """A sample with one tool having 6 schema types and a non-required argument. The body passes the arguments as they are to a defk that accepts the same types.
    mistake passes the non-required argument (`str | None`) to a `str` parameter (a type mismatch)."""
    first = "maybe" if mistake else "s"
    return (
        "(require doeff-hy.macros [defk defmcp-tool <-])\n\n"
        "(defk take [s i n b xs o maybe]\n"
        "  {:pre [(: s str) (: i int) (: n float) (: b bool) (: xs (get list object))\n"
        "         (: o (get dict #(str object))) (: maybe (| str None))]\n"
        "   :post [(: % str)]}\n"
        "  s)\n\n"
        "(defmcp-tool typed-tool\n"
        '  "typed"\n'
        '  [{:name "s" :type "string" :description "s"}\n'
        '   {:name "i" :type "integer" :description "i"}\n'
        '   {:name "n" :type "number" :description "n"}\n'
        '   {:name "b" :type "boolean" :description "b"}\n'
        '   {:name "xs" :type "array" :description "xs"}\n'
        '   {:name "o" :type "object" :description "o"}\n'
        '   {:name "maybe" :type "string" :description "maybe" :required False}]\n'
        f"  (<- r (take {first} i n b xs o maybe))\n"
        "  r)\n"
    )


@needs_pyright
@pytest.mark.parametrize("mistake", [False, True])
def test_defmcp_tool_handler_params_carry_the_schema_types(tmp_path: Path, mistake: bool) -> None:
    """The type-checking expansion of defmcp-tool annotates the handler parameters with types mirroring the schema :type (non-required parameters are
    `| None` - the server passes None for arguments not sent). Without the annotations, in strict mode each parameter produced 2 errors the writer could not fix
    (reportMissingParameterType, reportUnknownParameterType), plus reportUnknownArgumentType at every place the body passes a parameter,
    and parameter type mismatches passed silently."""
    import contextlib
    import io

    from doeff_hy._static_check import main

    (tmp_path / "tool.hy").write_text(_typed_mcp_tool(mistake), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(tmp_path), "--strict", "--json", str(tmp_path / "tool.hy")])
    errors = [
        (d["rule"], d["message"]) for d in json.loads(out.getvalue()) if d["severity"] == "error"
    ]
    if mistake:
        assert [rule for rule, _ in errors] == ["reportArgumentType"], errors
    else:
        assert errors == [], errors


def _union_bind_first(mistake: bool) -> str:
    """A sample that first has a union-typed bind (`(<- m (| int None) …)`) in a module, followed by a defk bind that answers int.
    mistake writes the type of the later bind as str (a type mismatch)."""
    later = "str" if mistake else "int"
    return (
        "(require doeff-hy.macros [defk <-])\n\n"
        "(defk maybe-step [x]\n  {:pre [(: x int)] :post [(: % (| int None))]}\n  \"maybe\"\n  None)\n\n"
        "(defk union-first [x]\n  {:pre [(: x int)] :post [(: % (| int None))]}\n  \"union first\"\n"
        "  (<- m (| int None) (maybe-step x))\n  m)\n\n"
        "(defk one-step [x]\n  {:pre [(: x int)] :post [(: % int)]}\n  \"one step\"\n  (+ x 1))\n\n"
        f"(defk later [x]\n  {{:pre [(: x int)] :post [(: % int)]}}\n  \"later bind\"\n  (<- v {later} (one-step x))\n  x)\n"
    )


@needs_pyright
@pytest.mark.parametrize("mistake", [False, True])
def test_a_bind_after_a_union_bind_keeps_its_answer_type(tmp_path: Path, mistake: bool) -> None:
    """Counterexample: back when `_doeff_perform` had a catch-all `object -> Any` overload, if the first
    evaluation in a module was under a union expected type (`(<- m (| int None) …)`), pyright dropped every later `_doeff_perform` to the catch-all, and the answer became
    Any - a later bind type mismatch (writing int as str) passed silently, and the value of an event-loop clause became Unknown."""
    import contextlib
    import io

    from doeff_hy._static_check import main

    (tmp_path / "union_first.hy").write_text(_union_bind_first(mistake), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(tmp_path), "--json", str(tmp_path / "union_first.hy")])
    errors = [(d["rule"], d["message"]) for d in json.loads(out.getvalue()) if d["severity"] == "error"]
    if mistake:
        assert [rule for rule, _ in errors] == ["reportAssignmentType"], errors
    else:
        assert errors == [], errors


BINDINGS = """(require doeff-hy.macros [defk <-])

(defk rebinds [x]
  {:pre [(: x int)] :post [(: % int)]}
  (setv y x)
  (setv y (+ y 1))
  (val z (* y 2))
  z)
"""


@needs_pyright
def test_binding_findings_are_reported_on_their_lines(tmp_path: Path) -> None:
    """Use of setv is a warning and rebinding is an error, reported at the line written (ADR-DOE-HY-006)."""
    import contextlib
    import io

    from doeff_hy._static_check import main

    (tmp_path / "bindings.hy").write_text(BINDINGS, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--root", str(tmp_path), "--json", str(tmp_path / "bindings.hy")])
    found = {(d["rule"], d["severity"], d["line"]) for d in json.loads(out.getvalue())}
    assert ("doeff-hy-setv", "warning", 5) in found
    assert ("doeff-hy-setv", "warning", 6) in found
    assert ("doeff-hy-rebind", "error", 6) in found
    assert not any(rule == "doeff-hy-rebind" and line != 6 for rule, _, line in found)
    assert code == 1


#: A source with a finding for the outermost setv (a warning recommending val), and a reader macro defined in the file that a later form uses.
READ_ONCE_MODULE = """\
(require doeff-hy.macros [defk])
(defreader twice (setv form (.parse-one-form &reader)) `(* 2 ~form))
(setv counter 1)
(defk doubled [x]
  {:pre [(: x int)] :post [(: % int)]}
  #twice x)
"""


def test_one_source_is_read_by_the_hy_reader_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case: project read the source twice with Hy's reader (compile, and the finding for the outermost
    # setv). The second read was about 20% of expansion (2.6 s over 76 dependency files). Read once and reuse the forms compile read for the findings too.
    # A reader macro defined in the file (#twice) works because compile processes earlier forms before reading the next, so do not break that either.
    from doeff_hy._static_check import Projection, project

    source = tmp_path / "probe.hy"
    source.write_text(READ_ONCE_MODULE, encoding="utf-8")
    reads: list[str] = []
    real = hy.read_many

    def counting(text: str, *args: object, **kwargs: object) -> object:
        reads.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(hy, "read_many", counting)
    result = project(tmp_path, [tmp_path], source)
    assert isinstance(result, Projection), result
    assert len(reads) == 1, f"read {len(reads)} times with the Hy reader"
    assert "2 * x" in result.text, result.text  # the reader macro took effect on the later form
    assert [f.rule for f in result.findings].count("doeff-hy-setv") == 1, result.findings  # the finding for the outermost setv remains


#: A source where Hy gensym names appear in the expansion text (a macro in the file uses gensym).
GENSYM_MODULE = """\
(defmacro kept-twice [x]
  (setv g (hy.gensym "kept"))
  `(do (setv ~g ~x) (+ ~g ~g)))
(setv answer (kept-twice 21))
"""


def test_the_expansion_text_does_not_depend_on_what_was_expanded_before(tmp_path: Path) -> None:
    # Premise of the failure case: Hy's gensym numbers are a single per-process counter, so even for the same source, the expansion text changed depending on
    # whether another file was expanded first in the same process. So that the stored content does not change with execution order (or the process assignment when run in parallel),
    # gensym names in the expansion text are renumbered in order of appearance within the file.
    from doeff_hy._static_check import Projection, project

    first = tmp_path / "first.hy"
    other = tmp_path / "other.hy"
    first.write_text(GENSYM_MODULE, encoding="utf-8")
    other.write_text(GENSYM_MODULE.replace("answer", "other_answer"), encoding="utf-8")
    alone = project(tmp_path, [tmp_path], first)
    assert isinstance(alone, Projection), alone
    assert "_hy_gensym_kept_" in alone.text, alone.text
    assert isinstance(project(tmp_path, [tmp_path], other), Projection)
    again = project(tmp_path, [tmp_path], first)
    assert isinstance(again, Projection), again
    assert again.text == alone.text
