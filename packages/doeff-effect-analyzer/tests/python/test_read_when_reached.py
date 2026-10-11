"""閉じの解析が、effect の届いた節だけを読み、同じ scope を辿り直さない事の検(card ki-5a5f6bd26d5e)。

agora-controllers の閉じの検は、土台の handler を読むたびに全部の節の emits(節が中で出す effect)を読み、handled の scope と運ばれた
Program を、そこへ届いた walk ごとに報告し直していた(deliver の組で作った節 452 のうち引かれたのは 118・報告 7,000〜17,500 回に
別の本体 800〜1,900)。節の emits は effect が届いた時に読み、報告は reader に覚えて、答えを変えずに同じ物を作り直さない。
"""

import importlib
import pickle

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer.program_effects import analyze_program, reading_sources

EFFECTS_PY = """\
from doeff_vm import EffectBase


class Ping(EffectBase):
    pass


class Pong(EffectBase):
    pass


class Far(EffectBase):
    pass


class Tick(EffectBase):
    pass
"""

# Ping の節だけが読む module(節の emits を読んだかを、読んだ source の記録で見るため)。
FAR_PY = """\
from doeff import do

from {pkg}.effects import Far


@do
def far_away():
    yield Far()
"""

HANDLERS_PY = """\
from doeff import Pass, Transfer

from {pkg}.effects import Ping, Pong
from {pkg}.far import far_away


def two_clauses(effect, k):
    if isinstance(effect, Ping):
        yield far_away()
        return (yield Transfer(k, None))
    if isinstance(effect, Pong):
        return (yield Transfer(k, None))
    yield Pass(effect, k)
"""

PROGRAMS_PY = """\
from doeff import do
from doeff.program import with_handlers

from {pkg}.effects import Tick
from {pkg}.handlers import two_clauses


@do
def inner():
    yield Tick()


@do
def shared():
    return (yield with_handlers([two_clauses], inner()))


@do
def through_a():
    yield shared()


@do
def through_b():
    yield shared()


@do
def both():
    yield with_handlers([two_clauses], through_a())
    yield with_handlers([two_clauses], through_b())


@do
def x():
    yield Tick()
    yield helper()


@do
def helper():
    return (yield with_handlers([two_clauses], x()))


@do
def top():
    yield x()
    return (yield with_handlers([two_clauses], helper()))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {"effects.py": EFFECTS_PY, "far.py": FAR_PY, "handlers.py": HANDLERS_PY, "programs.py": PROGRAMS_PY}
    )


def _effect(pkg: str, name: str) -> type:
    """検の package の effect の class(check_coverage に渡す effect の組を作るため)。"""
    return getattr(importlib.import_module(f"{pkg}.effects"), name)


def test_a_clause_is_read_when_an_effect_reaches_it_and_counted_where_it_is_read(pkg: str) -> None:
    handler = reading_sources(lambda: analyze_handler(f"{pkg}.handlers:two_clauses"))
    assert f"{pkg}.handlers" in handler.read
    assert f"{pkg}.far" not in handler.read  # Ping の節の本体はまだ読んでいない

    pong = reading_sources(lambda: check_coverage([_effect(pkg, "Pong")], [handler.value]))
    assert pong.value.complete
    assert f"{pkg}.far" not in pong.read  # Pong が届くのは Pong の節だけ

    ping = reading_sources(lambda: check_coverage([_effect(pkg, "Ping")], [handler.value]))
    assert f"{pkg}.far" in ping.read  # 遅れて読んだ source は、引いた所の reading_sources が数える
    assert [gap.effect.__name__ for gap in ping.value.gaps] == ["Far"]


def test_a_pickled_handler_keeps_what_its_clauses_emit(pkg: str) -> None:
    handler = analyze_handler(f"{pkg}.handlers:two_clauses")

    copied = pickle.loads(pickle.dumps(handler))

    assert copied == handler
    [ping] = [clause for clause in copied.clauses if clause.handles.__name__ == "Ping"]
    assert [use.effect.__name__ for use in ping.emits.effects] == ["Far"]


def test_a_scope_reached_again_under_the_same_lookups_is_the_report_walked_before(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:both")

    under_a, under_b = report.handled
    [shared_a] = under_a.program.handled
    [shared_b] = under_b.program.handled
    assert shared_a.via != shared_b.via  # 別の道で届いた
    assert shared_a.program is shared_b.program  # 同じ本体を辿り直さない
    assert [use.effect.__name__ for use in shared_a.program.effects] == ["Tick"]


def test_a_scope_reached_where_a_call_in_it_is_being_reported_is_walked_again(pkg: str) -> None:
    # helper の scope は x を呼ぶ。top の handled の scope から届いた時は x を読み(Tick)、x を報告している途中で届いた時は
    # x を読まない — 覚えた報告を、見た鍵の上の active が違う所で使い回さない。
    report = analyze_program(f"{pkg}.programs:top")

    in_scope, under_x = report.handled
    [helper_in_scope] = in_scope.program.handled
    assert helper_in_scope.program.target == under_x.program.target
    assert [use.effect.__name__ for use in helper_in_scope.program.effects] == ["Tick"]
    assert under_x.program.effects == ()
