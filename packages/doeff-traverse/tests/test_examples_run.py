"""doeff-traverse の examples/ の例が script として最後まで走り、期待どおりの結果を印字する事の検。

例は `hy examples/<名>.hy` で走らせる読み物で、どの検も走らせていなかったので、doeff-hy の macro が変わっても
誰も気づかなかった: 2026-04-07 の fa37eaaf1 が fold macro を消し、defk の :pre/:post を必須にした後、
ex09_for_do_when.hy は使ってもいない fold の require で落ちていた(card ki-7e6a11f56bd5 の担い手の所見)。

examples/ の `.hy` は全部この検に入る(新しい例も書いただけで入る)。今の macro で走らない例は BROKEN の表に
理由つきで置き、表の例は今も落ちる事を確かめる — 直した例が表に残ると赤になり、行を外せと名指す。

終了コードだけでは足りない: Traverse の handler は件の中の例外を件の失敗として記録して先へ進むので、macro の
展開が件の中で壊れても例は 0 で終わる(設計検証 lt-QV9FJWHA394WE5MH3QVJFQBHV5 の盲検 A — When の展開が引く名を
変えると、ex09 は件を skipped ではなく failed と印字して 0 で終わった)。走る例は標準出力を expected/<stem>.txt と
比べる。走るたびに変わる所(実時間・checkout の path・lib の traceback)は、期待の出力の側で ANY に置き換える —
どこが変わるかは例の持ち分で、検は印の意味だけを知る。印を置いた例ごとに、件の中だけで失敗する変異を例の写しに
当て、写しの出力が期待の出力に合わない事を確かめる(印が件の結果まで伏せていない事の検 — card ki-2d5b129f2ff2)。
件を走らせない debug 用の例だけは出力を比べず、件を走らせない事を確かめる。

macro の持ち主は doeff-hy で、例は doeff-hy の macro だけを使う: 消えた macro を例の中で定義し直すと、例は利用者が
使えない書き方を教え、doeff-hy の変更も届かない(同じ設計検証の盲検 B — fold を例の中の defmacro で戻した ex04 は
走った)。例の中の macro の定義と、doeff-hy の外からの require を断る。
"""

import difflib
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import hy
import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
# 走る例の標準出力の期待(<例の stem>.txt)。例の印字を変えたら、例と一緒に書き直す。
EXPECTED = EXAMPLES / "expected"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1 秒(置き場が冷えていても 約 20 秒)。
CHILD_DEADLINE_SECONDS = 50

# 今の macro で走らない例(file の名 → 理由)。直したら行を外す。
BROKEN: dict[str, str] = {}

# 期待の出力の中で、どの文字列(改行を含む・空も)にも合う印。走るたびに変わる所だけに置く。
ANY = "…"

# 走るが標準出力を比べない例(file の名 → 理由)。件を走らせず(doeff の run を名指さない)、印字が doeff-hy・Hy の
# 隠す中身だけの debug 用の例に限る — 件を走らせない例には、印字にしか出ない件の失敗も無い。
OUTPUT_NOT_COMPARED: dict[str, str] = {
    "ex04_debug.hy": (
        "traverse macro の展開(gensym の名を含む — 番号は compile の状態で変わる)を印字する debug 用 — "
        "印字は doeff-hy の展開の中身で、例の結果ではない"
    ),
    "ex04_debug2.hy": (
        "関数の repr(番地と Hy の無名関数の番号 — 番号は defk の展開で変わる)を印字する debug 用 — "
        "印字は Hy・doeff-hy の中身で、例の結果ではない"
    ),
}

# 期待の出力に ANY を置いた例の、件の中だけで失敗する変異(file の名 → 例の写しで置き換える元の文と先の文)。
# 写しは終了コード 0 のまま件を失敗として印字する(盲検 A の形)ので、出力の比べ方だけが捕まえられる。
CASE_FAILURE_MUTANTS: dict[str, tuple[str, str]] = {
    # sync-backend が x = 3 の件で ZeroDivisionError — 合計が 550 でなくなる
    "ex05_parallel_timing.hy": ("(resume (* x 10))", "(resume (// 10 (- x 3)))"),
    # mock が MSFT の価格を知らない — FetchPrice の件が KeyError で失敗し、成功が 3 から 2 へ
    "ex07_proboscis_ema_rewrite.hy": ('"MSFT" 300.0', '"MSFX" 300.0'),
}

# 件を走らせる口(doeff の run・async_run)。
_PROGRAM_RUNNERS = frozenset({"run", "async_run"})

# 例の中で使わない form(macro を定義する)と、例が require してよい package(macro の持ち主)。
_MACRO_DEFINERS = frozenset({"defmacro", "defmacro!", "defreader"})
_MACRO_OWNER = "doeff_hy"


def _examples() -> list[str]:
    return sorted(path.name for path in EXAMPLES.glob("*.hy"))


def _runnable() -> list[str]:
    return [name for name in _examples() if name not in BROKEN]


def _run_hy(path: Path, deadline_scale_factor: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hy", str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _expected(name: str) -> Path:
    return EXPECTED / f"{Path(name).stem}.txt"


def _matches(expected: str, actual: str) -> bool:
    """actual が expected と同じか(expected の ANY は、どの文字列にも合う)。"""
    pattern = ".*?".join(re.escape(part) for part in expected.split(ANY))
    return re.fullmatch(pattern, actual, flags=re.DOTALL) is not None


def _output_diff(expected: Path, actual: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.read_text(encoding="utf-8").splitlines(),
            actual.splitlines(),
            str(expected.relative_to(EXAMPLES)),
            "標準出力",
            lineterm="",
        )
    )


def _subforms(form: object) -> Iterator[object]:
    yield form
    if isinstance(form, hy.models.Sequence):
        for child in form:
            yield from _subforms(child)


def _module_package(arg: object) -> str | None:
    """require の module の語の頭の package(module の語でなければ None)。Hy 1.x の reader は `a.b` を `(. a b)` と読む。"""
    if isinstance(arg, hy.models.Symbol):
        return hy.mangle(str(arg))
    if isinstance(arg, hy.models.Expression) and len(arg) > 1 and arg[0] == hy.models.Symbol("."):
        return _module_package(arg[1])
    return None


def _required_packages(form: hy.models.Expression) -> list[str]:
    """`(require <module> [名 …] <module> :as <別名> …)` の module の頭の package(keyword の次の語は別名)。"""
    packages: list[str] = []
    after_keyword = False
    for arg in list(form)[1:]:
        package = None if after_keyword else _module_package(arg)
        if package is not None:
            packages.append(package)
        after_keyword = isinstance(arg, hy.models.Keyword)
    return packages


def _forms(source: str, filename: str) -> Iterator[object]:
    """source の form を全部(入れ子の form も)。"""
    for top in hy.read_many(source, filename=filename):
        yield from _subforms(top)


def _macro_violations(source: str, filename: str) -> list[str]:
    violations: list[str] = []
    for form in _forms(source, filename):
        if not (
            isinstance(form, hy.models.Expression)
            and form
            and isinstance(form[0], hy.models.Symbol)
        ):
            continue
        head = str(form[0])
        if head in _MACRO_DEFINERS:
            violations.append(f"{head} {form[1] if len(form) > 1 else ''}".strip())
        elif head == "require":
            violations.extend(
                f"require {package}"
                for package in _required_packages(form)
                if package != _MACRO_OWNER
            )
    return violations


def _program_runners(source: str, filename: str) -> list[str]:
    """source が名指す、件を走らせる口(入れ子の form の中も・import の名も)。"""
    return sorted(
        {
            hy.mangle(str(form))
            for form in _forms(source, filename)
            if isinstance(form, hy.models.Symbol) and hy.mangle(str(form)) in _PROGRAM_RUNNERS
        }
    )


@pytest.mark.parametrize("name", _runnable())
def test_example_runs(name: str, deadline_scale_factor: float) -> None:
    result = _run_hy(EXAMPLES / name, deadline_scale_factor)
    assert result.returncode == 0, result.stdout + result.stderr
    if name in OUTPUT_NOT_COMPARED:
        return
    expected = _expected(name)
    assert expected.is_file(), (
        f"{name} の期待の出力 {expected.relative_to(EXAMPLES)} が無い — 例を走らせた標準出力を置き、"
        f"走るたびに変わる所を {ANY} にする"
    )
    assert _matches(expected.read_text(encoding="utf-8"), result.stdout), _output_diff(
        expected, result.stdout
    )


@pytest.mark.parametrize("name", sorted(CASE_FAILURE_MUTANTS))
def test_case_failure_shows_in_compared_output(
    name: str, deadline_scale_factor: float, tmp_path: Path
) -> None:
    """ANY を置いた期待の出力が、件の結果まで伏せていない事: 件の中で失敗する写しの出力は合わない。"""
    source = (EXAMPLES / name).read_text(encoding="utf-8")
    original, mutated = CASE_FAILURE_MUTANTS[name]
    assert source.count(original) == 1, (
        f"変異の元の文 {original!r} が {name} に 1 つだけ在るのではない — 変異を例に合わせて書き直す"
    )
    mutant = tmp_path / name
    mutant.write_text(source.replace(original, mutated), encoding="utf-8")
    result = _run_hy(mutant, deadline_scale_factor)
    assert result.returncode == 0, (
        f"変異が件の外で例を落とした — 件の中だけで失敗する変異を選ぶ\n{result.stdout}{result.stderr}"
    )
    assert not _matches(_expected(name).read_text(encoding="utf-8"), result.stdout), (
        f"件の中で失敗した写しの出力が {_expected(name).relative_to(EXAMPLES)} に合う — "
        f"{ANY} が件の結果まで伏せている\n{result.stdout}"
    )


@pytest.mark.parametrize("name", sorted(OUTPUT_NOT_COMPARED))
def test_not_compared_example_runs_no_program(name: str) -> None:
    path = EXAMPLES / name
    assert _program_runners(path.read_text(encoding="utf-8"), str(path)) == [], (
        f"{name} は件を走らせる — OUTPUT_NOT_COMPARED から外し、期待の出力を置いて比べる"
        f"(走るたびに変わる所は {ANY} にする)"
    )


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_example_is_still_broken(name: str, deadline_scale_factor: float) -> None:
    assert (EXAMPLES / name).is_file(), f"BROKEN の行 {name} の例が examples/ に無い — 行を外す"
    result = _run_hy(EXAMPLES / name, deadline_scale_factor)
    assert result.returncode != 0, (
        f"{name} は走るようになった — BROKEN の行(理由: {BROKEN[name]})を外す"
    )


@pytest.mark.parametrize("name", _examples())
def test_example_uses_only_doeff_hy_macros(name: str) -> None:
    path = EXAMPLES / name
    assert _macro_violations(path.read_text(encoding="utf-8"), str(path)) == [], (
        f"{name} が macro を定義するか doeff-hy の外から require している — macro は doeff-hy に置く"
    )


def test_macro_check_sees_nested_forms() -> None:
    """今の例には違反が無いので、確かめが form の中へ降りる事は違反を含む source で確かめる
    (降りない確かめは、例の中の defmacro を見逃して緑のまま — 設計検証 lt-QV9FJWHA394WE5MH3QVJFQBHV5 の c6a)。"""
    source = """
(require doeff-hy.macros [defk] support.fold-shim [fold] bare [x] doeff_hy.handle :as h)
(when True
  (defmacro fold [coll] coll))
"""
    assert _macro_violations(source, "<inline>") == [
        "require support",
        "require bare",
        "defmacro fold",
    ]


def test_runner_check_sees_nested_forms() -> None:
    """比べない例は今どれも件を走らせないので、件を走らせる口を拾う事は、口を含む source で確かめる。"""
    source = """
(import doeff [run :as r])
(when True
  (print (doeff.run (prog)))
  (async-run (prog)))
"""
    assert _program_runners(source, "<inline>") == ["async_run", "run"]


def test_tables_name_examples_as_they_are() -> None:
    """期待の出力は比べる例ちょうどに在り、比べない表と変異の表は今の例を名指す(消した・名を変えた例の行を残さない)。"""
    compared = [name for name in _runnable() if name not in OUTPUT_NOT_COMPARED]
    present = {path.name for path in EXPECTED.glob("*.txt")}
    assert present - {_expected(name).name for name in compared} == set(), (
        "比べる例の無い期待の出力が在る — 消すか、例を BROKEN から外す"
    )
    assert set(OUTPUT_NOT_COMPARED) - set(_runnable()) == set(), (
        "OUTPUT_NOT_COMPARED の行が走る例を名指さない"
    )
    with_any = {
        name
        for name in compared
        if _expected(name).is_file() and ANY in _expected(name).read_text(encoding="utf-8")
    }
    assert with_any - set(CASE_FAILURE_MUTANTS) == set(), (
        f"期待の出力に {ANY} を置いた例に、件の中で失敗する変異が無い — CASE_FAILURE_MUTANTS に行を足す"
    )
    assert set(CASE_FAILURE_MUTANTS) - with_any == set(), (
        f"CASE_FAILURE_MUTANTS の行が、期待の出力に {ANY} を置いた例を名指さない — 行を外す"
    )
    # checkout の path を含む期待の出力は、別の木(日次の作業木)では必ず外れる — 置いた木で気づくように断る。
    checkout = str(EXAMPLES.parents[2])
    with_path = sorted(
        path.name for path in EXPECTED.glob("*.txt") if checkout in path.read_text(encoding="utf-8")
    )
    assert with_path == [], f"期待の出力に checkout の path が在る — path を含む所を {ANY} にする"
