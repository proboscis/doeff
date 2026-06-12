from __future__ import annotations

from dataclasses import dataclass

import pytest
from doeff_vm import EffectBase
from doeff_vm import UnhandledEffect as VmUnhandledEffect

from doeff import Pass, UnhandledEffect, do, run
from doeff import handler as _install_raw_handler


@dataclass(frozen=True, kw_only=True)
class MissingEffect(EffectBase):
    label: str


@do
def pass_through(effect, k):
    yield Pass(effect, k)


def test_class_import_path():
    assert UnhandledEffect is VmUnhandledEffect


def test_class_hierarchy():
    assert issubclass(UnhandledEffect, RuntimeError)


def test_no_handler_raises_unhandled_effect():
    @do
    def program():
        yield MissingEffect(label="no-handler")

    with pytest.raises(UnhandledEffect):
        run(program())


def test_pass_falls_off_top_raises_unhandled_effect():
    @do
    def program():
        yield MissingEffect(label="pass-fallthrough")

    with pytest.raises(UnhandledEffect):
        run(_install_raw_handler(pass_through)(program()))


def test_unhandled_effect_is_catchable_inside_do():
    @do
    def missing_program():
        yield MissingEffect(label="catchable")

    @do
    def program():
        try:
            return (yield missing_program())
        except UnhandledEffect:
            return "fallback"

    assert run(program()) == "fallback"


def test_pass_fallthrough_unhandled_effect_is_catchable_inside_do():
    @do
    def program():
        try:
            yield MissingEffect(label="catchable-after-pass")
        except UnhandledEffect:
            return "fallback"

    assert run(_install_raw_handler(pass_through)(program())) == "fallback"


def test_unhandled_effect_preserves_doeff_traceback():
    @do
    def program():
        yield MissingEffect(label="traceback")

    with pytest.raises(UnhandledEffect) as exc_info:
        run(_install_raw_handler(pass_through)(program()))

    tb = getattr(exc_info.value, "__doeff_traceback__", None)
    assert isinstance(tb, list)
    handler_entries = [
        entry
        for entry in tb
        if isinstance(entry, list)
        and len(entry) >= 3
        and entry[0] == "handler"
        and entry[1] == "chain"
    ]
    assert handler_entries
    assert "pass_through" in handler_entries[0][2]
