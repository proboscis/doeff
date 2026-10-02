"""Handler lists a builder splits and puts back together (agora-redesign #2674).

agora's ``claude-runtime-handlers`` took doeff-agents' pair apart and put a handler of its
own between the two halves (a75d84047)::

    (setv [lower adapter] (claude-agent-runtime-handlers …))
    (<- launch list (managed-launch [] adapter))
    [lower #* launch]

The reader lost the three shapes in it: a name unpacked from a list (``lower``), a builder's
parameter bound to what the caller wrote (``around`` ← ``[]``, ``adapter`` ← the caller's
unpacked name; also ``(get runtime 0)``), and a ``<-``-bound defk builder nested deep
enough to hit the hop limit.  Reading them, a part of a list the reader cannot open keeps
the name that list was reported by (the library's unreadable handler), and a part of a list
it can open is that handler with its clauses.  A value that really cannot be followed (a
builder passed in as a parameter) stays unread, named.
"""

import pytest
from doeff_effect_analyzer.handler_effects import Basis, analyze_env, check_coverage
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from doeff_vm import EffectBase


class Ping(EffectBase):
    pass


class Raw(EffectBase):
    pass


class Mark(EffectBase):
    pass


class Tick(EffectBase):
    pass
"""

HANDLERS_HY = """\
(require doeff-hy.macros [defhandler <-])
(import {pkg}.effects [Ping Raw Mark Tick])

(defhandler lower-world
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Raw [] (resume 1)))

(defhandler adapter
  {:tags {:context "analyzer-test" :role "protocol"}}
  (Ping [] (<- r (Raw)) (resume r)))

(defhandler marker
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Mark [] (resume None)))

(defhandler ticker
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Tick [] (resume None)))
"""

# The library side (doeff-agents' shape): a pair the reader can open, and one it cannot —
# the list comes from a module it imports when called.
LIBRARY_PY = """\
from importlib import import_module

from {pkg}.handlers import adapter, lower_world


def _compose():
    return import_module("{pkg}.handlers")


def readable_runtime_handlers():
    return [lower_world, adapter]


def opaque_runtime_handlers():
    return _compose().runtime_pair()
"""

OPAQUE = "_compose().runtime_pair()"

ENVS_HY = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import {pkg}.handlers [marker ticker])
(import {pkg}.library [readable-runtime-handlers opaque-runtime-handlers])

(defk managed-launch [around adapter]
  {:pre [(: around list) (: adapter Callable)] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [marker #* around adapter])

;; agora's claude-runtime-handlers (a75d84047), over a pair the reader cannot open.
(defk opaque-split-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (setv [lower adapter] (opaque-runtime-handlers))
  (<- launch list (managed-launch [] adapter))
  [lower #* launch])

;; The same shape over a pair it can open, with a handler between the halves.
(defk readable-split-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (setv [lower adapter] (readable-runtime-handlers))
  (<- launch list (managed-launch [ticker] adapter))
  [lower #* launch])

;; A builder taking the list as an argument and placing its elements by index.
(defk indexed-handlers [runtime]
  {:pre [(: runtime list)] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [(get runtime 0) marker (get runtime 1)])

(defk indexed-split-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (<- built list (indexed-handlers (readable-runtime-handlers)))
  built)

;; Unpacking more names than the list holds is not read as a guess.
(defk miscounted-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (setv [lower adapter extra] (readable-runtime-handlers))
  [lower adapter extra])

;; The pair comes from a builder passed in: nothing to follow.
(defk chosen-split-handlers [choose]
  {:pre [(: choose Callable)] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (setv [lower adapter] (choose))
  [lower marker adapter])
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(import {pkg}.effects [Ping Mark Tick])

(defk talk []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Mark))
  (<- (Tick))
  (<- answer (Ping))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {
            "effects.py": EFFECTS_PY,
            "handlers.hy": HANDLERS_HY,
            "library.py": LIBRARY_PY,
            "envs.hy": ENVS_HY,
            "programs.hy": PROGRAMS_HY,
        }
    )


def test_parts_of_an_unreadable_list_keep_its_name(pkg: str) -> None:
    env = analyze_env(f"{pkg}.envs:opaque_split_handlers")

    assert [h.name for h in env] == [OPAQUE, "marker", OPAQUE]
    assert [h.basis for h in env] == [Basis.UNREAD, Basis.CLAUSES, Basis.UNREAD]
    # Each part says which element of the unopened list it is.
    assert [u.text for u in env[0].unresolved][-1] == "lower"
    assert [u.text for u in env[2].unresolved][-1] == "adapter"
    coverage = check_coverage(analyze_program(f"{pkg}.programs:talk"), env)
    assert coverage.unknown_handlers == (OPAQUE,)
    assert {gap.effect.__name__ for gap in coverage.gaps} == {"Ping", "Tick"}


def test_parts_of_a_readable_list_are_its_handlers(pkg: str) -> None:
    env = analyze_env(f"{pkg}.envs:readable_split_handlers")

    assert [h.name for h in env] == ["lower_world", "marker", "ticker", "adapter"]
    assert all(h.known for h in env), [h.to_dict() for h in env]
    coverage = check_coverage(analyze_program(f"{pkg}.programs:talk"), env)
    assert coverage.complete, coverage


def test_elements_of_a_list_passed_to_a_builder(pkg: str) -> None:
    env = analyze_env(f"{pkg}.envs:indexed_split_handlers")

    assert [h.name for h in env] == ["lower_world", "marker", "adapter"]
    assert all(h.known for h in env), [h.to_dict() for h in env]


def test_what_cannot_be_followed_stays_named(pkg: str) -> None:
    miscounted = analyze_env(f"{pkg}.envs:miscounted_handlers")
    chosen = analyze_env(f"{pkg}.envs:chosen_split_handlers")

    assert [h.name for h in miscounted] == ["lower", "adapter", "extra"]
    assert not any(h.known for h in miscounted)
    assert miscounted[0].unresolved[-1].reason == "unpacks 3 names from a list of 2 handlers"
    assert [h.name for h in chosen] == ["choose()", "marker", "choose()"]
    assert [h.known for h in chosen] == [False, True, False]
