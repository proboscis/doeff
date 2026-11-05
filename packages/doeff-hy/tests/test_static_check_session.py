"""Failure cases for the type-check expansion of defhandler session val / session var (the same kind as #2214 / #2279).

The runtime expansion binds a name to the answer of `(yield (Get key))` (the content of Some) or to the initial value. If the type-check expansion copied this as is,
the Get answer has no type, so the name and every expression using it became Unknown, and strict gave a dozen reds per handler that the author could not fix
(vg-w39 measured this in #2251 — the fix moving daily_verify's cache to session val was stopped by the type-check hook). In addition,
`doeff_hy._session` had no stub, so the import got a "no stub" red. If the expansion binds the name to the initial value and a stub is placed:

- Under strict, 0 reds for a handler with a session val (a typed dict) and a session var (a number rewritten by :=), and for a handler without :=.
- The name's type has not escaped: adding a number to a string session val is red as a type mix-up.
- The runtime expansion is as before (the Get / Put round trip — seen by tests/test_val_var_lazy.py).
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is missing")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Name [(get EffectBase str)]
  #^ str prefix)
"""

PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import probe_effects [Tick Name])

(defhandler remembering
  "Count Tick and remember the answer of Name."
  {:tags {:context "probe" :role "protocol"}}
  (session val answered ((get dict #(str str))))
  (session var count 0)
  (Tick [step]
    (:= count (+ count step))
    (resume count))
  (Name [prefix]
    (when (not-in prefix answered)
      (setv (get answered prefix) (+ prefix "!")))
    (resume (get answered prefix))))

(defhandler labelled
  "Answer Name with a fixed prefix (session val only — a handler without :=)."
  {:tags {:context "probe" :role "protocol"}}
  (session val head "x-")
  (Name [prefix]
    (resume {LABEL})))
"""

BASE = {"LABEL": "(+ head prefix)"}

# A module with only handlers without :=. The PROBE above sits next to remembering, which has :=, so remembering read the Put / Some imports the expansion puts in the module,
# hiding the "import is unused" red for the handler without := (agora-controllers' daily run
# 2026-10-11 16:00 — 7 files such as profile_openings.hy, 2 each for Put / Some).
LABELLED_ONLY = """\
(require doeff-hy.macros [defhandler])
(import probe_effects [Name])

(defhandler labelled
  "Answer Name with a fixed prefix (session val only — a handler without :=)."
  {:tags {:context "probe" :role "protocol"}}
  (session val head "x-")
  (Name [prefix]
    (resume (+ head prefix))))
"""

# A module with only handlers that have := (a watch that the Put / Some imports are not dropped too much — dropping them gives an undefined-name red).
COUNTING_ONLY = """\
(require doeff-hy.macros [defhandler])
(import probe_effects [Tick])

(defhandler counting
  "Count Tick (session var and := only)."
  {:tags {:context "probe" :role "protocol"}}
  (session var count 0)
  (Tick [step]
    (:= count (+ count step))
    (resume count)))
"""

# A module with only the old lazy-var and set!. set! expands to (<- (Put key (Some name))), so it uses Put / Some even without :=.
LEGACY_SET_ONLY = """\
(require doeff-hy.macros [defhandler <- set!])
(import probe_effects [Tick])

(defhandler counting
  "Count Tick (old lazy-var and set!)."
  {:tags {:context "probe" :role "protocol"}}
  (lazy-var count 0)
  (Tick [step]
    (set! count (+ count step))
    (resume count)))
"""


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
def test_strict_has_no_error_from_session_expansions(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
@pytest.mark.parametrize(
    "text",
    [LABELLED_ONLY, COUNTING_ONLY, LEGACY_SET_ONLY],
    ids=["no-assign", "assign", "legacy-set"],
)
def test_strict_has_no_error_for_one_handler_module(tmp_path: Path, text: str) -> None:
    # The expansion of a handler with neither := nor set! does not emit the Put / Some imports (emitting them gives 2 strict reportUnusedImport).
    # A handler with := / set! emits them as before (not emitting gives an undefined Put / Some red).
    assert _check(tmp_path, text) == []


@needs_pyright
def test_session_names_keep_their_types(tmp_path: Path) -> None:
    # head is a string session val — adding a number is red as a type mix-up (the name has not escaped to Unknown).
    text = _render({"LABEL": "(+ head 1)"})
    errors = _check(tmp_path, text)
    assert _line_of(text, "(resume (+ head 1))") in [d["line"] for d in errors], errors
