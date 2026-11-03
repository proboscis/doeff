"""doeff-conductor の make_blocking_scheduled_handler・make_async_scheduled_handler は名のまま工場 — fn を受け、全部の effect に
fn(effect) で答える handler を返す(ADR-DOE-API-001 R3・R6・R16。async の方は DeprecationWarning を出す)。

root の tests/test_handler_wrapper_factories.py(program に handler を被せていた 8 名)から、doeff-conductor の分を分けた
(2026-11-04 — root の tests が内輪の package を import しないため)。
"""

from __future__ import annotations

import pytest
from doeff_conductor import make_async_scheduled_handler, make_blocking_scheduled_handler

from doeff import Tell, do, run


@do
def _tells():
    return (yield Tell("asked"))


def _marked(value: object) -> bool:
    return getattr(value, "_doeff_is_handler_fn", False) is True


def test_make_blocking_scheduled_handler_returns_a_handler_that_answers_with_the_function() -> None:
    answering = make_blocking_scheduled_handler(lambda effect: f"answered {effect.msg}")

    assert _marked(answering)
    assert run(answering(_tells())) == "answered asked"


def test_make_async_scheduled_handler_warns_and_returns_the_same_handler() -> None:
    with pytest.warns(DeprecationWarning, match="make_async_scheduled_handler is deprecated"):
        answering = make_async_scheduled_handler(lambda effect: "answered")

    assert _marked(answering)
    assert run(answering(_tells())) == "answered"
