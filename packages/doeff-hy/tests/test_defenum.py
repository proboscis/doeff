"""doeff-hy's `defenum` (doeff_hy/record.hy) — a macro that builds a closed set of values as a StrEnum.

What it checks:
- Shape of the expansion: `(defclass Name [StrEnum] (setv MEMBER "value" …))`.
- How values are decided: the name is lowercased and `_` / `-` are normalized to `-`. `(NAME "value")` is an explicit value.
- Forms refused at expansion time: no member, the shape of a name, colliding names, colliding values, the shape of a member.
- Conversion to JSON: the value string is written as is and can be read back.
- `match` exhaustiveness: it can branch at runtime, doeff-hy-check (pyright) catches a missing member, and an exhaustive form passes.

Design record = docs/design/defenum/design.md.
"""

import json
import shutil
import types
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is missing")

PRELUDE = "(require doeff-hy.record [defenum])\n(import enum [StrEnum])\n"


def _module(source: str) -> types.ModuleType:
    module = types.ModuleType("defenum_probe")
    hy.eval(hy.read_many(PRELUDE + source), module=module)
    return module


def _expand(form: str) -> str:
    module = _module("")
    expanded = hy.macroexpand(hy.read(form), module=module)
    return hy.repr(expanded)


def test_expansion_is_a_strenum_class_with_one_setv() -> None:
    assert _expand('(defenum A X (Y "yy"))') == '\'(defclass A [StrEnum] (setv X "x" Y "yy"))'


def test_values_come_from_the_member_names() -> None:
    module = _module(
        "(defenum PlacementMismatch KEY CONVERSATION KIND PHASE CANCELLED GUARANTEED)\n"
        "(defenum Progress IN-PROGRESS DONE_NOW Ready)\n"
    )
    mismatch = module.PlacementMismatch
    assert [(m.name, m.value) for m in mismatch] == [
        ("KEY", "key"),
        ("CONVERSATION", "conversation"),
        ("KIND", "kind"),
        ("PHASE", "phase"),
        ("CANCELLED", "cancelled"),
        ("GUARANTEED", "guaranteed"),
    ]
    progress = module.Progress
    # Hy's `IN-PROGRESS` becomes the attribute `IN_PROGRESS`, and in the value spelling both `_` and `-` are normalized to `-`.
    assert [(m.name, m.value) for m in progress] == [
        ("IN_PROGRESS", "in-progress"),
        ("DONE_NOW", "done-now"),
        ("Ready", "ready"),
    ]


def test_explicit_values_are_used_as_written() -> None:
    module = _module('(defenum Phase (PENDING "Pending") (RUNNING "Running") DONE)')
    assert [m.value for m in module.Phase] == ["Pending", "Running", "done"]
    assert module.Phase("Running") is module.Phase.RUNNING


def test_members_are_strings() -> None:
    module = _module("(defenum Kind ALPHA BETA-GAMMA)")
    kind = module.Kind
    assert kind.ALPHA == "alpha"
    assert str(kind.BETA_GAMMA) == "beta-gamma"
    assert f"{kind.BETA_GAMMA}" == "beta-gamma"
    assert isinstance(kind.ALPHA, str)


def test_json_round_trip() -> None:
    module = _module("(defenum PlacementMismatch KEY CONVERSATION IN-FLIGHT)")
    mismatch = module.PlacementMismatch
    text = json.dumps({"mismatch": mismatch.IN_FLIGHT, "all": list(mismatch)})
    assert text == '{"mismatch": "in-flight", "all": ["key", "conversation", "in-flight"]}'
    assert mismatch(json.loads(text)["mismatch"]) is mismatch.IN_FLIGHT
    with pytest.raises(ValueError, match="IN_FLIGHT"):
        mismatch("IN_FLIGHT")


def test_match_dispatches_on_members() -> None:
    module = _module(
        "(defenum Light RED GREEN)\n"
        "(defn describe [light]\n"
        "  (match light\n"
        '    Light.RED "stop"\n'
        '    Light.GREEN "go"))\n'
    )
    assert module.describe(module.Light.RED) == "stop"
    assert module.describe(module.Light("green")) == "go"


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ("(defenum Empty)", "has no members"),
        ('(defenum "Name" A)', "first argument must be an enum name"),
        ("(defenum Twice A B A)", "member names collide"),
        ("(defenum Mangled IN-PROGRESS IN_PROGRESS)", "member names collide"),
        ('(defenum SameValue A (B "a"))', "collides between"),
        ("(defenum Spelling A-B A_B)", "member names collide"),
        ("(defenum NotAMember A 1)", "must be exactly NAME or"),
        ('(defenum Triple (A "a" "b"))', "must be exactly NAME or"),
        ("(defenum Sunder _A)", "must start with a letter"),
    ],
)
def test_malformed_declarations_are_rejected_at_expansion(form: str, message: str) -> None:
    with pytest.raises(hy.errors.HyMacroExpansionError, match=message):
        _module(form)


def test_same_value_from_different_spellings_is_rejected() -> None:
    # `A-B` and `(C "a-b")` have the same value — StrEnum would silently make the later one an alias, so the expansion stops it.
    with pytest.raises(hy.errors.HyMacroExpansionError, match="collides between"):
        _module('(defenum Alias A-B (C "a-b"))')


EXHAUSTIVE = """\
(require doeff-hy.record [defenum])
(import enum [StrEnum])
(import typing [assert-never])

(defenum PlacementMismatch KEY CONVERSATION KIND PHASE CANCELLED GUARANTEED)

(defn #^ str describe [#^ PlacementMismatch mismatch]
  (match mismatch
    PlacementMismatch.KEY "key"
    PlacementMismatch.CONVERSATION "conversation"
    PlacementMismatch.KIND "kind"
    PlacementMismatch.PHASE "phase"
    PlacementMismatch.CANCELLED "cancelled"
{LAST}    _ (assert-never mismatch)))
"""


@pytest.fixture(scope="module")
def exhaustiveness(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[dict[str, object]]]:
    import contextlib
    import io

    from doeff_hy._static_check import main

    root: Path = tmp_path_factory.mktemp("defenum_check")
    files = {
        "exhaustive": root / "enum_exhaustive.hy",
        "missing": root / "enum_missing.hy",
    }
    files["exhaustive"].write_text(
        EXHAUSTIVE.replace("{LAST}", '    PlacementMismatch.GUARANTEED "guaranteed"\n'),
        encoding="utf-8",
    )
    files["missing"].write_text(EXHAUSTIVE.replace("{LAST}", ""), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", *map(str, files.values())])
    diagnostics: list[dict[str, object]] = json.loads(out.getvalue())
    return {
        name: [d for d in diagnostics if d["path"] == path.name and d["severity"] == "error"]
        for name, path in files.items()
    }


@needs_pyright
def test_exhaustive_match_passes_the_static_check(
    exhaustiveness: dict[str, list[dict[str, object]]],
) -> None:
    assert exhaustiveness["exhaustive"] == []


@needs_pyright
def test_missing_member_is_caught_by_the_static_check(
    exhaustiveness: dict[str, list[dict[str, object]]],
) -> None:
    found = exhaustiveness["missing"]
    assert [(d["rule"], d["line"]) for d in found] == [("reportArgumentType", 14)], found
    assert "GUARANTEED" in str(found[0]["message"])
