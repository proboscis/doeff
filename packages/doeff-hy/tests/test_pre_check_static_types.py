"""Failure cases showing that the :pre of defeffect and the :check of defwire / defrecord do not produce reds the author cannot fix in the type-check expansion.

- defeffect: `__post_init__` bound all fields as `<field> = self.<field>` for the :pre check, so fields :pre does not use became
  reportUnusedVariable reds. Bind only the fields :pre references (the same shape as defrecord's :check).
- defwire / defrecord :check: the check compares the verdict with `verdict is not True`, so an expression whose answer is `re.Match` became a
  reportUnnecessaryComparison red (Match | None and True do not overlap). In the type-check expansion the answer is widened to object.
- Runtime meaning is unchanged: even if :pre has fields it does not use, the checks on the fields it does use are refused at construction. A Match answer of :check is read as truthiness.
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is missing")

MODULE = """\
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defwire defrecord])
(import dataclasses [dataclass])
(import re)

(defeffect PutProfile
  "Sample effect (:pre uses only name of the two fields)."
  {:fields [(: name str) (: size int)]
   :pre [(: name str) (> (len name) 0)]
   :answer None
   :tags {:context "probe" :role "intent"}})

(defwire ParkRow
  "Sample wire (:check returns re.Match)."
  {:names :camel
   :check [(re.fullmatch "c-[0-9A-Z]+" conversation-id)]}
  (#^ str conversation-id))

(defrecord Span
  "Sample record (:check returns re.Match)."
  {:check [(re.match "[a-z]+" label)]}
  (#^ str label))
"""


@dataclass(frozen=True)
class Run:
    """Result of one run of doeff-hy-check (JSON diagnostics)."""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_pre_and_check_add_no_errors_the_author_cannot_fix(probe: Path) -> None:
    # Before the fix, reportUnusedVariable on the field size that :pre does not use, and two reportUnnecessaryComparison from :check, were red.
    assert _check(probe).errors() == []


def _namespace() -> dict[str, object]:
    namespace: dict[str, object] = {}
    hy.eval(hy.models.Expression([hy.models.Symbol("do"), *hy.read_many(MODULE)]), namespace)
    return namespace


def test_runtime_pre_still_refuses_the_used_field() -> None:
    put_profile = _namespace()["PutProfile"]
    assert callable(put_profile)
    assert put_profile(name="a", size=3).size == 3
    with pytest.raises(AssertionError, match="PutProfile"):
        put_profile(name="", size=3)


def test_runtime_check_reads_a_match_as_truth() -> None:
    namespace = _namespace()
    park_row = namespace["ParkRow"]
    span = namespace["Span"]
    assert callable(park_row)
    assert callable(span)
    assert park_row(conversation_id="c-9AB").conversation_id == "c-9AB"
    assert span(label="abc").label == "abc"
    with pytest.raises(ValueError, match="conversation-id"):
        park_row(conversation_id="x")
    with pytest.raises(ValueError, match="label"):
        span(label="9")
