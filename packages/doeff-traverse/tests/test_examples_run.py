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

macro の持ち主は doeff-hy で、例は doeff-hy の macro だけを使う: 消えた macro を例の中で定義し直すと、例は利用者が
使えない書き方を教え、doeff-hy の変更も届かない(同じ設計検証の盲検 B — fold を例の中の defmacro で戻した ex04 は
走った)。例の中の macro の定義と、doeff-hy の外からの require を断る。
"""

import difflib
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
