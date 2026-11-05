"""Test that when a handler's ``match`` has a ``case`` in or form (``case A(...) | B(...):``), the analyzer reads each of its classes as answered by the same body.

In doeff 88834a040 (L1806), doeff-events' ``subscribed_event_handler`` came to answer ``WaitForEvent`` and ``WaitForEvents`` with a single
``case WaitForEventEffect(...) | WaitForEventsEffect(...):``. The analyzer's ``handler_effects._branches`` read only the single-class
form (``ast.MatchClass``) and skipped the or form (``ast.MatchOr``), so the fact that this handler answers ``WaitForEventEffect`` was invisible to the
analysis, and agora's closure tests (test_delivery_closure and 4 others) went red on the gap "WaitForEventEffect with no responder" (T3 pin,
2026-10-07). This is a gap in an analyzer that cannot read valid Python, so the analyzer side is fixed to read the or form.
"""

from doeff_effect_analyzer._handler_effects import analyze_handler
from doeff_events import (
    EventBus,
    PublishEffect,
    WaitForEventEffect,
    WaitForEventsEffect,
    subscribed_event_handler,
)


class Signal:
    """Signal for the test (the subscribed type)."""


def test_an_or_pattern_case_is_read_as_a_clause_for_each_of_its_classes() -> None:
    read = analyze_handler(subscribed_event_handler(EventBus(), "s", (Signal,)), name="subscribed")
    handled = {clause.handles for clause in read.clauses}
    assert {WaitForEventEffect, WaitForEventsEffect, PublishEffect} <= handled, handled
