"""doeff-hy の macro を「型検査のための展開」に切り替える口(1 点)。

`doeff-hy-check`(doeff_hy/static_check.py)は Hy の source を本物の macro で展開し、
得た Python を pyright に読ませる。実行時の展開のうち、型検査に要る情報を持たない所
(`(<- x T e)` の `x = yield e` は x の型を e から導けない)だけを、この切替が入っている
間は型の分かる形で出す。切替は macro の展開の時にだけ読む(実行時には読まない)ので、
通常の import・bytecode の cache・実行の意味は変わらない。

切替で形が変わる所(macros.hy の `_static-view?` を読む所がすべて):
- `_bind-yield`: `(setv x (yield e))` → `x: T = _doeff_perform(e)`
  (`_doeff_perform` は e の答えの型を返すと宣言した静的な関数。doeff_hy/static_types.pyi。
  Python の `@effectful` の `x = perform(e)` と同じ形)
- defk / defp / do! / deftest / fnk: `do` を型付きの `doeff_hy.static_types.do` から取る
  (呼んだ結果が core の `Expand[T, E]` = 答えの型 T を持つ Program になる)
- defhandler の `resume` / `transfer`: core の `typed_resume` / `typed_transfer`
  (答えの値を effect の `EffectBase[T]` の T と突き合わせる)
- deftest の関数: 引数に fixture の型の注記(DEFTEST_FIXTURE_TYPES)・返り値 None・decorator の pytest は
  `_doeff_pytest`(agora-redesign #2214 — 実行時の展開は注記なしで interpreter の答えを返す)

所見の受け渡し(ADR-DOE-HY-006): val / var の検査のうち展開を止めない物(setv の使用・同じ名前の
束縛し直し・defhandler の旧い lazy-val / set!)は、macro が `report_findings` に渡す。doeff-hy-check が
`collect_findings` の中で展開した時だけ集まり、警告 / 赤として出る(通常の import では捨てる)。

どちらの展開にも共通で型のための形を持つ所(実行時に評価されない・意味を変えない):
引数と deff の戻り値の文字列の注記、結果の変数 `_contract_result: T`、補助の import
(型は macros.pyi)。
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from doeff_hy.binding_forms import Finding

_STATIC_VIEW: ContextVar[bool] = ContextVar("doeff_hy_static_view", default=False)


@dataclass(frozen=True)
class FixtureType:
    """pytest の fixture の名 1 つと、その値の型(doeff_hy/static_types.pyi の型の名)。"""

    fixture: str
    type_name: str


#: deftest が作る検の関数の引数の型(agora-redesign #2214)— fixture の名と static_types.pyi の型の名の組の唯一の定義元。
#: 型検査のための展開では、deftest(macros.hy)が `deftest_fixture_annotation` で引数に注記 `_doeff_<型の名>` を付け、
#: doeff-hy-check がその名を module の頭で static_types から import する(STATIC_HELPER_IMPORTS)。ここに無い名
#: (利用者が conftest で定義した fixture・:params の引数)は、書き手の `#^ T 名` の注記をそのまま、無ければ `object` を付ける。
#: pytest の組み込みのうち tmpdir は型(py.path.local)が pytest の公開の名に無いので載せない(object になる)。
DEFTEST_FIXTURE_TYPES: tuple[FixtureType, ...] = (
    FixtureType("doeff_interpreter", "DeftestInterpreter"),
    FixtureType("tmp_path", "TmpPath"),
    FixtureType("tmp_path_factory", "TmpPathFactory"),
    FixtureType("tmpdir_factory", "TmpdirFactory"),
    FixtureType("monkeypatch", "MonkeyPatch"),
    FixtureType("capsys", "CaptureStr"),
    FixtureType("capteesys", "CaptureStr"),
    FixtureType("capfd", "CaptureStr"),
    FixtureType("capsysbinary", "CaptureBytes"),
    FixtureType("capfdbinary", "CaptureBytes"),
    FixtureType("caplog", "LogCapture"),
    FixtureType("request", "FixtureRequest"),
    FixtureType("pytestconfig", "PytestConfig"),
    FixtureType("recwarn", "WarningsRecorder"),
    FixtureType("cache", "PytestCache"),
    FixtureType("subtests", "Subtests"),
    FixtureType("record_property", "RecordProperty"),
    FixtureType("record_xml_attribute", "RecordProperty"),
    FixtureType("record_testsuite_property", "RecordProperty"),
    FixtureType("doctest_namespace", "DoctestNamespace"),
)


def deftest_annotation_name(type_name: str) -> str:
    """static_types の型の名を、展開の中で使う名(module の頭の import の別名)にする。"""
    return f"_doeff_{type_name}"


def deftest_fixture_annotation(fixture: str) -> str:
    """deftest の引数(mangle した名)に、型検査のための展開で付ける注記の名(表に無い名は `object`)。"""
    found = [entry.type_name for entry in DEFTEST_FIXTURE_TYPES if entry.fixture == fixture]
    return deftest_annotation_name(found[0]) if found else "object"


#: 型検査のための展開で、macro が参照する補助の名の import(module の頭に 1 度だけ — doeff-hy-check が置く)。
#: 実行時の展開は defk / defhandler / `<-` ごとに同じ import を出すが、静的な展開で同じことをすると、1 つの名に
#: 宣言が積み上がる。pyright は 1 つの名の宣言が 64 を超えると型の推論をやめて Unknown にするので、defk を 65 個
#: 持つ module では `_doeff_do` が Unknown になり、defk の呼びが Program ではなく :post の型に見えていた
#: (doeff-cluster の local.hy の `(Spawn (defk の呼び))` 4 か所 — agora-redesign #1686)。静的な展開の macro は
#: この import を出さない(macros.hy の `_helper-imports`・`_bind-yield`、handle.hy の `_do-import`)。
STATIC_HELPER_IMPORTS: str = (
    "from doeff_hy.static_types import do as _doeff_do, _doeff_perform"
    + "".join(
        f", {name} as {deftest_annotation_name(name)}"
        for name in sorted({entry.type_name for entry in DEFTEST_FIXTURE_TYPES})
    )
    + "\n"
    "from doeff_hy.macros import _install_guard_globals, _guard_performed, _guard_statement_value, "
    "_doeff_check_program_return\n"
    # deftest の decorator(@pytest.mark.…)が引く pytest(deftest ごとの import が書き手の import と重ならないように)
    "import pytest as _doeff_pytest\n"
)


def static_view_enabled() -> bool:
    return _STATIC_VIEW.get()


@contextmanager
def static_view() -> Iterator[None]:
    token = _STATIC_VIEW.set(True)
    try:
        yield
    finally:
        _STATIC_VIEW.reset(token)


_FINDINGS: ContextVar[list[Finding] | None] = ContextVar("doeff_hy_static_findings", default=None)


def report_findings(findings: Iterable[Finding]) -> None:
    """macro の所見を、集めている doeff-hy-check へ渡す(集めていない時は捨てる — 展開は止めない)。"""
    sink = _FINDINGS.get()
    if sink is not None:
        sink.extend(findings)


@contextmanager
def collect_findings() -> Iterator[list[Finding]]:
    """この中で展開した macro の所見を集める(doeff-hy-check が 1 file の展開ごとに使う)。"""
    found: list[Finding] = []
    token = _FINDINGS.set(found)
    try:
        yield found
    finally:
        _FINDINGS.reset(token)
