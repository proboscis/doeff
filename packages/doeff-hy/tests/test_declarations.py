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
(require doeff-hy.macros [defk deff defp do! <-])
(require doeff-hy.handle [defhandler])
(defclass ReadRow [])
(defclass PutRow [])
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
  {:effects [PutRow] :tags {:context "kanban" :role "translation"}}
  (ReadRow [] (resume 1)))
(defhandler bare (ReadRow [] (resume 1)))
""")
    assert ns["twice"].__doeff_tags__ == DefinitionTags(context="shared", role="judgment")
    assert ns["answer"].__doeff_tags__ == DefinitionTags(context="shared", role="entry")
    assert ns["read_rows"].__doeff_effects__ == (ns["PutRow"],)
    assert ns["read_rows"].__doeff_tags__ == DefinitionTags(context="kanban", role="translation")
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


def test_a_handler_refuses_pre_and_post_and_do_refuses_declarations() -> None:
    assert ":pre" in refused('(defhandler h {:pre []} (ReadRow [] (resume 1)))')
    assert ":tags" in refused('(defn g [] (do! {:tags {:context "k" :role "program"}} 1))')


def test_the_roles_are_the_closed_list_of_the_decision() -> None:
    # agora-redesign #780 の決定(io-effect は外した)。
    assert ROLES == ("type", "effect", "judgment", "program", "translation", "foundation", "entry")
    with pytest.raises(ValueError):
        DefinitionTags(context="kanban", role="io-effect")
    with pytest.raises(ValueError):
        DefinitionTags(context="", role="program")
