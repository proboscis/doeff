"""The :effects and :tags in the header dict of defk / deff / defp / defhandler.

- A declaration that was written stays on the definition as the attributes __doeff_effects__ (a tuple of effect types) and __doeff_tags__ (DefinitionTags). None if absent.
- An unknown key is refused instead of being silently dropped (counterexample = the old macro did nothing when :tags was written).
- The shape of the keys and values of :tags, the closed list of roles, and the shape of :effects are checked at transform time.
"""

from __future__ import annotations

import hy
import pytest

from doeff_hy._declarations import ROLES, DefinitionTags

PRELUDE = """
(require doeff-hy.macros [defk deff defp defeffect do! <-])
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase])
(defclass ReadRow [EffectBase])
(defclass PutRow [EffectBase])
"""


def evaluate(source: str) -> dict[str, object]:
    """To evaluate PRELUDE and source in one namespace and return the namespace."""
    namespace: dict[str, object] = {"__name__": "declarations_probe"}
    hy.eval(hy.read_many(PRELUDE + source), namespace)
    return namespace


def refused(source: str) -> str:
    """To check that evaluating source is refused, and return the error message."""
    with pytest.raises(Exception) as caught:
        evaluate(source)
    return str(caught.value)


def test_a_defk_keeps_its_declared_effects_and_tags() -> None:
    ns = evaluate("""
(defk create-card [x]
  {:pre [(: x int)] :post [(: % int)]
   :effects [ReadRow PutRow]
   :tags {:context "kanban" :role "program"}}
  x)
""")
    card = ns["create_card"]
    assert card.__doeff_effects__ == (ns["ReadRow"], ns["PutRow"])
    assert card.__doeff_tags__ == DefinitionTags(context="kanban", role="program")


def test_an_empty_effects_list_means_no_effects_and_absence_means_undeclared() -> None:
    ns = evaluate("""
(defk judge [x] {:pre [(: x int)] :post [(: % bool)] :effects [] :tags {:context "kanban" :role "judgment"}} (> x 0))
(defk plain [x] {:pre [(: x int)] :post [(: % int)]} x)
""")
    assert ns["judge"].__doeff_effects__ == ()
    assert ns["plain"].__doeff_effects__ is None
    assert ns["plain"].__doeff_tags__ is None


def test_deff_defp_and_defhandler_keep_declarations_too() -> None:
    ns = evaluate("""
(deff twice [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "shared" :role "judgment"}} (* x 2))
(defp answer {:post [(: % int)] :tags {:context "shared" :role "entry"}} 42)
(defhandler read-rows
  "A translation that answers ReadRow."
  {:effects [PutRow] :tags {:context "kanban" :role "protocol"}}
  (ReadRow [] (resume 1)))
(defhandler bare (ReadRow [] (resume 1)))
""")
    assert ns["twice"].__doeff_tags__ == DefinitionTags(context="shared", role="judgment")
    assert ns["answer"].__doeff_tags__ == DefinitionTags(context="shared", role="entry")
    assert ns["read_rows"].__doeff_effects__ == (ns["PutRow"],)
    assert ns["read_rows"].__doeff_tags__ == DefinitionTags(context="kanban", role="protocol")
    assert ns["bare"].__doeff_tags__ is None


def test_an_unknown_key_is_refused_instead_of_dropped() -> None:
    # Counterexample: the old macro silently dropped :context even when written directly under the dict.
    message = refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :context "kanban"} x)')
    assert ":context" in message and "defk f" in message


def test_a_function_declares_what_it_writes_and_nothing_happens_at_run_time() -> None:
    # A non-job writer (a task Program sent from a system job) declares what it writes to outside stores in the same form as the :writes of a defsystem job
    # (the consumer's doeff-linter DOEFF207 reads the source spelling). Previously this was a SyntaxError as an unknown key (counterexample).
    ns = evaluate("""
(defk run-task [x] {:pre [(: x int)] :post [(: % int)] :writes #{"record:turn" "records:turn-event"}} x)
(deff count-rows [x] {:pre [(: x int)] :post [(: % int)] :writes #{}} x)
""")
    assert ns["count_rows"](3) == 3
    assert not hasattr(ns["run_task"], "__doeff_writes__")


def test_the_writes_of_a_function_are_checked_like_a_system_job() -> None:
    # The form is checked the same way as the :writes of a doeff-cluster defsystem job (declarations.record-names) — a value that is not a set, or a mistaken spelling, is refused by the expansion.
    assert "set of string literals" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :writes ["record:turn"]} x)')
    assert "<store>:<name>" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :writes #{"turn"}} x)')
    assert "<store>:<name>" in refused('(deff f [x] {:pre [(: x int)] :post [(: % int)] :writes #{"a:b:c"}} x)')
    # :reads is not accepted (there is no rule to check reads of non-job definitions). defp does not accept :writes either (a key only for function contracts).
    assert ":reads" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :reads #{"record:turn"}} x)')
    assert ":writes" in refused('(defp g {:post [(: % int)] :writes #{"record:turn"}} 1)')


def test_the_tags_are_checked_when_compiling() -> None:
    assert "io-effect" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "io-effect"}} x)')
    assert ":role" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k"}} x)')
    assert "string literal" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context k :role "program"}} x)')
    assert ":effects" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects ReadRow} x)')


def test_the_spells_tag_names_a_wire_spelling_point() -> None:
    # The omittable :tags key :spells (a claim that the definition exists to spell out a wire shape at one point — a shape DOEFF172 does not count; #2265).
    # Previously it was a SyntaxError as a key outside :context and :role, so the claim could not be written (counterexample).
    ns = evaluate("""
(defk payload-text [x] {:pre [(: x int)] :post [(: % str)] :tags {:context "kanban" :role "protocol" :spells "json"}} (str x))
(defhandler spell-rows {:tags {:context "kanban" :role "protocol" :spells "json"}} (ReadRow [] (resume 1)))
""")
    assert ns["payload_text"].__doeff_tags__ == DefinitionTags(context="kanban", role="protocol", spells="json")
    assert ns["spell_rows"].__doeff_tags__.spells == "json"
    assert DefinitionTags(context="kanban", role="protocol").spells is None


def test_the_spells_tag_takes_only_a_known_spelling_literal() -> None:
    # Failure cases: a non-string value, a word outside the closed list, and a missing :context or :role remain a SyntaxError even with the claim attached.
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells 1}} x)')
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "yaml"}} x)')
    assert ":role" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :spells "json"}} x)')
    with pytest.raises(ValueError):
        DefinitionTags(context="k", role="protocol", spells="yaml")


def test_spells_and_reads_name_any_outer_form() -> None:
    # The value of :spells is a name of an outside shape (json / http / env / schema); the reading-side claim :reads accepts the same list.
    ns = evaluate("""
(defk header-of [x] {:pre [(: x int)] :post [(: % str)] :tags {:context "k" :role "protocol" :spells "http"}} (str x))
(defk row-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads "json"}} x)
(defk env-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "entry" :spells "env" :reads "schema"}} x)
""")
    assert ns["header_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", spells="http")
    assert ns["row_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", reads="json")
    assert ns["env_of"].__doeff_tags__ == DefinitionTags(context="k", role="entry", spells="env", reads="schema")


def test_spells_names_the_records_api_form() -> None:
    # [removed]
    # claimed as :spells "records" (claiming schema would mean a different boundary). Names outside the list are still refused as before.
    ns = evaluate("""
(defk where-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "records"}} x)
""")
    assert ns["where_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", spells="records")
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "record"}} x)')


def test_a_defwire_names_its_outer_form_in_tags() -> None:
    # A defwire that has a field of a mapping of an outside shape per the contract can also claim :spells / :reads in :tags (DOEFF172 does not count the field).
    ns = evaluate("""
(require doeff-hy.record [defwire])
(import dataclasses [dataclass])
(import pydantic [ConfigDict TypeAdapter])
(defwire UsageWire "An index of free-form keys to expose." {:tags {:context "k" :role "type" :spells "json"} :names :camel} (#^ int input))
""")
    assert ns["UsageWire"].__doeff_tags__ == DefinitionTags(context="k", role="type", spells="json")


def test_reads_takes_only_a_known_form_literal() -> None:
    # Failure cases: a :reads that is not a shape name (not a string, or outside the list) is a SyntaxError just like :spells, and a ValueError at construction.
    assert ":reads" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads 1}} x)')
    assert ":reads" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads "yaml"}} x)')
    with pytest.raises(ValueError):
        DefinitionTags(context="k", role="protocol", reads="yaml")


def test_effects_must_name_effect_types_or_effect_makers() -> None:
    # #800: it used to check only that :effects was a list of names, and silently let through functions and classes that are not effects (counterexample).
    # At definition time (when the module is read), refuse naming the name that cannot be written and the reason.
    assert "class that does not inherit EffectBase" in refused("(defclass Row [])\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [Row]} x)")
    message = refused("(defn helper [] 1)\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [helper]} x)")
    assert "defk f" in message and "function whose return annotation is not an effect type" in message
    assert "value that is neither a type nor a function" in refused("(setv LIMIT 3)\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [LIMIT]} x)")
    assert "class that does not inherit EffectBase" in refused("(defclass Row [])\n(defhandler h {:effects [Row]} (ReadRow [] (resume 1)))")


def test_effects_accept_functions_that_make_effects() -> None:
    # GetTime / Delay of doeff_time and Tell of doeff are functions that make effects (the return annotation is an effect type) — a shape found in agora's declarations.
    ns = evaluate("""
(import doeff_time [GetTime Delay])
(import doeff [Tell])
(defk waits [x] {:pre [(: x int)] :post [(: % int)] :effects [GetTime Delay Tell ReadRow]} x)
""")
    assert ns["waits"].__doeff_effects__ == (ns["GetTime"], ns["Delay"], ns["Tell"], ns["ReadRow"])


def test_a_handler_refuses_pre_and_post_and_do_refuses_declarations() -> None:
    assert ":pre" in refused('(defhandler h {:pre []} (ReadRow [] (resume 1)))')
    assert ":tags" in refused('(defn g [] (do! {:tags {:context "k" :role "program"}} 1))')


def test_the_roles_are_the_closed_list_of_the_decision() -> None:
    # The decided layers (core -> intent -> protocol), and the three roles of layer entry.
    assert ROLES == ("type", "judgment", "program", "intent", "protocol", "foundation", "entry", "system", "process", "main")
    for entry_role in ("system", "process", "main"):
        assert DefinitionTags(context="kanban", role=entry_role).role == entry_role
    for retired in ("io-effect", "effect", "translation"):
        with pytest.raises(ValueError):
            DefinitionTags(context="kanban", role=retired)
    with pytest.raises(ValueError):
        DefinitionTags(context="", role="program")


def test_defeffect_makes_a_frozen_effect_with_its_answer_and_tags() -> None:
    import dataclasses

    from doeff import EffectBase, run
    from doeff_core_effects import scheduled

    ns = evaluate("""
(defclass Lease [])
(defclass Refused [])
(defeffect BorrowToken
  "Borrow an access token from the custody store."
  {:fields [(: profile str) (: purpose str)]
   :answer (| Lease Refused)
   :tags {:context "agent-task" :role "intent"}})
(defeffect Tick {:answer int :tags {:context "shared" :role "intent"}})
(defhandler lend (BorrowToken [profile purpose] (resume (Lease))))
(defk borrow [p] {:pre [(: p str)] :post [(: % Lease)] :effects [BorrowToken] :tags {:context "agent-task" :role "program"}}
  (<- lease (BorrowToken p "run"))
  lease)
""")
    borrow_token = ns["BorrowToken"]
    effect = borrow_token("p", "run")
    assert isinstance(effect, EffectBase) and dataclasses.is_dataclass(effect)
    assert [f.name for f in dataclasses.fields(borrow_token)] == ["profile", "purpose"]
    with pytest.raises(dataclasses.FrozenInstanceError):  # Counterexample: it is frozen, so it cannot be written
        setattr(effect, "profile", "other")
    assert borrow_token.__doeff_answer__ == (ns["Lease"] | ns["Refused"])
    assert borrow_token.__doeff_tags__ == DefinitionTags(context="agent-task", role="intent")
    assert borrow_token.__doeff_defeffect__ is True
    assert borrow_token.__doc__.startswith("Borrow an")
    assert [f.name for f in dataclasses.fields(ns["Tick"])] == []
    # It can be answered by a handler and used from inside a Program.
    assert isinstance(run(scheduled(ns["lend"](ns["borrow"]("p")))), ns["Lease"])


def test_defeffect_requires_the_answer_and_tags_and_closed_keys() -> None:
    assert ":answer" in refused('(defeffect E {:tags {:context "k" :role "intent"}})')
    assert ":tags" in refused('(defeffect E {:answer int})')
    assert ":doc" in refused('(defeffect E {:answer int :tags {:context "k" :role "intent"} :doc "x"})')
    assert "(: name type)" in refused('(defeffect E {:fields [profile] :answer int :tags {:context "k" :role "intent"}})')
    assert "duplicated" in refused('(defeffect E {:fields [(: a int) (: a str)] :answer int :tags {:context "k" :role "intent"}})')
    assert "leading dict" in refused('(defeffect E)')


def test_defeffect_fields_take_defaults_and_pre_checks_on_construction() -> None:
    """Field defaults and :pre (to carry over StartTurn, DeliverInput, ExportAgentMemory of agent_task)."""
    import dataclasses

    ns = evaluate("""
(setv MODES #{"steer" "queue"})
(defeffect DeliverInput
  {:fields [(: ref str) (: mode str) (: lease-id (| str None) None)]
   :pre [(: ref str) (in mode MODES)]
   :answer str
   :tags {:context "agent-task" :role "intent"}})
(defeffect StartTurn
  {:fields [(: inputs tuple)]
   :pre [(all (gfor item inputs (isinstance item str)))]
   :answer str
   :tags {:context "agent-task" :role "intent"}})
""")
    deliver = ns["DeliverInput"]
    effect = deliver("r1", "steer")
    assert effect.lease_id is None  # default value
    assert deliver("r1", "queue", "L1").lease_id == "L1"
    assert [f.name for f in dataclasses.fields(deliver)] == ["ref", "mode", "lease_id"]
    # Counterexample: a value outside the closed vocabulary, a wrong field type, and a wrong element type are refused at construction (not silently let through).
    with pytest.raises(AssertionError, match="DeliverInput: :pre failed"):
        deliver("r1", "shout")
    with pytest.raises(AssertionError, match="`ref` expected str"):
        deliver(1, "steer")
    ns["StartTurn"](("a", "b"))
    with pytest.raises(AssertionError, match="StartTurn: :pre failed"):
        ns["StartTurn"](("a", 2))


def test_defeffect_refuses_misplaced_defaults_and_pre() -> None:
    tags = ':answer int :tags {:context "k" :role "intent"}'
    assert "field with a default" in refused('(defeffect E {:fields [(: a int 0) (: b int)] ' + tags + '})')
    assert "is not a field" in refused('(defeffect E {:fields [(: a int)] :pre [(: b int)] ' + tags + '})')
    assert "without :fields" in refused('(defeffect E {:pre [(> 1 0)] ' + tags + '})')
    assert "list of conditions" in refused('(defeffect E {:fields [(: a int)] :pre (> a 0) ' + tags + '})')
    assert "defk / do!" in refused('(defeffect E {:fields [(: a int)] :pre [(check = a 0 :reason "x")] ' + tags + '})')


def test_defeffect_runs_carried_names_the_fields_its_handler_runs_in_place() -> None:
    """:runs-carried — declares the fields holding a Program that the answerer runs at the place where it was performed, and the closure check reads it."""
    from doeff_core_effects._effects import runs_carried_of

    ns = evaluate("""
(import doeff [Program])
(defeffect InTransaction
  {:fields [(: database str) (: work-program Program)]
   :answer int
   :runs-carried [work-program]
   :tags {:context "k" :role "intent"}})
(defeffect Plain {:fields [(: a int)] :answer int :tags {:context "k" :role "intent"}})
""")
    assert runs_carried_of(ns["InTransaction"]) == frozenset({"work_program"})  # the name of the Python attribute
    assert runs_carried_of(ns["Plain"]) == frozenset()  # an effect that does not declare it runs what it carries somewhere else
    # Counterexample: a name that is not a field, or a shape that is not a list, is refused at expansion time (not silently turned into an empty declaration).
    tags = ':answer int :tags {:context "k" :role "intent"}'
    assert "is not a field name" in refused('(defeffect E {:fields [(: a int)] :runs-carried [b] ' + tags + '})')
    assert "list of :fields field names" in refused('(defeffect E {:fields [(: a int)] :runs-carried a ' + tags + '})')
