"""The type of the name bound by `<-` for an effect with a declaration, and the type of a relay `Perform` that does not open the declaration.

ADR-DOE-CORE-EFFECTS-003: at runtime `<-` turns an :absent answer into Absent and a :failure answer into Raise and emits them in the caller's scope,
so the bound name receives only success and :value answers. The type-checking expansion (`_doeff_perform`) returns the same type:

- `(<- x (| Hit Empty) (Read ...))` — if Read's :answer is `Hit | Empty | Missing | Broken` with :absent [Missing] and :failure [Broken],
  the type of x is `Hit | Empty` (when the annotation was the whole answer, Missing and Broken did not fit the annotation and were red).
- If the annotation is narrower than the bound name's type it stays red (the check is not loosened).
- A relay `(<- a (Perform ask))` does not open the declaration, so its answer is the whole declaration (a union of effects is also accepted).
- `_doeff_on_raise`, which the `on-raise` expansion imports, has fully known types (when the type argument of the DoExpr generic node was Unknown, the caller's strict reported
  a "partially unknown" red).
- The base answer of the effect stays the whole declaration — handler `typed_resume` answers all three with values.
"""

import json
import subprocess
import sys
from pathlib import Path

from doeff_hy._static_stub import stub_of

EFFECTS = """\
(require doeff-hy.macros [defeffect])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Hit [] (#^ int n))
(defclass [(dataclass :frozen True)] Empty [] (#^ str why))
(defclass [(dataclass :frozen True)] Missing [] (#^ str why))
(defclass [(dataclass :frozen True)] Broken [] (#^ str detail))

(defeffect Read
  "A read that declares failure and absence."
  {:fields [(: key str)]
   :answer (| Hit Empty Missing Broken)
   :absent [Missing]
   :failure [Broken]
   :value [Empty]
   :tags {:context "probe" :role "foundation"}})

(defeffect Plain
  "A read with no declaration."
  {:fields [(: key str)]
   :answer (| Hit Broken)
   :tags {:context "probe" :role "foundation"}})

(defeffect Only
  "A read that declares only failure (the success type is Hit)."
  {:fields [(: key str)]
   :answer (| Hit Broken)
   :failure [Broken]
   :tags {:context "probe" :role "foundation"}})
"""

USER = """\
(require doeff-hy.macros [defk defhandler on-raise <-])
(import doeff [Perform Program])
(import probe_pkg.probe_mod [Read Plain Only Hit Empty Missing Broken])

(defk bound-without-failure-and-absent [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty) (Read key))
  (if (isinstance got Hit) got.n 0))

(defk bound-of-a-failure-only-declaration [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got Hit (Only key))
  got.n)

(defk undeclared-keeps-everything [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Broken) (Plain key))
  (if (isinstance got Hit) got.n 0))

(defk relay-keeps-the-declared-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty Missing Broken) (Perform (Read key)))
  (if (isinstance got Hit) got.n 0))

(defk relay-takes-an-effect-union [ask]
  {:pre [(: ask (| Read Plain))] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty Missing Broken) (Perform ask))
  (if (isinstance got Hit) got.n 0))

(defhandler relay-through
  "A relay clause that re-emits without opening the declaration — the answer is the whole declaration, and it goes into typed_resume."
  {:tags {:context "probe" :role "foundation"}}
  (Read [key]
    (<- answer (Perform effect))
    (resume answer))
  (Plain [key]
    (<- answer (Perform effect))
    (resume answer)))

(defk closes-the-failure [body]
  {:pre [(: body Program)] :post [(: % "the body's answer")] :tags {:context "probe" :role "program"}}
  (<- got (on-raise body (Broken d) (len d)))
  got)

(defk bound-narrower-than-the-bound-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got Hit (Read key))
  got.n)

(defk relay-narrower-than-the-declared-answer [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  (<- got (| Hit Empty) (Perform (Read key)))
  (if (isinstance got Hit) got.n 0))
"""


def _write(tmp_path: Path) -> Path:
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(EFFECTS, encoding="utf-8")
    return source


def test_the_stub_of_a_declared_effect_carries_the_bound_answer(tmp_path: Path) -> None:
    lines = stub_of(tmp_path, [tmp_path], _write(tmp_path)).text.splitlines()
    # The base answer stays the whole declaration (handler typed_resume answers all three with values). The type of the bound name is held by the method.
    assert "class Read(_doeff_effect_base[Hit | Empty | Missing | Broken]):" in lines
    assert "    def __iter__(self) -> _doeff_BoundRun[Hit | Empty]:" in lines
    assert "    def __iter__(self) -> _doeff_BoundRun[Hit]:" in lines
    # An effect that does not declare how to split answers does not change the bound name's type (`__iter__` exists only on the two declaring effects).
    assert len([line for line in lines if "def __iter__" in line]) == 2


def test_a_bind_reads_the_bound_answer_and_a_relay_reads_the_declared_one(tmp_path: Path) -> None:
    source = _write(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("user_mod.hy")
    user.write_text(USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy._static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    errors = [(d["line"], d["rule"]) for d in found if d["severity"] == "error"]
    lines = USER.splitlines()
    narrower = [n for n, line in enumerate(lines, start=1) if "(<- got Hit (Read key))" in line or "(<- got (| Hit Empty) (Perform (Read key)))" in line]
    # The reds are only the 2 places where "the annotation is too narrow" — annotations that satisfy the bound name's type, effects without a declaration, and relays (including a union of effects) are not red.
    assert errors == [(n, "reportAssignmentType") for n in narrower], (errors, found)
