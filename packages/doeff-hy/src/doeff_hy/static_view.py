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
- defhandler / handle の関数(agora-redesign #2279 — handle.hy): 節を回す関数の引数 `effect: object`
  (節の `isinstance` が型を絞る)・`k: Continuation`・答え `ClauseRun`、handler を被せる関数の
  本文 `HandlerBody[HandledAnswer]` → `HandledScope[HandledAnswer]`、節の終わり方の検めは型付きの
  static_types の名(HANDLER_STATIC_NAMES)から引く。doeff の node は節の操作の表(clause_ops.hy)の static-nodes と
  handler 自身の Pass だけを import する(Resume / Transfer は typed_resume / typed_transfer が代わるので使わない)
- defhandler / handle の節の effect(card ki-fede97890f70 — handle.hy の `_effect-form-binding`): 節が欄を束ねるか typed_resume /
  typed_transfer で effect を読む時、`isinstance` で絞った後の `_doeff_effect` を `any_specialized(EffectType, *any_args,
  **any_kwargs)` に束ね直す。pyright は総称 class の絞りの型引数を Unknown にする(`Race[Unknown]`)が、構成子の呼びは型引数を
  すべて Any で解く(`Race[Any]`)ので、欄の型は effect の定義の型(`tuple[Task[Any] | Future[Any], ...]`)になり、Unknown の赤が出ない
- deftest の関数: 引数に fixture の型の注記(DEFTEST_FIXTURE_TYPES)・返り値 None・decorator の pytest は
  `_doeff_pytest`(agora-redesign #2214 — 実行時の展開は注記なしで interpreter の答えを返す)
- defk の返り値: `:post` の `(: % T)` の T を注記する(agora-redesign #2308 — この展開の defk は yield の無い
  普通の関数なので、自分を呼び直す defk でも答えの型を推論に頼らず読める。実行時の defk は生成器なので付けない・
  macros.hy の `_annotates-return?`)
- defhandler の session val / session var(agora-redesign #2293 — handle.hy の `_build-lazy-init-forms`): 名は初期化の値に
  束ねる(Get を使わない)。session var の書き換え(`:=`・旧い `set!`)の Put / Some と初期化のキーの session_key は
  `_doeff_<名>` で引き、module の頭の補助の import(SESSION_STATIC_IMPORTS)から取る
- defmcp-tool(card ki-4848d81e0bc5): import を 1 つも出さない。handler の decorator は module の頭の `_doeff_do`、
  tool の値の McpToolDef / McpParamSchema は `_doeff_<名>` で引き、module の頭の補助の import(MCP_STATIC_IMPORTS)から取る

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
from doeff_hy.expansion_names import alias, import_source

# 型検査のための展開の印は bytecode の包みが持つ — 包みの compile の口が、この展開で作った実行できない code に macro の依存の
# 記録を付けず、.pyc と共有の code の置き場から普通の import へ渡らないようにするため(doeff_hy_bytecode_guard/_expansion.py)。
from doeff_hy_bytecode_guard import TYPE_CHECK_EXPANSION as _STATIC_VIEW


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
    return alias(type_name)


def deftest_fixture_annotation(fixture: str) -> str:
    """deftest の引数(mangle した名)に、型検査のための展開で付ける注記の名(表に無い名は `object`)。"""
    found = [entry.type_name for entry in DEFTEST_FIXTURE_TYPES if entry.fixture == fixture]
    return deftest_annotation_name(found[0]) if found else "object"


#: defhandler / handle の型検査のための展開が参照する static_types の名(agora-redesign #2279)— 展開の中では
#: `_doeff_<名>` で引き、doeff-hy-check が module の頭で 1 度だけ import する(STATIC_HELPER_IMPORTS)。
#: - 型: 節を回す関数の答え `ClauseRun`・続き `Continuation`・handler を被せる本文 `HandlerBody[HandledAnswer]` と
#:   被せた結果 `HandledScope[HandledAnswer]`(本文の答えの型をそのまま運ぶ型変数)。
#: - 関数: 節の終わり方の検め(clause_endings.hy の check-clause-endings・check-clause-endings-once・fell-through)。
#:   実体は .hy なので pyright から型が見えない — 実行時の展開は今までどおり clause_endings から import する。
#: - 総称の effect の節: 構成子の呼びで型引数をすべて Any に解く `any_specialized` と、その呼びの引数 `any_args`・`any_kwargs`
#:   (card ki-fede97890f70 — 節の `isinstance` の絞りは総称 class の型引数を Unknown にし、欄の読みと typed_resume の引数が
#:   一部 Unknown の赤になる。handle.hy の `_effect-form-binding`)。
HANDLER_STATIC_NAMES: tuple[str, ...] = (
    "ClauseRun",
    "Continuation",
    "HandledAnswer",
    "HandledScope",
    "HandlerBody",
    "any_args",
    "any_kwargs",
    "any_specialized",
    "check_clause_endings",
    "check_clause_endings_once",
    "fell_through",
)

#: defeffect の型検査のための展開が参照する static_types の名(答えの分けを宣言した effect の `__iter__` の返り値 — declarations.hy)。
DEFEFFECT_STATIC_NAMES: tuple[str, ...] = ("BoundRun",)

#: for/do・traverse の型検査のための展開が参照する static_types の名(agora-redesign #2321)— 件の引数を items の要素の
#: 型へ読み直す `traverse_item`(macros.hy の _traverse-form)。展開の中では `_doeff_<名>` で引く。
TRAVERSE_STATIC_NAMES: tuple[str, ...] = ("traverse_item",)

#: defhandler の session val / session var の型検査のための展開が参照する名 — 展開の中では `_doeff_<名>` で引く。
#: - `Put` と `Some`: session var の書き換え(`:=` — binding_forms.py の `_assign`、旧い `set!` — macros.hy)だけが使う。
#: - `session_key`: session val / session var の初期化のキーを作る(handle.hy の `_build-lazy-init-forms` — 旧い lazy は使わない)。
#: handler ごとに import を出すと、:= も set! も無い handler の module で Put / Some が strict の「import が使われていない」の赤に
#: なり、書き手は直せなかった(agora-controllers の日次 2026-10-11 16:00 の 7 file・14 件)。module の頭の補助の import に載せ、
#: 読む時だけ置く。名に `_doeff_` を付けるのは、書き手の書いた Put / Some の import と 1 つの名を分け合わないため — 実行時の
#: 展開も同じ別名を引く(expansion_names.py が別名の綴りと import の唯一の定義元)。
SESSION_STATIC_IMPORTS: str = (
    import_source(("Some", "Put")) + "from doeff_hy.session import session_key as _doeff_session_key\n"
)

#: defmcp-tool の型検査のための展開が参照する名 — 展開の中では `_doeff_<名>` で引く(macros.hy の defmcp-tool)。
#: tool ごとに `from doeff.mcp import McpToolDef, McpParamSchema` を出していた頃は、doeff の module なので重ねの畳み
#: (static_check.without_bookkeeping)にも掛からず、tool を 65 個持つ module か class で McpToolDef の宣言が 64 を超え、
#: pyright が tool の値を Unknown にして、tool を使う側の型の誤りを黙って通していた(card ki-4848d81e0bc5 の設計検証の盲検 A)。
#: 名に `_doeff_` を付けるのは、書き手の書いた McpToolDef の import と 1 つの名を分け合わないため。
MCP_STATIC_IMPORTS: str = "from doeff.mcp import McpToolDef as _doeff_McpToolDef, McpParamSchema as _doeff_McpParamSchema\n"


#: 型検査のための展開で、macro が参照する補助の名の import(module の頭に 1 度だけ — doeff-hy-check が置く)。
#: 実行時の展開は defk / defhandler / `<-` ごとに同じ import を出すが、静的な展開で同じことをすると、1 つの名に
#: 宣言が積み上がる。pyright は 1 つの名の宣言が 64 を超えると型の推論をやめて Unknown にするので、defk を 65 個
#: 持つ module では `_doeff_do` が Unknown になり、defk の呼びが Program ではなく :post の型に見えていた
#: (doeff-cluster の local.hy の `(Spawn (defk の呼び))` 4 か所 — agora-redesign #1686)。静的な展開の macro は
#: この import を出さない(macros.hy の `_helper-imports`・`_bind-yield`・defmcp-tool、handle.hy の `_do-import`)。
STATIC_HELPER_IMPORTS: str = (
    "from doeff_hy.static_types import do as _doeff_do, _doeff_perform"
    + "".join(
        f", {name} as {deftest_annotation_name(name)}"
        for name in (
            *sorted({entry.type_name for entry in DEFTEST_FIXTURE_TYPES}),
            *HANDLER_STATIC_NAMES,
            *DEFEFFECT_STATIC_NAMES,
            *TRAVERSE_STATIC_NAMES,
        )
    )
    + "\n"
    "from doeff_hy.macros import _install_guard_globals, _guard_performed, _guard_statement_value, "
    "_doeff_check_program_return\n"
    # deftest の decorator(@pytest.mark.…)が引く pytest(deftest ごとの import が書き手の import と重ならないように)
    "import pytest as _doeff_pytest\n" + SESSION_STATIC_IMPORTS + MCP_STATIC_IMPORTS
)

#: defeffect が答えの分け方(:absent・:failure・:value)を置く時に出す、別名の無い `import <module>`(declarations.hy の
#: defeffect-form)。型検査のための展開の掃除が、`_doeff_` の別名で束ねる import(持ち主の package の定義の macro — doeff-cluster の
#: defsystem など — が出す形)と同じく macro が合成する import として畳み、関数・class の本体の中の
#: 物を module の段へ移す綴り。展開の綴りとの一致は tests/test_nested_definition_scope.py が関数の中の defeffect の投影で検める。
DEFEFFECT_OUTCOMES_MODULE: str = "doeff_core_effects.outcomes"


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
