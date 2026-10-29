"""閉じの解析が、effect の届いた節だけを読み、同じ scope を辿り直さない事の検(card ki-5a5f6bd26d5e)。

agora-controllers の閉じの検は、土台の handler を読むたびに全部の節の emits(節が中で出す effect)を読み、handled の scope と運ばれた
Program を、そこへ届いた walk ごとに報告し直していた(deliver の組で作った節 452 のうち引かれたのは 118・報告 7,000〜17,500 回に
別の本体 800〜1,900)。節の emits は effect が届いた時に読み、報告は reader に覚えて、答えを変えずに同じ物を作り直さない。

設計検証の盲検(docs/design/analyzer-read-when-reached-ki-5a5f6bd26d5e)が見つけた 2 つの失敗例も、ここで拒む: 1 つの handler の節を
2 つの thread が同時に引くと、節が共有する reader の読みかけの事実を辿って effect を落とした(A)。handler の読みを process で覚えると、
解析の答えと読んだ source の数えが、同じ process で前に走った解析に依った(B — `:absent` の受け手が実際にそう覚えられていた)。
"""

import importlib
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from doeff_effect_analyzer._handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer._program_effects import analyze_program, reading_sources

EFFECTS_PY = """\
from doeff import EffectBase


class Ping(EffectBase):
    pass


class Pong(EffectBase):
    pass


class Far(EffectBase):
    pass


class Tick(EffectBase):
    pass


class X(EffectBase):
    pass


class Y(EffectBase):
    pass


class Z(EffectBase):
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


# 2 つの節が同じ関数を呼ぶ(2 つの thread が別の節を引くと、同じ呼ばれ先の事実を読む)。
def shared_far(effect, k):
    if isinstance(effect, Ping):
        yield far_away()
        return (yield Transfer(k, None))
    if isinstance(effect, Pong):
        yield far_away()
        return (yield Transfer(k, None))
    yield Pass(effect, k)
"""

# 節の中で互いを入れる handler の組(盲検 B の入力): A の X の節は B を入れて Y を出し、B の Y の節は A を入れて Z を出す。
MUTUAL_PY = """\
from doeff import Pass, Transfer, do
from doeff.program import with_handlers

from {pkg}.effects import X, Y, Z


@do
def asks_y():
    yield Y()


@do
def asks_z():
    yield Z()


@do
def asks_x():
    yield X()


def handler_a(effect, k):
    if isinstance(effect, X):
        yield with_handlers([handler_b], asks_y())
        return (yield Transfer(k, None))
    if isinstance(effect, Z):
        return (yield Transfer(k, None))
    yield Pass(effect, k)


def handler_b(effect, k):
    if isinstance(effect, Y):
        yield with_handlers([handler_a], asks_z())
        return (yield Transfer(k, None))
    yield Pass(effect, k)


@do
def warm():
    yield with_handlers([handler_a], asks_x())


@do
def job():
    yield with_handlers([handler_b], asks_y())
"""

# `(<- x e :absent F)` の形(doeff-hy の bind が書く open_bind)の Program 2 つ。
ABSENT_PY = """\
from doeff import do
from doeff_core_effects._effects import Absent
from doeff_core_effects._outcomes import open_bind


@do
def maybe_one():
    yield Absent("none")


@do
def first():
    return (yield open_bind(maybe_one(), lambda: "first needs one"))


@do
def second():
    return (yield open_bind(maybe_one(), lambda: "second needs one"))
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
        {
            "effects.py": EFFECTS_PY,
            "far.py": FAR_PY,
            "handlers.py": HANDLERS_PY,
            "programs.py": PROGRAMS_PY,
            "mutual.py": MUTUAL_PY,
            "absent.py": ABSENT_PY,
        }
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


def _gap_names(coverage) -> list[str]:
    """答えの gap の effect の名(検の比べのため)。"""
    return [gap.effect.__name__ for gap in coverage.gaps]


def test_two_threads_asking_two_clauses_of_one_handler_both_read_what_they_emit(make_package) -> None:
    # 盲検 A: 新しい package で handler を読み、2 つの thread が同時に別の節を引く。節は reader を共有するので、守りが無いと片方が
    # もう片方の読みかけ(空)の far_away の事実を辿り、Far を落とした(直す前の main で 200 回中 173 回)。
    rounds = 12
    wrong = []
    for _ in range(rounds):
        pkg = make_package({"effects.py": EFFECTS_PY, "far.py": FAR_PY, "handlers.py": HANDLERS_PY})
        handler = analyze_handler(f"{pkg}.handlers:shared_far")
        barrier = threading.Barrier(2)

        def ask(effect: type, handler=handler, barrier=barrier) -> list[str]:
            barrier.wait()
            return _gap_names(check_coverage([effect], [handler]))

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(ask, [_effect(pkg, "Ping"), _effect(pkg, "Pong")]))
        if answers != [["Far"], ["Far"]]:
            wrong.append(answers)
    assert wrong == []


def test_two_analyses_each_count_the_absent_receiver_they_read(pkg: str) -> None:
    # 盲検 A: `:absent` の受け手が process に 1 つ覚えられ、2 つ目の解析はその節を読み直さず、読んだ source にも数えなかった。
    def closure(name: str):
        return _gap_names(check_coverage(analyze_program(f"{pkg}.absent:{name}"), []))

    first = reading_sources(lambda: closure("first"))
    second = reading_sources(lambda: closure("second"))

    assert first.value == second.value == ["Raise"]
    assert "doeff_core_effects._outcomes" in first.read
    assert "doeff_core_effects._outcomes" in second.read


def test_an_analysis_answers_and_reads_the_same_after_other_analyses_in_the_process(make_package) -> None:
    # 盲検 B: handler の読みを process で覚えると、先に走った解析の文脈(読んでいる handler)で読んだ handler が後の解析に使われ、
    # warm(A → B → A と入れ直す)と job の答えと読んだ source が、どちらを先に撃ったかで変わった。新しい package を 2 つ作り、
    # 両方の順で撃って、先に撃った時の答えと後に撃った時の答えを比べる。
    def closure(pkg: str, name: str):
        def compute():
            coverage = check_coverage(analyze_program(f"{pkg}.mutual:{name}"), [])
            return ([(gap.effect.__name__, gap.origin) for gap in coverage.gaps], coverage.unknown_handlers)

        found = reading_sources(compute)
        return found.value, {module for module in found.read if module.startswith(pkg)}

    files = {"effects.py": EFFECTS_PY, "mutual.py": MUTUAL_PY}
    job_first = make_package(files)
    job_alone, warm_after_job = closure(job_first, "job"), closure(job_first, "warm")
    warm_first = make_package(files)
    warm_alone, job_after_warm = closure(warm_first, "warm"), closure(warm_first, "job")

    assert job_alone == (([], ()), {f"{job_first}.mutual"})
    assert job_after_warm == (([], ()), {f"{warm_first}.mutual"})
    assert warm_alone[0] == warm_after_job[0]
