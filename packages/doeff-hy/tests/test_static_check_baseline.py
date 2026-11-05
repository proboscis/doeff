"""Failure cases for doeff-hy-check's comparison with the baseline, strict mode, and the expansion cache.

- strict: a defk with `:pre [(: args dict)]` is red as a dict without type arguments (reportMissingTypeArgument).
- Mix-up of `(| X None)`: reading an attribute of a value that may be None is red (reportOptionalMemberAccess).
- Baseline: exit 0 if there are only reds present in the baseline, exit 1 if even one red absent from the baseline appears. A mere line shift is not counted as a new red.
- Removing bookkeeping from the expansion: the Python shown to the type checker keeps no `setattr(..., '__doeff_...__', ...)` or `hy.macros.require`,
  and under strict no reds for doeff-hy's own imports (duplicates, missing stubs, private helper names) appear.
- Expansion cache: the second run pulls the saved expansion, and diagnostics are the same as the first. Changing the source gives a different entry (failure cases for pulling vs. re-expanding are
  test_static_cache.py).
"""

import contextlib
import io
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy._static_baseline import Baseline, Identity, read_baseline, split

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

MODULE = """\
(require doeff-hy.macros [defk <-])

(defk count-keys [args]
  {:pre [(: args dict)] :post [(: % int)]}
  (len args))

(defk maybe-name [key]
  {:pre [(: key str)] :post [(: % (| str None))]}
  (if key key None))

(defk shout [key]
  {:pre [(: key str)] :post [(: % str)]}
  (<- name (maybe-name key))
  (.upper name))
"""

NEW_MISTAKE = """
(defk total [key]
  {:pre [(: key str)] :post [(: % int)]}
  (count-keys key))
"""


@dataclass(frozen=True)
class Run:
    """The result of running doeff-hy-check once (exit code and JSON diagnostics)."""

    code: int
    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int]]:
        return [(str(d["rule"]), int(str(d["line"]))) for d in self.diagnostics if d["severity"] == "error"]


def _check(root: Path, *extra: str) -> Run:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--root", str(root), "--json", "--cache-dir", str(root / ".cache"), *extra, str(root / "probe.hy")])
    text = out.getvalue()
    return Run(code, tuple(json.loads(text)) if text.strip() else ())


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_strict_reports_a_bare_dict_contract(probe: Path) -> None:
    # Under the basic check a dict without type arguments is not red; under strict the defk line (line 3 -- the position of the argument annotation) is red.
    assert ("reportMissingTypeArgument", 3) not in _check(probe).errors()
    assert ("reportMissingTypeArgument", 3) in _check(probe, "--strict").errors()


@needs_pyright
def test_a_none_union_read_as_its_member_is_red(probe: Path) -> None:
    # Applying .upper to the value of a defk returning (| str None) (line 14) is red.
    assert ("reportOptionalMemberAccess", 14) in _check(probe).errors()


@needs_pyright
def test_only_reds_missing_from_the_baseline_fail(probe: Path) -> None:
    baseline = probe / "baseline.json"
    assert _check(probe, "--write-baseline", str(baseline)).code == 0
    assert read_baseline(baseline).counts, "existing reds are not recorded in the baseline"
    # Only reds present in the baseline = exit 0 (the reds are reported as known).
    known = _check(probe, "--baseline", str(baseline))
    assert known.code == 0
    assert all(d["known"] for d in known.diagnostics if d["severity"] == "error")
    # Even if adding lines on top shifts the lines of existing reds, they are not counted as new reds.
    (probe / "probe.hy").write_text(";; note\n;; note\n" + MODULE, encoding="utf-8")
    assert _check(probe, "--baseline", str(baseline)).code == 0
    # Adding one red absent from the baseline gives exit 1, and only the new red is not known.
    (probe / "probe.hy").write_text(MODULE + NEW_MISTAKE, encoding="utf-8")
    fresh = _check(probe, "--baseline", str(baseline))
    assert fresh.code == 1
    new = [d for d in fresh.diagnostics if d["severity"] == "error" and not d["known"]]
    assert {d["line"] for d in new} == {18}, new


@needs_pyright
def test_strict_shows_no_reds_from_the_doeff_hy_expansion(probe: Path) -> None:
    from doeff_hy._static_check import import_roots, project, pyright_settings

    projection = project(probe, import_roots(probe, pyright_settings(probe)), probe / "probe.hy")
    text = getattr(projection, "text")
    assert "__doeff_" not in text and "hy.macros.require" not in text, text
    rules = {d["rule"] for d in _check(probe, "--strict").diagnostics}
    assert not rules & {"reportDuplicateImport", "reportMissingTypeStubs", "reportPrivateUsage", "reportUnusedImport"}, rules


@needs_pyright
def test_the_expansion_cache_returns_the_same_diagnostics(probe: Path) -> None:
    first = _check(probe)
    entries = sorted((probe / ".cache").rglob("*.json"))
    assert entries, "the expansion was not saved"
    assert _check(probe).diagnostics == first.diagnostics
    assert sorted((probe / ".cache").rglob("*.json")) == entries  # the second run does not save again (same key)
    (probe / "probe.hy").write_text(MODULE + "\n;; changed\n", encoding="utf-8")
    _check(probe)
    assert len(list((probe / ".cache").rglob("*.json"))) == len(entries) + 1  # if the source changes, the key differs


@dataclass(frozen=True)
class Seen:
    """The minimal shape of a diagnostic passed to split."""

    path: str
    line: int
    rule: str
    message: str
    severity: str = "error"


def test_split_counts_the_same_red_by_multiplicity() -> None:
    # If the red with the same identifier is 1 in the baseline and 2 now, 1 is new (a form that comparing as sets would miss).
    base = Baseline(Counter({Identity("a.hy", "r", "m"): 1}))
    verdict = split(base, [Seen("a.hy", 3, "r", "m"), Seen("a.hy", 9, "r", "m")])
    assert [d.line for d in verdict.known] == [3] and [d.line for d in verdict.new] == [9]


def _baseline_of(tmp_path: Path, messages: list[str]) -> Baseline:
    path = tmp_path / "base.json"
    rows = [{"path": "a.hy", "rule": "r", "message": m, "line": i} for i, m in enumerate(messages, 1)]
    path.write_text(json.dumps({"version": 1, "errors": rows}), encoding="utf-8")
    return read_baseline(path)


def test_a_shifted_hy_generated_number_is_still_the_known_red(tmp_path: Path) -> None:
    # Even if adding one definition shifts the numbers in Hy's generated names 44 -> 45 and 28 -> 29, they remain the same red as in the baseline (#2287).
    # Baseline lines are folded when read, so there is no need to rebuild the baseline.
    base = _baseline_of(tmp_path, ["x is _hy_anon_44", "y of _hy_let_first_28", "z of _lazy_sent_cached_1"])
    verdict = split(base, [Seen("a.hy", 3, "r", "x is _hy_anon_45"), Seen("a.hy", 4, "r", "y of _hy_let_first_29"), Seen("a.hy", 5, "r", "z of _lazy_sent_cached_2")])
    assert len(verdict.known) == 3 and verdict.new == ()


def test_folding_does_not_hide_an_added_generated_red_or_other_numbers(tmp_path: Path) -> None:
    # Counts are still compared after folding, so a generated-name red that truly increased by one is a new red. Digits in type names are not folded.
    base = _baseline_of(tmp_path, ["x is _hy_anon_44", "type Int32 at 7"])
    verdict = split(base, [
        Seen("a.hy", 1, "r", "x is _hy_anon_9"),
        Seen("a.hy", 2, "r", "x is _hy_anon_10"),
        Seen("a.hy", 3, "r", "type Int64 at 7"),
    ])
    assert [d.line for d in verdict.known] == [1]
    assert [d.message for d in verdict.new] == ["x is _hy_anon_10", "type Int64 at 7"]


def test_split_follows_a_moved_file_one_to_one() -> None:
    # The baseline red disappears and a red with the same rule and message appears at one different path = a move. The second one is new.
    base = Baseline(Counter({Identity("old.hy", "r", "m"): 1}))
    verdict = split(base, [Seen("new.hy", 1, "r", "m"), Seen("other.hy", 1, "r", "m")])
    assert [d.path for d in verdict.known] == ["new.hy"] and [d.path for d in verdict.new] == ["other.hy"]


def test_a_baseline_of_another_version_is_unreadable(tmp_path: Path) -> None:
    from doeff_hy import BaselineUnreadable

    path = tmp_path / "b.json"
    path.write_text(json.dumps({"version": 9, "errors": []}), encoding="utf-8")
    with pytest.raises(BaselineUnreadable):
        read_baseline(path)
