"""handler の ``match`` の ``case`` が or の形(``case A(...) | B(...):``)の時、解析器がその各 class を同じ本体の答えとして読む事の検。

doeff 88834a040(L1806)で、doeff-events の ``subscribed_event_handler`` は ``WaitForEvent`` と ``WaitForEvents`` を 1 つの
``case WaitForEventEffect(...) | WaitForEventsEffect(...):`` で答える形になった。解析器の ``handler_effects._branches`` は単独の class の
形(``ast.MatchClass``)だけを読み、or の形(``ast.MatchOr``)を読み飛ばしていたので、この handler が ``WaitForEventEffect`` に答える事が
解析に見えず、agora の閉じの検(test_delivery_closure ほか 4 本)が「答え手の無い WaitForEventEffect」の隙間で赤になった(T3 の pin・
2026-10-07)。Python の正しい書き方を読めない解析器の欠けなので、解析器の側で or の形を読む。
"""

from doeff_effect_analyzer.handler_effects import analyze_handler
from doeff_events import EventBus, subscribed_event_handler
from doeff_events.effects import PublishEffect, WaitForEventEffect, WaitForEventsEffect


class Signal:
    """検の合図(購読の型)。"""


def test_an_or_pattern_case_is_read_as_a_clause_for_each_of_its_classes() -> None:
    read = analyze_handler(subscribed_event_handler(EventBus(), "s", (Signal,)), name="subscribed")
    handled = {clause.handles for clause in read.clauses}
    assert {WaitForEventEffect, WaitForEventsEffect, PublishEffect} <= handled, handled
