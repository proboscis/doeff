"""Failure cases for the tool (doeff_hy._static_stub) that builds a Hy module's type declarations (.pyi) from the type-checking expansion.

Hand-written .pyi files drift from the implementation, and every time a doeff definition was changed from defn to defk, the consumer's (agora) type gate stopped on Unknown errors
(4 times on 2026-10-02). The tool builds declarations from the expansion tree, and the consistency check is "regenerated output == committed output".

- For each definition form (defk, deff, defhandler, defrecord, defenum, constants, type aliases, imports), a declaration the consumer can read is emitted.
- Where a type cannot be produced it becomes Incomplete and the name is returned (it is not silently left Unknown).
- When the implementation changes, a stale committed .pyi is named (the failure case of the consistency check). A hand-written .pyi is not compared.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy._static_stub import MARK, UsedModule, generated, stale_in, stub_of, users_probe

MODULE = """\
(require doeff-hy.macros [defk deff defhandler val <-])
(require doeff-hy.record [defrecord defenum])
(import doeff [EffectBase Program])
(import dataclasses [dataclass])
(import datetime [datetime])
(import os)

(val GREETING "hi")
(val HERE (os.path.dirname (os.path.abspath __file__)))
(val MAX-BYTES (* 32 1024 1024))
(val STOPPED-CODE -15)
(val Stamp (| datetime None))
(val LIMITS #(1 2 3))
(val NAMES (+ #("a") #("b")))
(val SQL (+ "SELECT 1" " FROM t"))
(val Answer (| int str))
(val LIMIT-OF {"a" 1 "b" 2})
(val UNKNOWABLE (sorted LIMITS))

(defenum Color RED BLUE)
(val PRIMARY Color.RED)

(defrecord Point
  "A point in the plane."
  (#^ int x)
  (#^ int y))

(defclass [(dataclass :frozen True)] Ping [(get EffectBase int)]
  "The answer is int."
  (#^ str target)
  (setv #^ int tries 3))

(val EFFECT-TYPES #(Ping Point))

(deff double [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "To double it."
  (* n 2))

(defk ping-twice [target]
  {:pre [(: target str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "To ping twice and add the answers."
  (<- first int (Ping target))
  (<- second int (Ping target))
  (+ first second))

(defk passed-through [body]
  {:pre [(: body Program)] :post [(: % "body answer")] :tags {:context "probe" :role "program"}}
  "To return the body's answer as is (the answer type depends on the body)."
  (<- answer body)
  answer)

(defhandler counting [#^ (get list int) seen]
  (Ping [target tries]
    (resume (+ tries (len seen)))))

(defhandler answering-one
  (Ping [target tries]
    (resume 1)))

(defclass _Sentinel [])
(val SENTINEL (_Sentinel))
"""


def _module(tmp_path: Path, text: str = MODULE) -> Path:
    """Place the test module in a package under the tmp root (root = tmp_path)."""
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(text, encoding="utf-8")
    return source


def _lines(tmp_path: Path) -> list[str]:
    source = _module(tmp_path)
    return stub_of(tmp_path, [tmp_path], source).text.splitlines()


@pytest.mark.parametrize(
    "line",
    [
        "GREETING: str",
        "LIMITS: tuple[int, ...]",
        "NAMES: tuple[str, ...]",
        "SQL: str",
        "Answer: TypeAlias = int | str",
        "LIMIT_OF: dict[str, int]",
        "PRIMARY: Color",
        # A sequence of class names (the effect type table the sim fence lets through -- the shape of SIM-PASSABLE in doeff-cluster). If it cannot be read it becomes Incomplete,
        # and the tuple[type, ...] type was lost when a hand-written .pyi was replaced by the tool's output.
        "EFFECT_TYPES: tuple[type, ...]",
        "class Point:",
        "class Ping(EffectBase[int]):",
        "    tries: int = 3",
        "def double(n: int) -> int:",
        "def ping_twice(target: str) -> _Program[int, object]:",
        "def counting(seen: list[int]) -> _Handler:",
        "answering_one: _Handler",
        "HERE: str",
        "MAX_BYTES: int",
        "STOPPED_CODE: int",
        "Stamp: TypeAlias = datetime | None",
        "SENTINEL: _Sentinel",
        "def passed_through(body: Program) -> _Program[Incomplete, object]:",
        "from doeff import EffectBase as EffectBase",
    ],
)
def test_each_kind_of_definition_gets_a_declaration(tmp_path: Path, line: str) -> None:
    assert line in _lines(tmp_path)


def test_the_stub_starts_with_the_mark_and_has_no_bodies(tmp_path: Path) -> None:
    lines = _lines(tmp_path)
    assert lines[0].startswith(MARK)
    assert not [line for line in lines if "_doeff_perform" in line or "yield" in line]


def test_the_macro_helpers_the_expansion_imports_are_not_reexported(tmp_path: Path) -> None:
    # The defk expansion imports helpers of doeff_hy.macros (_install_guard_globals, _guard_performed, etc.). They are not part of the type surface, so
    # they are not re-exported -- re-exporting would make the .pyi depend on the macro module, which also hit the quality check's dependency contract (#2842).
    assert not [line for line in _lines(tmp_path) if line.startswith("from doeff_hy.macros import _")]


def test_what_cannot_be_typed_is_named_not_left_unknown(tmp_path: Path) -> None:
    # The :post of a description string and the answer of sorted cannot be typed -- they become Incomplete and the name is returned.
    source = _module(tmp_path)
    made = stub_of(tmp_path, [tmp_path], source)
    assert made.incomplete == ("UNKNOWABLE", "passed_through answer")
    assert "UNKNOWABLE: Incomplete" in made.text.splitlines()
    assert "from _typeshed import Incomplete" in made.text.splitlines()


def test_a_current_stub_is_not_stale(tmp_path: Path) -> None:
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    assert stale_in(tmp_path) == ()


def test_a_changed_field_makes_the_committed_stub_stale(tmp_path: Path) -> None:
    # Failure case: adding an implementation field names the committed .pyi as stale.
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    source.write_text(MODULE.replace("  (#^ int y))", "  (#^ int y)\n  (#^ int z))"), encoding="utf-8")
    assert [(s.source.name, s.reason) for s in stale_in(tmp_path)] == [("probe_mod.hy", "differs from the regenerated output")]


def test_a_hand_written_stub_is_not_checked(tmp_path: Path) -> None:
    # A hand-written .pyi (no mark) is not treated as the tool's -- it is in neither the consistency check nor --write.
    source = _module(tmp_path)
    source.with_suffix(".pyi").write_text("GREETING: str\n", encoding="utf-8")
    assert not generated(source.with_suffix(".pyi"))
    assert stale_in(tmp_path) == ()


def test_a_stub_with_an_older_mark_is_still_the_tools_and_stale(tmp_path: Path) -> None:
    # Failure case: a .pyi generated before the mark changed (same head, other words after it) read as hand-written,
    # so the check skipped it and --write refused to regenerate it. It stays the tool's and is named stale.
    source = _module(tmp_path)
    made = stub_of(tmp_path, [tmp_path], source).text
    older = "# doeff_hy._static_stub (an older mark)" + made[made.index("\n") :]
    source.with_suffix(".pyi").write_text(older, encoding="utf-8")
    assert generated(source.with_suffix(".pyi"))
    assert [(s.source.name, s.reason) for s in stale_in(tmp_path)] == [("probe_mod.hy", "differs from the regenerated output")]


EFFECTS = """\
(require doeff-hy.macros [defeffect val])

(defeffect Pong
  "The answer is int."
  {:fields [(: target str)]
   :answer int
   :tags {:context "probe" :role "foundation"}})

(defeffect MaybePong
  "The answer is int or None."
  {:fields [(: target str)]
   :answer (| int None)
   :tags {:context "probe" :role "foundation"}})

(defeffect NoneTypePong
  "The answer expression is not in type form (a runtime value) -- the base stays in the bare form that carries no answer."
  {:fields [(: target str)]
   :answer (type None)
   :tags {:context "probe" :role "foundation"}})

(import typing [TypeVar])
(import doeff [Program])
(val T (TypeVar "T"))

(defeffect Wrapped
  "The answer is the wrapped program's answer (type argument T) or None."
  {:fields [(: program (get Program #(T object)))]
   :answer (| T None)
   :tags {:context "probe" :role "foundation"}})
"""

USER = """\
(require doeff-hy.macros [defk <-])
(import probe_pkg.probe_mod [Pong])

(defk use-pong [target]
  {:pre [(: target str)] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "To use the answer of Pong."
  (<- n (Pong target))
  n)
"""


@pytest.mark.parametrize(
    "line",
    [
        "from doeff import EffectBase as _doeff_effect_base",
        "from dataclasses import dataclass as _doeff_dataclass",
        "@_doeff_dataclass(frozen=True)",
        "class Pong(_doeff_effect_base[int]):",
        "class MaybePong(_doeff_effect_base[int | None]):",
        "class NoneTypePong(_doeff_effect_base):",
        # An effect whose answer contains the module's TypeVar (the shape of SqlTransaction in sql_effects -- #2925).
        "class Wrapped(_doeff_effect_base[T | None]):",
        "    program: Program[T, object]",
    ],
)
def test_a_defeffect_keeps_its_base_with_the_answer_and_its_dataclass(tmp_path: Path, line: str) -> None:
    # Failure case (#2886): the tool removes the macro helper imports (names starting with _doeff_), but the base and decoration of defeffect read those names,
    # so a base with no definition in the .pyi (Unknown to consumers) remained and the dataclass decoration was lost. The base also did not carry the answer type.
    source = _module(tmp_path, EFFECTS)
    assert line in stub_of(tmp_path, [tmp_path], source).text.splitlines()


def test_a_defeffect_stub_carries_no_macro_bookkeeping_field(tmp_path: Path) -> None:
    # Failure case (#2886): copying `__doeff_answer__: _doeff_ClassVar[object]` made the field comparison of the hand-written .pyi
    # (test_hy_module_stubs in doeff-core-effects -- the order of dataclass fields) fail to see through the aliased ClassVar and count it as a field,
    # turning 4 replaced effect modules red. The base carries the answer type, so the bookkeeping name is not needed in the .pyi.
    source = _module(tmp_path, EFFECTS)
    text = stub_of(tmp_path, [tmp_path], source).text
    assert "__doeff_answer__" not in text
    assert "_doeff_ClassVar" not in text


def test_a_dotted_module_import_no_declaration_reads_is_not_copied(tmp_path: Path) -> None:
    # Failure case (#2886): copying the `import doeff_hy.record` that the defrecord expansion pulls into the .pyi made the .pyi depend on
    # doeff_hy.record even though no declaration reads it, and it hit the quality check's module dependency contract (dependency-not-allowed)
    # (when meter_effects' hand-written .pyi was replaced by the tool's output). A dotted import does not expose a name to consumers, so it is not copied.
    # The expansion of a defrecord with a header dict emits `(import doeff_hy._declarations doeff_hy.record)`.
    checked = """\
(require doeff-hy.record [defrecord])

(defrecord Amount
  "A non-negative quantity."
  {:tags {:context "probe" :role "type"}
   :check [(>= value 0)]}
  (#^ int value))
"""
    lines = stub_of(tmp_path, [tmp_path], _module(tmp_path, checked)).text.splitlines()
    assert "class Amount:" in lines
    assert [line for line in lines if line.startswith("import doeff_hy.")] == []


def test_a_module_import_no_declaration_reads_is_not_copied(tmp_path: Path) -> None:
    # Failure case (#2925): copying `(import os)` as `import os as os` made the .pyi depend on os even though no declaration reads os, and
    # it hit the quality check's "pure layer depends on IO API" (pure-io-import) (when process_effects' hand-written .pyi was replaced).
    # A module import has no meaning of exposing a name to consumers (consumers do not write `from m import os`), so it is not copied unless a declaration reads it.
    assert [line for line in _lines(tmp_path) if line.startswith("import os")] == []


def test_a_field_annotated_then_given_its_default_keeps_the_default(tmp_path: Path) -> None:
    # Failure case (#2974): when a field is written as two statements, "#^ float timeout" and "(setv timeout 30.0)", the tool copied them to the .pyi as two statements too
    # (`timeout: float` and `timeout = 30.0`), and pyright builds the dataclass constructor from the value of the annotation statement, so it read a field with no default --
    # calls that omit the field got a "timeout is missing" error (agora's 17th, ClickHouseDatabase). At runtime it is a field with default 30.0.
    # When the hand-written process_effects.pyi (`timeout: float | None = None`) was replaced at L1377, 23 fields such as RunProcess regressed to the same shape.
    split = """\
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True :kw-only True)] Database []
  "The destination of the store."
  #^ str host
  #^ float timeout
  (setv timeout 30.0)
  #^ (| str None) user
  (setv user None))
"""
    lines = stub_of(tmp_path, [tmp_path], _module(tmp_path, split)).text.splitlines()
    assert "    host: str" in lines
    assert "    timeout: float = 30.0" in lines
    assert "    user: str | None = None" in lines
    assert [line for line in lines if line.strip().startswith(("timeout =", "user ="))] == []


def test_a_user_reads_the_answer_of_a_generated_effect(tmp_path: Path) -> None:
    # Failure case (#2886): `n` in the consumer's `(<- n (Pong target))` was read as Unknown because the generated .pyi base was Unknown, and
    # strict type checking produced 7 "type is unknown" errors. Once the answer type int is carried on the base, n can be read as int.
    source = _module(tmp_path, EFFECTS)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("user_mod.hy")
    user.write_text(USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy._static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    assert [f"{d['line']}: {d['rule']}: {d['message']}" for d in found if d["severity"] == "error"] == []


def test_the_users_probe_binds_each_name_on_its_own_line() -> None:
    # Bundling names into a single tuple would bury the fully unknown names in "part of the type is unknown" -- bundle each name into a separate name.
    used = (UsedModule("a.b", ("Thing", "make-thing")), UsedModule("c", ("LIMIT",)))
    assert users_probe("pkg", used).splitlines() == [
        "(import pkg.a.b [Thing make-thing])",
        "(import pkg.c [LIMIT])",
        "",
        "(setv used-0 Thing)",
        "(setv used-1 make-thing)",
        "(setv used-2 LIMIT)",
    ]


STORE = """\
(import pathlib [Path])

(defclass Store []
  "A store (a class that places fields in __init__ -- the shape of WalStore in #2972)."
  (defn #^ None __init__ [self #^ int start #^ str label]
    (setv #^ Path self.where (Path label) self.start start #^ int self.seq start
          self.loose (dict) self._hidden 0))

  (defn #^ int next-seq [self]
    "The next number (a method that reads a field)."
    (+ self.seq 1)))
"""

LOG_USER = """\
(require doeff-hy.macros [defk <-])
(import pathlib [Path])
(import typing [Protocol])
(import probe_pkg.probe_mod [Store])

(defclass Log [Protocol]
  "The shape of a store (fields only -- the shape of ByteLog in #2972)."
  (#^ int seq)
  (#^ Path where))

(defk seq-of [log]
  {:pre [(: log Log)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "To read the fields of the store shape."
  log.seq)

(defk seq-of-store [store]
  {:pre [(: store Store)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "To pass Store as the store shape (without the fields in the declaration it does not satisfy the shape)."
  (<- n (seq-of store))
  n)
"""


@pytest.mark.parametrize(
    "line",
    [
        # A placement with an annotation copies the annotation.
        "    where: Path",
        "    seq: int",
        # A placement that takes an argument as is copies that argument's annotation.
        "    start: int",
        # A placement whose type cannot be read is Incomplete (the name is returned).
        "    loose: Incomplete",
    ],
)
def test_the_fields_an_init_places_get_declarations(tmp_path: Path, line: str) -> None:
    # Failure case (#2972): the tool did not read `(setv self.x …)` in __init__ and declared only methods. The WalStore .pyi lacked the fields seq,
    # snapshot, log, max_log_bytes and so did not satisfy ByteLog that requires them, so the consumer's correct way of passing it got a type error.
    assert line in stub_of(tmp_path, [tmp_path], _module(tmp_path, STORE)).text.splitlines()


def test_an_untyped_init_field_is_named_and_a_private_one_is_not_declared(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, STORE))
    assert "Store.loose" in made.incomplete
    assert [line for line in made.text.splitlines() if "_hidden" in line] == []


def test_a_class_whose_init_places_the_fields_satisfies_a_protocol_of_fields(tmp_path: Path) -> None:
    # Failure case (#2972): without the field declarations, `(seq-of store)` becomes the error "Store has no fields seq, where of Log".
    source = _module(tmp_path, STORE)
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    user = source.with_name("log_user.hy")
    user.write_text(LOG_USER, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy._static_check", "--root", str(tmp_path), "--json", "--strict", "--no-cache", str(user)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    found = json.loads(done.stdout) if done.stdout.strip() else []
    assert [f"{d['line']}: {d['rule']}: {d['message']}" for d in found if d["severity"] == "error"] == []


EFFECT_FREE = """\
(require doeff-hy.macros [defk])

(defk copied [x]
  {:pre [(: x int)] :post [(: % int)] :effects [] :tags {:context "probe" :role "judgment"}}
  "For a copy that emits nothing."
  (+ x 1))

(defk asking [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "For a definition with no effect declaration."
  (+ x 1))
"""


def test_a_defk_declared_to_perform_no_effect_has_a_never_effect_position(tmp_path: Path) -> None:
    # Failure case (#2939): the hand-written .pyi wrote normalized_value as `Program[SqlValue, Never]` (the copy emits no effect). The tool's projection did not keep the declaration
    # `:effects []` and always made the effect position object, so the hand-written claim vanished from the tool's output.
    lines = stub_of(tmp_path, [tmp_path], _module(tmp_path, EFFECT_FREE)).text.splitlines()
    assert "def copied(x: int) -> _Program[int, _Never]:" in lines
    assert "from typing import Never as _Never" in lines
    # A definition with no declaration is object as before (it does not claim that no effect is emitted).
    assert "def asking(x: int) -> _Program[int, object]:" in lines
