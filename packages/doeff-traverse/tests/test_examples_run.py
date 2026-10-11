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
どこが変わるかは例の持ち分で、検は印の意味だけを知る。印を置いた例ごとに、件の中だけで失敗する変異を examples/
の写しに当て、写しの出力が期待の出力に合わない事を確かめる(印が件の結果まで伏せていない事の検 — card
ki-2d5b129f2ff2)。Hy の付ける名(番号は macro の展開で変わる)は期待の出力に置かない。走る例は全部比べる —
件を走らせず、macro の展開や関数の番地など doeff-hy・Hy の中身だけを印字する debug 用の file は例ではなく、
examples/ に置かない(card ki-3497c5fd38df が ex04_debug・ex04_debug2 と、それを比べずに通す表を外した)。
例が doeff の件を走らせる口(doeff から import した run・async_run か doeff.run)を呼ぶ事を確かめる(設計検証
lt-C53PGBQD7WWPYAA1WWSW41BHDP)。

macro の持ち主は doeff-hy で、例は doeff-hy の macro だけを使う: 消えた macro を例の中で定義し直すと、例は利用者が
使えない書き方を教え、doeff-hy の変更も届かない(同じ設計検証の盲検 B — fold を例の中の defmacro で戻した ex04 は
走った)。例の中の macro の定義と、doeff-hy の外からの require を断る。展開の形も doeff-hy の隠す中身で、例が
印字すると答えを変えない macro の変更で例が赤になるので、展開の引き出し(hy.macroexpand)も断る。
"""

import difflib
import itertools
import re
import shutil
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

# Hy が compile の時に付ける名(無名関数・gensym)。番号は macro の展開と compile の順で決まる Hy・doeff-hy の
# 隠す中身で、例の結果ではない — 期待の出力に置くと、答えを変えない macro の変更で例が赤になる
# (設計検証 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF の盲検 B)。印字に出るなら、その所を ANY にする。
_HY_GENERATED_NAME = re.compile(r"_hy_(?:anon|gensym)_")

# 期待の出力に ANY を置いた例の、件の中だけで失敗する変異(file の名 → 例の写しで置き換える元の文と先の文)。
# 写しは終了コード 0 のまま件を失敗として印字する(盲検 A の形)ので、出力の比べ方だけが捕まえられる。
CASE_FAILURE_MUTANTS: dict[str, tuple[str, str]] = {
    # sync-backend が x = 3 の件で ZeroDivisionError — 合計が 550 でなくなる
    "ex05_parallel_timing.hy": ("(resume (* x 10))", "(resume (// 10 (- x 3)))"),
    # mock が MSFT の価格を知らない — FetchPrice の件が KeyError で失敗し、成功が 3 から 2 へ
    "ex07_proboscis_ema_rewrite.hy": ('"MSFT" 300.0', '"MSFX" 300.0'),
}

# 件を走らせる口(doeff の run・async_run)。例はどれも、doeff から import した口か `doeff.run` を呼ぶ — 名だけで
# 数えると asyncio.run・subprocess.run・例の中で定義した run も口に見える(設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP の盲検 B)。
_RUNNER_MODULE = "doeff"
_PROGRAM_RUNNERS = frozenset({"run", "async_run"})
# 中身を呼ばずに data として持つ form。`'(run x)` は呼び出しではない。
_QUOTES = frozenset({"quote", "quasiquote"})

# 例の中で使わない form(macro を定義する)と、例が require してよい package(macro の持ち主)。
_MACRO_DEFINERS = frozenset({"defmacro", "defmacro!", "defreader"})
_MACRO_OWNER = "doeff_hy"
# 例の中で呼ばない、macro の展開を引き出す関数。展開の形は doeff-hy の隠す中身で、例が印字すると、答えを変えない
# macro の変更で例が赤になる(同じ設計検証の盲検 A・B — Program を走らせる例に展開の印字を足すと検を通った)。
_MACRO_EXPANDERS = frozenset({"macroexpand", "macroexpand_1", "macroexpand_all"})


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


def _callee(head: object) -> str | None:
    """呼び出しの頭の関数の名(`a.b` は reader が `(. a b)` と読む — その最後の名)。"""
    if isinstance(head, hy.models.Symbol):
        return hy.mangle(str(head))
    if (
        isinstance(head, hy.models.Expression)
        and len(head) > 2
        and head[0] == hy.models.Symbol(".")
    ):
        return _callee(head[-1])
    return None


def _macro_violations(source: str, filename: str) -> list[str]:
    return [
        violation for form in _forms(source, filename) for violation in _form_macro_violations(form)
    ]


def _form_macro_violations(form: object) -> list[str]:
    """form 1 つの違反: macro の展開の引き出し・macro の定義・doeff-hy の外からの require。"""
    if not (isinstance(form, hy.models.Expression) and form):
        return []
    callee = _callee(form[0])
    expanded = [f"{callee} の呼び出し"] if callee in _MACRO_EXPANDERS else []
    head = str(form[0]) if isinstance(form[0], hy.models.Symbol) else ""
    if head in _MACRO_DEFINERS:
        return [*expanded, f"{head} {form[1] if len(form) > 1 else ''}".strip()]
    if head == "require":
        return [
            *expanded,
            *(
                f"require {package}"
                for package in _required_packages(form)
                if package != _MACRO_OWNER
            ),
        ]
    return expanded


def _imported_runners(form: object) -> set[str]:
    """`(import doeff [run async-run :as arun] …)` が module に置く、件を走らせる口の名(別名ならその名)。"""
    if not (
        isinstance(form, hy.models.Expression) and form and form[0] == hy.models.Symbol("import")
    ):
        return set()
    args = list(form)[1:]
    return {
        name
        for module, listed in itertools.pairwise(args)
        if isinstance(module, hy.models.Symbol)
        and hy.mangle(str(module)) == _RUNNER_MODULE
        and isinstance(listed, hy.models.List)
        for name in _listed_runners(list(listed))
    }


def _listed_runners(items: list[object]) -> set[str]:
    """import の名の列 `[run async-run :as arun …]` のうち、件を走らせる口が module に置かれる名(別名の語は名ではない)。"""
    return {
        hy.mangle(str(items[index + 2]))
        if index + 2 < len(items) and items[index + 1] == hy.models.Keyword("as")
        else hy.mangle(str(item))
        for index, item in enumerate(items)
        if not (index > 0 and isinstance(items[index - 1], hy.models.Keyword))
        and isinstance(item, hy.models.Symbol)
        and hy.mangle(str(item)) in _PROGRAM_RUNNERS
    }


def _runner_call(head: object, runners: set[str]) -> str | None:
    """呼び出しの頭が件を走らせる口なら、その名(doeff から import した名か、`doeff.run` = `(. doeff run)`)。"""
    if isinstance(head, hy.models.Symbol):
        name = hy.mangle(str(head))
        return name if name in runners else None
    if (
        isinstance(head, hy.models.Expression)
        and len(head) == 3
        and head[0] == hy.models.Symbol(".")
        and head[1] == hy.models.Symbol(_RUNNER_MODULE)
        and isinstance(head[2], hy.models.Symbol)
        and hy.mangle(str(head[2])) in _PROGRAM_RUNNERS
    ):
        return f"{_RUNNER_MODULE}.{hy.mangle(str(head[2]))}"
    return None


def _unquoted_subforms(form: object) -> Iterator[object]:
    """form とその入れ子の form(quote・quasiquote の中には降りない)。"""
    if (
        isinstance(form, hy.models.Expression)
        and form
        and isinstance(form[0], hy.models.Symbol)
        and str(form[0]) in _QUOTES
    ):
        return
    yield form
    if isinstance(form, hy.models.Sequence):
        for child in form:
            yield from _unquoted_subforms(child)


def _program_calls(source: str, filename: str) -> list[str]:
    """source が呼ぶ、doeff の件を走らせる口(入れ子の form の中も)。import するだけ・名を書くだけ・quote の中・
    doeff の外の同じ名(asyncio.run・例の中で定義した run)は数えない。"""
    forms = [
        form for top in hy.read_many(source, filename=filename) for form in _unquoted_subforms(top)
    ]
    runners = set().union(*(_imported_runners(form) for form in forms))
    return sorted(
        {
            name
            for form in forms
            if isinstance(form, hy.models.Expression)
            and form
            and (name := _runner_call(form[0], runners)) is not None
        }
    )


@pytest.mark.parametrize("name", _examples())
def test_example_runs_a_program(name: str) -> None:
    path = EXAMPLES / name
    assert _program_calls(path.read_text(encoding="utf-8"), str(path)) != [], (
        f"{name} は doeff の件を走らせる口({'・'.join(sorted(_PROGRAM_RUNNERS))} — doeff から import した名か "
        f"{_RUNNER_MODULE}.run)を呼ばない — examples/ は Program を走らせて結果を印字する読み物の置き場で、"
        "macro の展開や関数の repr を印字するだけの debug 用の file は置かない"
    )


@pytest.mark.parametrize("name", _runnable())
def test_example_runs(name: str, deadline_scale_factor: float) -> None:
    result = _run_hy(EXAMPLES / name, deadline_scale_factor)
    assert result.returncode == 0, result.stdout + result.stderr
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
    """ANY を置いた期待の出力が、件の結果まで伏せていない事: 件の中で失敗する写しの出力は合わない。

    写しは examples/ の dir ごと複製して作る — 例は兄弟の file(共有の helper・data)と並んだ場所で走る物で、
    1 file だけを別の dir へ移すと、変異と関わりなく import で落ちる(設計検証 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF の盲検 A)。"""
    source = (EXAMPLES / name).read_text(encoding="utf-8")
    original, mutated = CASE_FAILURE_MUTANTS[name]
    assert source.count(original) == 1, (
        f"変異の元の文 {original!r} が {name} に 1 つだけ在るのではない — 変異を例に合わせて書き直す"
    )
    copy = tmp_path / EXAMPLES.name
    shutil.copytree(EXAMPLES, copy)
    mutant = copy / name
    mutant.write_text(source.replace(original, mutated), encoding="utf-8")
    result = _run_hy(mutant, deadline_scale_factor)
    assert result.returncode == 0, (
        f"変異を当てた写しが 0 以外で終わった — 変異が件の外で例を落とした(件の中だけで失敗する変異を選ぶ)\n"
        f"{result.stdout}{result.stderr}"
    )
    assert not _matches(_expected(name).read_text(encoding="utf-8"), result.stdout), (
        f"件の中で失敗した写しの出力が {_expected(name).relative_to(EXAMPLES)} に合う — "
        f"{ANY} が件の結果まで伏せている\n{result.stdout}"
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
    violations = _macro_violations(path.read_text(encoding="utf-8"), str(path))
    assert violations == [], (
        f"{name} が macro を定義するか、doeff-hy の外から require するか、展開を引き出している({violations}) — "
        "macro は doeff-hy に置き、展開の形は doeff-hy の隠す中身なので例に印字しない"
    )


def test_macro_check_sees_nested_forms() -> None:
    """今の例には違反が無いので、確かめが form の中へ降りる事は違反を含む source で確かめる
    (降りない確かめは、例の中の defmacro を見逃して緑のまま — 設計検証 lt-QV9FJWHA394WE5MH3QVJFQBHV5 の c6a)。"""
    source = """
(require doeff-hy.macros [defk] support.fold-shim [fold] bare [x] doeff_hy.handle :as h)
(when True
  (defmacro fold [coll] coll)
  (print (hy.repr (hy.macroexpand '(traverse (<- v (Iterate xs)) v))))
  (print (macroexpand-1 '(fold xs))))
"""
    assert _macro_violations(source, "<inline>") == [
        "require support",
        "require bare",
        "defmacro fold",
        "macroexpand の呼び出し",
        "macroexpand_1 の呼び出し",
    ]


def test_runner_call_check_sees_nested_forms() -> None:
    """今の例はどれも口を呼ぶので、拾う側と拾わない側は inline の source で確かめる: doeff から import した口の
    入れ子の呼び出し(別名も)と `doeff.run` の形は拾い、import するだけ・呼ばずに名を書くだけ・quote の中・
    doeff の外の同じ名(asyncio.run・subprocess.run・例の中で定義した run)は拾わない(設計検証
    lt-C53PGBQD7WWPYAA1WWSW41BHDP の盲検 B)。"""
    calls = """
(import doeff [do :as _doeff-do run async-run :as arun])
(when True
  (print (run (prog)) (doeff.run (prog))))
(defn go [] (arun (prog)))
"""
    assert _program_calls(calls, "<inline>") == ["arun", "doeff.run", "run"]
    no_calls = """
(import doeff [run async-run])
(print run (hy.repr '(run x)) (hy.repr `(async-run ~x)))
"""
    assert _program_calls(no_calls, "<inline>") == []
    other_runs = """
(import asyncio subprocess doeff [pure :as run])
(asyncio.run (main))
(subprocess.run ["hy2py" "ex04_simple.hy"])
(run 1)
"""
    assert _program_calls(other_runs, "<inline>") == []
    local_run = """
(defn run [] (print "local"))
(run)
"""
    assert _program_calls(local_run, "<inline>") == []


def test_tables_name_examples_as_they_are() -> None:
    """期待の出力は走る例ちょうどに在り、変異の表は今の例を名指す(消した・名を変えた例の行を残さない)。"""
    compared = _runnable()
    present = {path.name for path in EXPECTED.glob("*.txt")}
    assert present - {_expected(name).name for name in compared} == set(), (
        "比べる例の無い期待の出力が在る — 消すか、例を BROKEN から外す"
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
    with_hy_names = sorted(
        path.name
        for path in EXPECTED.glob("*.txt")
        if _HY_GENERATED_NAME.search(path.read_text(encoding="utf-8"))
    )
    assert with_hy_names == [], (
        f"期待の出力に Hy の付ける名(_hy_anon_・_hy_gensym_)が在る — 番号は macro の展開で変わる。"
        f"その所を {ANY} にする"
    )


def test_hy_name_check_sees_generated_names() -> None:
    """今の期待の出力には Hy の付ける名が無いので、拾う事は名を含む文で確かめる。"""
    assert _HY_GENERATED_NAME.search("raised in: _hy_anon_17:129 -> fetch_price:95")
    assert _HY_GENERATED_NAME.search("(setx _hy_gensym_bound_2 x)")
    assert not _HY_GENERATED_NAME.search("raised in: …:129 -> fetch_price:95")
