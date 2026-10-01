"""defk / deff / defp / defhandler の頭の辞書の :effects と :tags(agora-redesign #800)。

- 書いた宣言は定義の属性 __doeff_effects__(effect の型の tuple)と __doeff_tags__(DefinitionTags)に残る。無ければ None。
- 知らない鍵は黙って捨てずに断る(反例 = 今までの macro は :tags を書いても何も起きなかった)。
- :tags の鍵・値の形・役の閉じた一覧、:effects の形を変換の時に検める。
"""

from __future__ import annotations

import hy
import pytest

from doeff_hy.declarations import ROLES, DefinitionTags

PRELUDE = """
(require doeff-hy.macros [defk deff defp defeffect do! <-])
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase])
(defclass ReadRow [EffectBase])
(defclass PutRow [EffectBase])
"""


def evaluate(source: str) -> dict[str, object]:
    """PRELUDE と source を 1 つの名前空間で評価し、名前空間を返すため。"""
    namespace: dict[str, object] = {"__name__": "declarations_probe"}
    hy.eval(hy.read_many(PRELUDE + source), namespace)
    return namespace


def refused(source: str) -> str:
    """source の評価が断られることを確かめ、誤りの文を返すため。"""
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
  "ReadRow に答える翻訳。"
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
    # 反例: 今までの macro は :context を辞書の直下に書いても黙って捨てた。
    message = refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :context "kanban"} x)')
    assert ":context" in message and "defk f" in message


def test_the_tags_are_checked_when_compiling() -> None:
    assert "io-effect" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "io-effect"}} x)')
    assert ":role" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k"}} x)')
    assert "文字列" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context k :role "program"}} x)')
    assert ":effects" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects ReadRow} x)')


def test_the_spells_tag_names_a_wire_spelling_point() -> None:
    # agora-redesign #2299: :tags の省ける鍵 :spells(wire の形を綴る 1 点の名乗り — DOEFF172 が数えない形・#2265)。
    # 以前は :context と :role の外の鍵として SyntaxError で、名乗りを書けなかった(反例)。
    ns = evaluate("""
(defk payload-text [x] {:pre [(: x int)] :post [(: % str)] :tags {:context "kanban" :role "protocol" :spells "json"}} (str x))
(defhandler spell-rows {:tags {:context "kanban" :role "protocol" :spells "json"}} (ReadRow [] (resume 1)))
""")
    assert ns["payload_text"].__doeff_tags__ == DefinitionTags(context="kanban", role="protocol", spells="json")
    assert ns["spell_rows"].__doeff_tags__.spells == "json"
    assert DefinitionTags(context="kanban", role="protocol").spells is None


def test_the_spells_tag_takes_only_a_known_spelling_literal() -> None:
    # 失敗ケース: 文字列でない値・閉じた一覧の外の語・:context と :role の欠けは、名乗りを付けても SyntaxError のまま。
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells 1}} x)')
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "yaml"}} x)')
    assert ":role" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :spells "json"}} x)')
    with pytest.raises(ValueError):
        DefinitionTags(context="k", role="protocol", spells="yaml")


def test_spells_and_reads_name_any_outer_form() -> None:
    # agora-redesign #2515: :spells の値は外の形の名(json / http / env / schema)・読む側の名乗り :reads も同じ一覧を受ける。
    ns = evaluate("""
(defk header-of [x] {:pre [(: x int)] :post [(: % str)] :tags {:context "k" :role "protocol" :spells "http"}} (str x))
(defk row-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads "json"}} x)
(defk env-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "entry" :spells "env" :reads "schema"}} x)
""")
    assert ns["header_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", spells="http")
    assert ns["row_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", reads="json")
    assert ns["env_of"].__doeff_tags__ == DefinitionTags(context="k", role="entry", spells="env", reads="schema")


def test_spells_names_the_records_api_form() -> None:
    # agora-redesign #2515(cisco-c8 の決め 2026-10-02 02:00): doeff-records の API の形(ListRows の where・RecordsSchema)を綴る 1 点は
    # :spells "records" と名乗る(schema と名乗ると何の境界かが違う)。一覧の外の名は今までどおり断る。
    ns = evaluate("""
(defk where-of [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "records"}} x)
""")
    assert ns["where_of"].__doeff_tags__ == DefinitionTags(context="k", role="protocol", spells="records")
    assert ":spells" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :spells "record"}} x)')


def test_a_defwire_names_its_outer_form_in_tags() -> None:
    # agora-redesign #2515: 契約どおりの外の形の写像の欄を持つ defwire も :tags に :spells / :reads を名乗れる(DOEFF172 が欄を数えない)。
    ns = evaluate("""
(require doeff-hy.record [defwire])
(import dataclasses [dataclass])
(import pydantic [ConfigDict TypeAdapter])
(defwire UsageWire "公開する自由なキーの索引。" {:tags {:context "k" :role "type" :spells "json"} :names :camel} (#^ int input))
""")
    assert ns["UsageWire"].__doeff_tags__ == DefinitionTags(context="k", role="type", spells="json")


def test_reads_takes_only_a_known_form_literal() -> None:
    # 失敗ケース: 形の名でない :reads(文字列でない・一覧の外)は :spells と同じく SyntaxError・作る時は ValueError。
    assert ":reads" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads 1}} x)')
    assert ":reads" in refused('(defk f [x] {:pre [(: x int)] :post [(: % int)] :tags {:context "k" :role "protocol" :reads "yaml"}} x)')
    with pytest.raises(ValueError):
        DefinitionTags(context="k", role="protocol", reads="yaml")


def test_effects_must_name_effect_types_or_effect_makers() -> None:
    # #800: 前は :effects が名の list であることしか検めず、関数や effect でない class を書いても黙って通した(反例)。
    # 定義の時(module を読む時)に、書けない名と理由を名指して断る。
    assert "EffectBase を継がない class" in refused("(defclass Row [])\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [Row]} x)")
    message = refused("(defn helper [] 1)\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [helper]} x)")
    assert "defk f" in message and "答えの注釈が effect の型でない関数" in message
    assert "型でも関数でもない値" in refused("(setv LIMIT 3)\n(defk f [x] {:pre [(: x int)] :post [(: % int)] :effects [LIMIT]} x)")
    assert "EffectBase を継がない class" in refused("(defclass Row [])\n(defhandler h {:effects [Row]} (ReadRow [] (resume 1)))")


def test_effects_accept_functions_that_make_effects() -> None:
    # doeff_time の GetTime・Delay や doeff の Tell は effect を作る関数(答えの注釈が effect の型)— agora の宣言に在る形。
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
    # agora-redesign #780 の層の確定(core -> intent -> protocol)と、層 entry の 3 種の役(#1108・#1187)。
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
    from doeff_core_effects.scheduler import scheduled

    ns = evaluate("""
(defclass Lease [])
(defclass Refused [])
(defeffect BorrowToken
  "預かり所から access token を借りる。"
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
    with pytest.raises(dataclasses.FrozenInstanceError):  # 反例: frozen なので書けない
        setattr(effect, "profile", "other")
    assert borrow_token.__doeff_answer__ == (ns["Lease"] | ns["Refused"])
    assert borrow_token.__doeff_tags__ == DefinitionTags(context="agent-task", role="intent")
    assert borrow_token.__doeff_defeffect__ is True
    assert borrow_token.__doc__.startswith("預かり所から")
    assert [f.name for f in dataclasses.fields(ns["Tick"])] == []
    # handler で答えて Program の中から使える。
    assert isinstance(run(scheduled(ns["lend"](ns["borrow"]("p")))), ns["Lease"])


def test_defeffect_requires_the_answer_and_tags_and_closed_keys() -> None:
    assert ":answer" in refused('(defeffect E {:tags {:context "k" :role "intent"}})')
    assert ":tags" in refused('(defeffect E {:answer int})')
    assert ":doc" in refused('(defeffect E {:answer int :tags {:context "k" :role "intent"} :doc "x"})')
    assert "(: 名 型)" in refused('(defeffect E {:fields [profile] :answer int :tags {:context "k" :role "intent"}})')
    assert "重複" in refused('(defeffect E {:fields [(: a int) (: a str)] :answer int :tags {:context "k" :role "intent"}})')
    assert "頭の辞書" in refused('(defeffect E)')


def test_defeffect_fields_take_defaults_and_pre_checks_on_construction() -> None:
    """欄の既定値と :pre(agora-redesign #800 — agent_task の StartTurn・DeliverInput・ExportAgentMemory を移すため)。"""
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
    assert effect.lease_id is None  # 既定値
    assert deliver("r1", "queue", "L1").lease_id == "L1"
    assert [f.name for f in dataclasses.fields(deliver)] == ["ref", "mode", "lease_id"]
    # 反例: 閉じた語彙の外の値・欄の型の誤り・要素の型の誤りは作る時に断る(黙って通さない)。
    with pytest.raises(AssertionError, match="DeliverInput: :pre failed"):
        deliver("r1", "shout")
    with pytest.raises(AssertionError, match="`ref` expected str"):
        deliver(1, "steer")
    ns["StartTurn"](("a", "b"))
    with pytest.raises(AssertionError, match="StartTurn: :pre failed"):
        ns["StartTurn"](("a", 2))


def test_defeffect_refuses_misplaced_defaults_and_pre() -> None:
    tags = ':answer int :tags {:context "k" :role "intent"}'
    assert "既定値を持つ欄" in refused('(defeffect E {:fields [(: a int 0) (: b int)] ' + tags + '})')
    assert "欄ではない" in refused('(defeffect E {:fields [(: a int)] :pre [(: b int)] ' + tags + '})')
    assert ":fields の無い" in refused('(defeffect E {:pre [(> 1 0)] ' + tags + '})')
    assert "条件の list" in refused('(defeffect E {:fields [(: a int)] :pre (> a 0) ' + tags + '})')
    assert "defk / do!" in refused('(defeffect E {:fields [(: a int)] :pre [(check = a 0 :reason "x")] ' + tags + '})')


def test_defeffect_runs_carried_names_the_fields_its_handler_runs_in_place() -> None:
    """:runs-carried(agora-redesign #1456)— 答え手が出した所で走らせる Program の欄を宣言し、閉じの検がそれを読む。"""
    from doeff_core_effects.effects import runs_carried_of

    ns = evaluate("""
(import doeff [Program])
(defeffect InTransaction
  {:fields [(: database str) (: work-program Program)]
   :answer int
   :runs-carried [work-program]
   :tags {:context "k" :role "intent"}})
(defeffect Plain {:fields [(: a int)] :answer int :tags {:context "k" :role "intent"}})
""")
    assert runs_carried_of(ns["InTransaction"]) == frozenset({"work_program"})  # Python の属性の名
    assert runs_carried_of(ns["Plain"]) == frozenset()  # 宣言しない effect は運ぶ物を別の所で走らせる
    # 反例: 欄に無い名・list でない形は展開の時に断る(黙って空の宣言にしない)。
    tags = ':answer int :tags {:context "k" :role "intent"}'
    assert "欄の名ではない" in refused('(defeffect E {:fields [(: a int)] :runs-carried [b] ' + tags + '})')
    assert "欄の名の list" in refused('(defeffect E {:fields [(: a int)] :runs-carried a ' + tags + '})')
