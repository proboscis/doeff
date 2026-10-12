"""doeff-traverse の examples/ の例が script として最後まで走り、期待どおりの結果を印字する事の検。

例は `hy examples/<名>.hy` で走らせる読み物で、どの検も走らせていなかったので、doeff-hy の macro が変わっても
誰も気づかなかった: 2026-04-07 の fa37eaaf1 が fold macro を消し、defk の :pre/:post を必須にした後、
ex09_for_do_when.hy は使ってもいない fold の require で落ちていた(card ki-7e6a11f56bd5 の担い手の所見)。

examples/ の `.hy` は全部この検に入る(新しい例も書いただけで入る)。今の macro で走らない例は BROKEN の表に
理由つきで置き、表の例は今も落ちる事を確かめる — 直した例が表に残ると赤になり、行を外せと名指す。

終了コードだけでは足りない: Traverse の handler は件の中の例外を件の失敗として記録して先へ進むので、macro の
展開が件の中で壊れても例は 0 で終わる(設計検証 lt-QV9FJWHA394WE5MH3QVJFQBHV5 の盲検 A — When の展開が引く名を
変えると、ex09 は件を skipped ではなく failed と印字して 0 で終わった)。走る例は標準出力を expected/<stem>.txt と
比べる。走るたびに変わる所(実時間・lib の traceback)は、期待の出力の側で印に置き換える — どこが変わるかは例の
持ち分で、検は印の意味だけを知る。印は種類ごとに呑める物が決まっていて(行だけの ANY は traceback の frame の行・行の
中の印は MARKS の形の文字列)、件の結果の行を呑む印は赤になる(card ki-f8a58fb49dd9)。書いた木の上では毎回同じに出て、
別の木・別の機体や答えを変えない変更で外れる物(checkout や機体の path・Hy の付ける名・frame の行)は、期待の出力に
置かない(UNPLACEABLE)。機体の時間帯は子の側で決めた値にする(CHILD_TIME_ZONE)ので、地方時の印字は機体に依らない。

件の中の失敗が比べる出力に出る事は、例ごとに、Traverse の走りごとに最初の件を件の中で失敗させる子
(run_example_failing_a_case.py)で確かめる — その子の出力は期待の出力に合わない(card ki-2d5b129f2ff2)。失敗は例の
source を書き換えずに起こすので、出力を変えない例の中の書き換えで検の file を直す事は無い。同じ子が実行の印 — 件の関数を
呼んだ数と doeff の run で Program を走らせた数 — を数える: 件を走らせず、macro の展開や関数の番地など doeff-hy・Hy の
中身だけを印字する debug 用の file は例ではなく、examples/ に置かない(card ki-3497c5fd38df が ex04_debug・ex04_debug2 を
外した)。例は Program を走らせ、件を 1 つも走らせない例は NO_CASES に理由つきで置く。走った数で確かめるので、名だけ
在って走らない run(呼ばない関数の中・quote の中)は数えない(設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP の名で数える検を、
card ki-f8a58fb49dd9 が実行の印に替えた)。

macro の持ち主は doeff-hy で、例は doeff-hy の macro だけを使う: 消えた macro を例の中で定義し直すと、例は利用者が
使えない書き方を教え、doeff-hy の変更も届かない(同じ設計検証の盲検 B — fold を例の中の defmacro で戻した ex04 は
走った)。例の中の macro の定義と、doeff-hy の外からの require を断る。展開の形も doeff-hy の隠す中身で、例が
印字すると答えを変えない macro の変更で例が赤になるので、展開の引き出し(hy.macroexpand)も断る。引き出さずに展開を
文字列で写して印字する例は、macro が変わっても赤にならずに古い展開を教える — 写しは展開の形の印(Hy・doeff-hy の隠す名と、
展開が module を引く __import__)を持つので、期待の出力に置かせない。例が使う doeff-hy の macro の展開がどれもその印を持つ
事も、展開して確かめる(card ki-fe7a2e60fdfb)。
"""

import difflib
import functools
import itertools
import re
import subprocess
import sys
import types
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import hy
import pytest
from doeff_hy.static_stub import hidden_name

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
# 走る例の標準出力の期待(<例の stem>.txt)。例の印字を変えたら、例と一緒に書き直す。
EXPECTED = EXAMPLES / "expected"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1 秒(置き場が冷えていても 約 20 秒)。
CHILD_DEADLINE_SECONDS = 50
# 子の時間帯(POSIX の TZ の綴り — 時間帯の data base が無い機体でも読める)。例が地方時を印字しても、期待の出力が機体の
# 時間帯に依らない(設計検証 lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD の盲検 B — datetime.fromtimestamp の印字は TZ=JST-9 の機体で外れた)。
CHILD_TIME_ZONE = "UTC0"
# 子(例・件の中で失敗させる例)を同時に走らせる数。子 1 本は 約 1 秒で、多くは hy と doeff の import(例の中身は 0.2〜0.3 秒・ex05 は
# sleep 2.5 秒)。1 本ずつ順に走らせると子 12 本の秒が足し算になり、この file 1 本が負荷の下で 25〜42 秒かかって、変えた所の検
# (make test-changed)の 60 秒の上限で後ろの検を打ち切った(card ki-458fc8e0de36)。全部を一度に起こさないのは、機体の検の枠
# 1 つ(root の conftest の _hold_check_slot)の目安 — 検 1 本 0.5〜2.5 core — を大きく越えないため。
PARALLEL_CHILDREN = 4
# 例を件の中で失敗させて走らせる子の入口(Traverse の走りごとに最初の件を失敗させ、実行の印を標準エラーに書く)。
FAILING_A_CASE = Path(__file__).with_name("run_example_failing_a_case.py")
# 件の中で失敗させる子が標準エラーに書く、実行の印の行(子の COUNTS_LINE)。
_COUNTS = re.compile(r"^case-calls: (\d+) program-runs: (\d+)$", re.MULTILINE)

# 今の macro で走らない例(file の名 → 理由)。直したら行を外す。
BROKEN: dict[str, str] = {}

# Traverse の件を 1 つも走らせない例(file の名 → 理由)。件の中の失敗を起こせないので、出力の比べ方だけで確かめる。
NO_CASES: dict[str, str] = {
    "ex01_basic_fail.hy": "Fail の効果を Traverse の外で見せる例 — 失敗は 3 つの戦略の結果として出力に出る",
}

# 期待の出力の印の字。印の種類は置く所で決まり、種類ごとに呑める物が決まっている:
#   - 行が ANY だけの行は、traceback の frame の行を 1 行以上呑む(_frame_violations) — 件の結果の行は呑めない。
#   - 行の中の印は MARKS のどれかの形で置き、その形の文字列だけを呑む(行を越えない)。
# どの文字列にも合う印は、件の結果を載せる行まで伏せられ、その行を変える変異が無いと気づけなかった(card ki-f8a58fb49dd9)。
ANY = "…"
# 行の中の印(綴り → 呑む文字列)。走るたびに変わる物の種類を増やす時は、ここに行を足す。
MARKS: dict[str, str] = {
    # 実時間の秒(ex05 の「550 in …s」)
    f"{ANY}s": r"\d+(?:\.\d+)?s",
}
_MARK = re.compile(
    "(" + "|".join(re.escape(mark) for mark in sorted(MARKS, key=len, reverse=True)) + ")"
)

# traceback の frame の頭の行と、frame の中の印の行(位置の印 ^~・Python 3.13 からの略した source の行・繰り返しの略)。
_FRAME_HEAD = re.compile(r'\s*File "(?P<path>[^"]+)", line \d+, in \S+')
_FRAME_MARKER = re.compile(
    r"\s*(?:[\^~]+|\.\.\.<\d+ lines>\.\.\.|\[Previous line repeated \d+ more times?\])"
)

# 機体と利用者で変わる置き場(venv・Python・home)。
_MACHINE_PATHS = sorted(
    {path.rstrip("/") for path in (sys.prefix, sys.base_prefix, str(Path.home()))} - {""}
)

# 文の中の、_ で始まる語(Hy の綴りの - も語に含める — 名は mangle して比べる)。
_UNDERSCORED_WORD = re.compile(r"(?<![\w-])_[\w-]*")
# 展開が名を束ねずに module を引く呼び出し(doeff-hy の macros.hy の _do-reference・outcome_forms.hy の open-form — fnk の
# 展開は隠す名を持たず、これだけを持つ)。
_EXPANSION_IMPORT = "__import__"


def _shows_expansion(text: str) -> bool:
    """文が展開の形の印を持つか: Hy・doeff-hy の隠す名(持ち主は doeff-hy の hidden_name — Hy の gensym・macro の補助と記帳)
    か、展開が module を引く __import__。"""
    return any(
        word == _EXPANSION_IMPORT or hidden_name(hy.mangle(word))
        for word in _UNDERSCORED_WORD.findall(text)
    )


def _searches(pattern: str, flags: int = 0) -> Callable[[str], bool]:
    """正規表現が文のどこかに合うか、を問う関数。"""
    compiled = re.compile(pattern, flags)
    return lambda text: compiled.search(text) is not None


# 期待の出力に置かない物(名 → 文がそれを持つか)。どれも書いた木の上では毎回同じに出るので緑のまま置けてしまい、別の木・
# 別の機体や、答えを変えない macro・lib の変更で外れる — 置いた木で気づくように断る。印字に出るなら、その所を印にする。
UNPLACEABLE: dict[str, Callable[[str], bool]] = {
    # 日次の作業木など、別の checkout では必ず外れる
    "checkout の path": _searches(re.escape(str(EXAMPLES.parents[2]))),
    "機体の置き場(venv・Python・home)": _searches(
        "|".join(re.escape(f"{path}/") for path in _MACHINE_PATHS)
    ),
    # Hy の付ける名の番号は macro の展開と compile の順で決まり、例の結果ではない(設計検証 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF の
    # 盲検 B)。展開を文字列で写した印字は、答えを変えない macro の変更でも外れず、古い展開を教える(設計検証
    # lt-C53PGBQD7WWPYAA1WWSW41BHDP の残る限界・card ki-fe7a2e60fdfb)
    "展開の形の印(Hy・doeff-hy の隠す名・展開の __import__)": _shows_expansion,
    # path と、lib・例の行番号が入る — frame は行だけの ANY にする
    "traceback の frame の行": _searches(r'^\s*File "[^"]*", line \d+', re.MULTILINE),
}


def _unplaceable_in(text: str) -> list[str]:
    """文が持つ、期待の出力に置かない物の名。"""
    return [name for name, finds in UNPLACEABLE.items() if finds(text)]


# 例の中で使わない form(macro を定義する)と、例が require してよい package(macro の持ち主)。
_MACRO_DEFINERS = frozenset({"defmacro", "defmacro!", "defreader"})
_MACRO_OWNER = "doeff_hy"
# 例の中で呼ばない、macro の展開を引き出す関数。展開の形は doeff-hy の隠す中身で、例が印字すると、答えを変えない
# macro の変更で例が赤になる(設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP の盲検 A・B — Program を走らせる例に展開の印字を
# 足すと検を通った)。
_MACRO_EXPANDERS = frozenset({"macroexpand", "macroexpand_1", "macroexpand_all"})


@dataclass(frozen=True)
class RunMarks:
    """件の中で失敗させる子が数えた実行の印。"""

    # Traverse の件の関数を呼んだ数
    case_calls: int
    # doeff の run で Program を走らせた数
    program_runs: int


def _run_marks(result: subprocess.CompletedProcess[str]) -> RunMarks | None:
    """子の標準エラーの実行の印(書かずに終わったら None)。"""
    found = _COUNTS.search(result.stderr)
    return None if found is None else RunMarks(int(found[1]), int(found[2]))


def _examples() -> list[str]:
    return sorted(path.name for path in EXAMPLES.glob("*.hy"))


def _runnable() -> list[str]:
    return [name for name in _examples() if name not in BROKEN]


@dataclass(frozen=True)
class Child:
    """検が待つ子 1 本 — 例 1 本を、そのままか件の中で失敗させて走らせる。"""

    # 例の file の名
    name: str
    # 件の中で失敗させる子の入口(FAILING_A_CASE)から走らせるか
    failing_a_case: bool


def _in_child_time_zone(command: list[str]) -> list[str]:
    """子の命令 — 親の環境のまま、時間帯だけを決めた値にして走らせる(期待の出力が機体の時間帯に依らないため)。"""
    return ["env", f"TZ={CHILD_TIME_ZONE}", *command]


def _run_child(child: Child, deadline_scale_factor: float) -> subprocess.CompletedProcess[str]:
    """例を `hy <例>` で走らせる(failing_a_case なら、件の中で失敗させる子の入口から)。"""
    example = str(EXAMPLES / child.name)
    command = (
        [sys.executable, str(FAILING_A_CASE), example]
        if child.failing_a_case
        else [sys.executable, "-m", "hy", example]
    )
    return subprocess.run(
        _in_child_time_zone(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _child_of(item: pytest.Item) -> Child | None:
    """検 1 本が待つ子。子を待たない検は None(BROKEN が空の時に pytest が置く空の parametrize の item も、name に例の名を
    持たないので None)。"""
    callspec = getattr(item, "callspec", None)
    name = callspec.params.get("name") if callspec is not None else None
    if not isinstance(name, str):
        return None
    function = getattr(item, "function", None)
    if function in (test_example_runs, test_broken_example_is_still_broken):
        return Child(name, failing_a_case=False)
    if function in (test_case_failure_shows_in_compared_output, test_example_runs_a_program):
        return Child(name, failing_a_case=True)
    return None


class Children:
    """選ばれた検が待つ子を、最初の検の時に全部起こして PARALLEL_CHILDREN 本ずつ並べて走らせ、検はそれぞれ自分の子の
    結末を待つ。起こすのは、この session で選ばれた検の子だけ(-k で 1 本を選べば、子も 1 本)。"""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=PARALLEL_CHILDREN)
        self._mut_runs: dict[Child, Future[subprocess.CompletedProcess[str]]] | None = None

    def start(self, items: list[pytest.Item], deadline_scale_factor: float) -> None:
        """file の無い例(消えた BROKEN の例)の子は起こさない — その検は子を待つ前に名指す。"""
        if self._mut_runs is not None:
            return
        children = (_child_of(item) for item in items)
        self._mut_runs = {
            child: self._pool.submit(_run_child, child, deadline_scale_factor)
            for child in children
            if child is not None and (EXAMPLES / child.name).is_file()
        }

    def result(self, name: str, failing_a_case: bool) -> subprocess.CompletedProcess[str]:
        assert self._mut_runs is not None, (
            "子を起こす前に結末を待った — 検は children の fixture から受け取る"
        )
        return self._mut_runs[Child(name, failing_a_case)].result()

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


@pytest.fixture(scope="module")
def _children() -> Iterator[Children]:
    children = Children()
    yield children
    children.close()


@pytest.fixture
def children(
    _children: Children, request: pytest.FixtureRequest, deadline_scale_factor: float
) -> Children:
    """子の池(負荷の倍率は関数の scope の fixture なので、module の scope の池へ最初の検が渡す)。"""
    _children.start(request.session.items, deadline_scale_factor)
    return _children


def _expected(name: str) -> Path:
    return EXPECTED / f"{Path(name).stem}.txt"


def _line_pattern(line: str) -> str | None:
    """期待の出力の 1 行(行だけの ANY でない行)の正規表現。MARKS の形でない ANY が在れば None。"""
    # 印で割ると、偶数番目は字のままの所・奇数番目は印。
    pieces = _MARK.split(line)
    if any(ANY in literal for literal in pieces[::2]):
        return None
    return "".join(
        MARKS[piece] if index % 2 else re.escape(piece) for index, piece in enumerate(pieces)
    )


def _mark_violations(expected: str) -> list[str]:
    """期待の出力の、印の形でない ANY を持つ行(行番号つき)。"""
    return [
        f"{number}: {line}"
        for number, line in enumerate(expected.split("\n"), start=1)
        if line != ANY and _line_pattern(line) is None
    ]


def _expected_line_pattern(index: int, line: str) -> str:
    """期待の出力の index 行目の正規表現。行だけの ANY は 1 行以上を名のある組(frames<行>)で呑む。印の形でない ANY は
    字のまま比べる(どこが悪いかは _mark_violations が名指す)。"""
    if line == ANY:
        return rf"(?P<frames{index}>[^\n]*(?:\n[^\n]*)*?)"
    pattern = _line_pattern(line)
    return re.escape(line) if pattern is None else pattern


def _pattern(expected: str) -> re.Pattern[str]:
    return re.compile(
        "\n".join(
            _expected_line_pattern(index, line) for index, line in enumerate(expected.split("\n"))
        )
    )


@functools.cache
def _source_lines(path: str) -> frozenset[str]:
    """file の空でない行(前後の空白を除く)。読めない file(<frozen …> など)は空。"""
    file = Path(path)
    if not file.is_file():
        return frozenset()
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    return frozenset(stripped for line in lines if (stripped := line.strip()))


def _frame_violations(lines: list[str]) -> list[str]:
    """traceback の frame の行でない行。frame の並びは File の行で始まり、続く行は File の行・直前の File の行の file の
    source の行・frame の中の印の行。"""
    heads = [_FRAME_HEAD.fullmatch(line) for line in lines]
    # 行ごとの、直前の File の行の file の source の行(その行より前に File の行が無ければ空)。
    sources = itertools.accumulate(
        heads,
        lambda source, head: source if head is None else _source_lines(head["path"]),
        initial=frozenset[str](),
    )
    return [
        line
        for index, (line, head, source) in enumerate(zip(lines, heads, sources, strict=False))
        if head is None
        and (index == 0 or not (line.strip() in source or _FRAME_MARKER.fullmatch(line)))
    ]


def _compare(expected: str, actual: str) -> list[str] | None:
    """actual を expected と比べる: 合わなければ None、合えば行だけの ANY が呑んだ行のうち frame の行でない物(無ければ
    空)。"""
    match = _pattern(expected).fullmatch(actual)
    if match is None:
        return None
    return [
        line
        for frames in match.groupdict().values()
        for line in _frame_violations(frames.split("\n"))
    ]


def _matches(expected: str, actual: str) -> bool:
    """actual が expected に合い、行だけの ANY が frame の行だけを呑んだか。"""
    return _compare(expected, actual) == []


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


def _doeff_hy_macro_names(form: hy.models.Expression) -> frozenset[str]:
    """`(require doeff-hy.macros [defk <- …] …)` が doeff-hy の module から引く macro の名(mangle した名)。"""
    return frozenset(
        hy.mangle(str(name))
        for module, names in itertools.pairwise(list(form)[1:])
        if _module_package(module) == _MACRO_OWNER and isinstance(names, hy.models.List)
        for name in names
        if isinstance(name, hy.models.Symbol)
    )


def _doeff_hy_expansions(source: str, filename: str) -> Iterator[tuple[str, object]]:
    """source の doeff-hy の macro の使い所ごとの、`<行>: <macro>` と 1 段の展開。展開は source の doeff-hy の require
    だけを評価した module の中でする(例を走らせない)。"""
    requires = [
        form
        for form in _forms(source, filename)
        if isinstance(form, hy.models.Expression)
        and form
        and form[0] == hy.models.Symbol("require")
    ]
    module = types.ModuleType(f"expansion_of_{Path(filename).stem}")
    macros = frozenset[str]().union(*(_doeff_hy_macro_names(form) for form in requires))
    for form in requires:
        if _doeff_hy_macro_names(form):
            hy.eval(form, module=module)
    for form in _forms(source, filename):
        if (
            isinstance(form, hy.models.Expression)
            and form
            and isinstance(form[0], hy.models.Symbol)
            and hy.mangle(str(form[0])) in macros
        ):
            yield f"{form.start_line}: {form[0]}", hy.macroexpand_1(form, module=module)


@pytest.mark.parametrize("name", _runnable())
def test_example_runs(name: str, children: Children) -> None:
    result = children.result(name, failing_a_case=False)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = _expected(name)
    assert expected.is_file(), (
        f"{name} の期待の出力 {expected.relative_to(EXAMPLES)} が無い — 例を走らせた標準出力を置き、"
        f"走るたびに変わる所を印にする"
    )
    swallowed = _compare(expected.read_text(encoding="utf-8"), result.stdout)
    assert swallowed is not None, _output_diff(expected, result.stdout)
    assert swallowed == [], (
        f"{expected.relative_to(EXAMPLES)} の行だけの {ANY} が traceback の frame でない行を呑んだ — 行だけの {ANY} は"
        f" frame だけを伏せる。呑んだ行を期待の出力に書く: {swallowed}"
    )


@pytest.mark.parametrize("name", _runnable())
def test_case_failure_shows_in_compared_output(name: str, children: Children) -> None:
    """件の中の失敗が、比べる出力に出る事: Traverse の走りごとに最初の件を件の中で失敗させた子は 0 で終わり、その出力は
    期待の出力に合わない。件を 1 つも走らせない例は NO_CASES に理由つきで置く。"""
    result = children.result(name, failing_a_case=True)
    marks = _run_marks(result)
    assert marks is not None, (
        f"件の中で失敗させる子が、実行の印を書かずに終わった\n{result.stdout}{result.stderr}"
    )
    if name in NO_CASES:
        assert marks.case_calls == 0, (
            f"{name} は件を走らせるようになった — NO_CASES の行(理由: {NO_CASES[name]})を外す"
        )
        return
    assert marks.case_calls > 0, (
        f"{name} が Traverse の件を 1 つも走らせない — 件を走らせず doeff-hy・Hy の中身だけを印字する debug 用の file は"
        f"例ではない(examples/ に置かない)。件の無い例なら NO_CASES に理由つきで足す"
    )
    # 子が 0 以外で終わるのは、起こした失敗が件の失敗として記録されず例の外へ出た時 — 出力の比べ方を確かめられないので、
    # 失敗が見えたとは数えない(traverse-lib の件の失敗の方針が変わったか、子の入口が壊れた — 設計検証
    # lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD の盲検 A)
    assert result.returncode == 0, (
        f"件の中で失敗させた子が 0 以外で終わった — 起こした失敗が件の失敗として記録されず、例の外へ出た。子の入口"
        f"(件の失敗の起こし方の持ち主)を traverse-lib の今の件の失敗の方針に合わせる\n{result.stdout}{result.stderr}"
    )
    expected = _expected(name)
    assert expected.is_file(), f"{name} の期待の出力が無い(test_example_runs が名指す)"
    assert not _matches(expected.read_text(encoding="utf-8"), result.stdout), (
        f"件の中で失敗した子の出力が {expected.relative_to(EXAMPLES)} に合う — 例が件の結果を印字しないか、"
        f"印が件の結果まで伏せている\n{result.stdout}"
    )


@pytest.mark.parametrize("name", _runnable())
def test_example_runs_a_program(name: str, children: Children) -> None:
    """例が doeff の run で Program を走らせる事を、名ではなく走った数で確かめる(件の中で失敗させる子が数える — 呼ばない
    関数の中の run や quote の中の run は数えない)。"""
    marks = _run_marks(children.result(name, failing_a_case=True))
    assert marks is not None, "件の中で失敗させる子が、実行の印を書かずに終わった"
    assert marks.program_runs > 0, (
        f"{name} は doeff の run で Program を走らせない — examples/ は Program を走らせて結果を印字する読み物の置き場で、"
        "macro の展開や関数の repr を印字するだけの debug 用の file は置かない"
    )


def test_marks_count_only_what_ran(tmp_path: Path, deadline_scale_factor: float) -> None:
    """今の例はどれも Program を走らせるので、名だけ在って走らない run を数えない事は、それを含む source で確かめる:
    import するだけ・呼ばない関数の中・quote の中・doeff の外の同じ名(例の中で定義した run)は数えず、走らせた 1 回だけを
    数える(名で数えた検は、呼ばない関数の中の呼び出しを口に数えた)。"""
    script = tmp_path / "names_without_runs.hy"
    script.write_text(
        """
(import doeff [run :as doeff-run Pure])
(defn never-called [] (doeff-run (Pure 0)))
(print (hy.repr '(doeff-run (Pure 0))))
(defn run [x] x)
(run 1)
(print (doeff-run (Pure 1)))
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(FAILING_A_CASE), str(script)],
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _run_marks(result) == RunMarks(case_calls=0, program_runs=1), result.stderr


def test_children_run_in_a_fixed_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """子は親の時間帯に依らず CHILD_TIME_ZONE で走る(親を UTC から 9 時間ずらしても、時刻 0 の地方時は 00:00)。"""
    monkeypatch.setenv("TZ", "JST-9")
    result = subprocess.run(
        _in_child_time_zone(
            [sys.executable, "-c", "import time; print(time.strftime('%H:%M', time.localtime(0)))"]
        ),
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout == "00:00\n"


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_example_is_still_broken(name: str, children: Children) -> None:
    assert (EXAMPLES / name).is_file(), f"BROKEN の行 {name} の例が examples/ に無い — 行を外す"
    result = children.result(name, failing_a_case=False)
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


def test_tables_name_examples_as_they_are() -> None:
    """期待の出力は走る例ちょうどに在り、件の無い例の表は今の例を名指す(消した・名を変えた例の行を残さない)。"""
    compared = _runnable()
    present = {path.name for path in EXPECTED.glob("*.txt")}
    assert present - {_expected(name).name for name in compared} == set(), (
        "比べる例の無い期待の出力が在る — 消すか、例を BROKEN から外す"
    )
    assert set(NO_CASES) - set(compared) == set(), "NO_CASES の行が、走る例を名指さない — 行を外す"


def test_expected_output_places_only_known_marks() -> None:
    """期待の出力の ANY は、行だけの ANY か MARKS の形でだけ置く(どの文字列にも合う印は件の結果まで伏せられる)。"""
    violations = {
        path.name: found
        for path in sorted(EXPECTED.glob("*.txt"))
        if (found := _mark_violations(path.read_text(encoding="utf-8")))
    }
    assert violations == {}, (
        f"期待の出力に印の形でない {ANY} が在る — 行の中の印は MARKS の形(呑める物の決まった印)で置き、traceback の"
        f" frame は行だけの {ANY} にする。走るたびに変わる物の種類が新しければ MARKS に行を足す: {violations}"
    )


def test_expected_output_places_nothing_unplaceable() -> None:
    found = {
        path.name: names
        for path in sorted(EXPECTED.glob("*.txt"))
        if (names := _unplaceable_in(path.read_text(encoding="utf-8")))
    }
    assert found == {}, (
        f"期待の出力に置かない物が在る — その所を印にする(展開の形の印なら、例は展開を印字しない): {found}"
    )


@pytest.mark.parametrize("name", _examples())
def test_example_expansions_show_their_marks(name: str) -> None:
    """例が使う doeff-hy の macro の展開には、どれも展開の形の印が在る — 展開を写して印字すると、期待の出力の検が名指す。
    印の無い展開を作る macro の写しは見分けられないので、その macro の展開に隠す名を持たせるか、印の種類を足す。"""
    path = EXAMPLES / name
    unmarked = [
        use
        for use, expansion in _doeff_hy_expansions(path.read_text(encoding="utf-8"), str(path))
        if not _shows_expansion(hy.repr(expansion))
    ]
    assert unmarked == [], (
        f"{name} の doeff-hy の macro の展開に、展開の形の印({_EXPANSION_IMPORT} も隠す名も)が無い — 写して印字されても"
        f"期待の出力の検が見分けない: {unmarked}"
    )


def test_printed_expansion_copy_is_named(tmp_path: Path, deadline_scale_factor: float) -> None:
    """Program を走らせる例に、展開を引き出さずに文字列で写した展開の印字を足すと、期待の出力の検が名指す(例の検の他の
    検 — 走る・Program を走らせる・macro の持ち主 — は通る形: 設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP の残る限界)。写しは
    今の展開そのもの(古い写しの形は test_unplaceable_table_catches_its_samples)。"""
    example = EXAMPLES / "ex04_simple.hy"
    source = example.read_text(encoding="utf-8")
    copied = next(
        hy.repr(expansion)
        for use, expansion in _doeff_hy_expansions(source, str(example))
        if use.endswith(": traverse")
    )
    script = tmp_path / "ex04_shows_expansion.hy"
    script.write_text(f"{source}(print {hy.repr(copied)})\n", encoding="utf-8")
    result = subprocess.run(
        _in_child_time_zone([sys.executable, "-m", "hy", str(script)]),
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _macro_violations(script.read_text(encoding="utf-8"), str(script)) == []
    assert result.stdout == f"{_expected(example.name).read_text(encoding='utf-8')}{copied}\n"
    assert _unplaceable_in(_expected(example.name).read_text(encoding="utf-8")) == []
    assert _unplaceable_in(result.stdout) == [
        "展開の形の印(Hy・doeff-hy の隠す名・展開の __import__)"
    ]


def test_mark_check_sees_unknown_marks() -> None:
    """今の期待の出力の印は全部決まった形なので、形でない印を拾う事・印が決まった物しか呑まない事は、それを含む文で
    確かめる。"""
    expected = f"Success: {ANY}\nasync: {ANY} in {ANY}s\nsync: 550 in {ANY}s\n{ANY}\n"
    assert _mark_violations(expected) == [f"1: Success: {ANY}", f"2: async: {ANY} in {ANY}s"]
    assert _matches(f"sync: 550 in {ANY}s\n", "sync: 550 in 1.02s\n")
    # 秒の印は秒の数しか呑まず、行を越えない
    assert not _matches(f"sync: {ANY}s\n", "sync: 550 in 1.02s\n")
    assert not _matches(f"sync: 550 in {ANY}s\n", "sync: 550 in 1.02\n  [2] OK: 3s\n")


def test_frame_mark_swallows_only_frames() -> None:
    """今の例の行だけの ANY は frame だけを伏せているので、件の結果の行を呑む事を拾うのは、それを含む出力で確かめる。"""
    frame = (
        f'        File "{Path(__file__)}", line 1, in <module>\n    import difflib\n    ^^^^^^\n'
    )
    traceback = f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{frame}"
    actual = f"{traceback}      KeyError: 'boom'\n  [2] OK: 2\n  [3] OK: 3\n"
    assert (
        _compare(
            f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{ANY}\n      KeyError: 'boom'\n  [2] OK: 2\n  [3] OK: 3\n",
            actual,
        )
        == []
    )
    # 呑みすぎ: 例外の行と件 [2] の行まで伏せた期待の出力
    swallowing = (
        f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{ANY}\n  [3] OK: 3\n"
    )
    assert _compare(swallowing, actual) == ["      KeyError: 'boom'", "  [2] OK: 2"]
    assert not _matches(swallowing, actual)
    # frame の並びは File の行で始まる
    assert _frame_violations(["  [0] OK: 1", *frame.rstrip("\n").split("\n")]) == ["  [0] OK: 1"]


def test_unplaceable_table_catches_its_samples() -> None:
    """今の期待の出力には置かない物が無いので、表の行ごとに、その物を含む文を拾い、印にした文は拾わない事を確かめる。"""
    expansion_marks = "展開の形の印(Hy・doeff-hy の隠す名・展開の __import__)"
    samples = {
        "checkout の path": f"{EXAMPLES / 'ex07_proboscis_ema_rewrite.hy'}:129",
        "機体の置き場(venv・Python・home)": f"{Path(sys.base_prefix) / 'lib' / 'traceback.py'}",
        expansion_marks: "raised in: _hy_anon_17:129 -> fetch_price:95",
        "traceback の frame の行": '        File "/x/doeff_core_effects/handlers.py", line 154, in attempt',
    }
    assert set(samples) == set(UNPLACEABLE), "UNPLACEABLE の行ごとに、拾う文をここに足す"
    assert [name for name, sample in samples.items() if not UNPLACEABLE[name](sample)] == []
    # 展開の写し: gensym・doeff-hy の補助の名(Hy の綴りのままでも)・記帳の属性・fnk の展開の __import__。古い展開の写し
    # (外した ex04_debug2 が「macro が作る形」として印字した形)も拾う
    assert [
        copy
        for copy in [
            "(setx _hy_gensym_bound_2 x)",
            "(_doeff_traverse_Traverse (_doeff_do (fn [x] …)) items)",
            "(import doeff [do :as _doeff-do])",
            "handler.__doeff_body__",
            '(fn [acc x] (((. (__import__ "doeff.do" :fromlist #("do")) do) (fn [] (do (+ acc x))))))',
            "(fn [x] (_doeff_do (fn [] …)))",
        ]
        if not UNPLACEABLE[expansion_marks](copy)
    ] == []
    # 例の結果の名(_ を中に持つ名・dunder)は拾わない
    assert not UNPLACEABLE[expansion_marks](
        "fetch_price: _private_total=3 my_doeff_do __init__ __class__ _"
    )
    masked = (
        f"raised in: {ANY}:129 -> fetch_price:95\n      Traceback (most recent call last):\n{ANY}\n"
    )
    assert _unplaceable_in(masked) == []
