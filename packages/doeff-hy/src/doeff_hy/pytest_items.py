"""pytest の item の記録 — item を作る macro が展開の時に書き、pytest の plugin が test module を import せずに読む。

agora-redesign #1211(pytest の収集は 1 秒以内)の形: 収集で test module を import すると module の最上位の実行が
走り、1 file で 10 秒かかる。そこで item を作る macro(deftest・defadr・defsemgrep と、module の直下の
``(val pytestmark …)``)が、展開の時に「この module が pytest に見せる関数と印」を記録する。記録は Hy の
importer が pyc の隣の ``.hydeps`` に書き、``hy.importer.read_valid_records`` が pyc と同じ条件で有効な時だけ返す
(記録が有効 ⇔ pyc が有効)。

この module が記録の形の**唯一の定義元**: 書く関数(macro が呼ぶ)と読む関数(plugin が呼ぶ)を両方持つ。
deftest の鍵(``:marks``・``:params`` …)の意味は macro の側で読み、ここへは pytest の言葉(関数の名・引数の名・
parametrize・mark・skipif)に直した形で渡る。plugin は deftest の鍵を知らない。

params の値の扱い: 展開の時に見えるのは式の形だけなので、値が literal(文字列・整数・小数・真偽・None)なら値を
そのまま、literal の入れ物(list・tuple・dict・set・keyword)なら「中身を問わない値」(pytest の id は
``<引数の名><番号>`` になり値に依らない)として記録する。それ以外(式・名の参照)は本数が実行するまで決まらない
ので「動的」と記録し、plugin はその module を収集で import する。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import hy
from hy.importer import BOUND_NAMES_RECORD, DECORATORS_RECORD, TOP_LEVEL_CALLS_RECORD, add_compile_record

NAMESPACE = "doeff.pytest-items"

JsonValue = str | int | float | bool | None


# ---------------------------------------------------------------------------
# 記録の型(読む側に返す形)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiteralValue:
    """params の値のうち、展開の時に値が分かる物(pytest の id が値から決まる)。"""

    value: JsonValue


@dataclass(frozen=True)
class OpaqueValue:
    """params の値のうち、literal の入れ物(pytest の id は引数の名と番号 — 値に依らない)。"""


ParamValue = LiteralValue | OpaqueValue


@dataclass(frozen=True)
class Parametrize:
    argnames: str
    values: tuple[ParamValue, ...]


@dataclass(frozen=True)
class Mark:
    name: str


@dataclass(frozen=True)
class SkipIf:
    """条件は実行の時の式なので記録しない。評価は item の setup で実物の関数の印から行う。"""


Decorator = Parametrize | Mark | SkipIf


@dataclass(frozen=True)
class FunctionItem:
    """pytest に見せる test 関数 1 つ。``decorators`` は source の順(上から)— 実物と同じく下から当てる。"""

    name: str
    argnames: tuple[str, ...]
    decorators: tuple[Decorator, ...]


@dataclass(frozen=True)
class ModuleMarks:
    """module の直下の ``pytestmark``。"""

    names: tuple[str, ...]


@dataclass(frozen=True)
class Dynamic:
    """展開の時に item の形が決まらない物(本数が式の値に依る等)。"""

    where: str
    reason: str


Record = FunctionItem | ModuleMarks | Dynamic


@dataclass(frozen=True)
class RecordedModule:
    """有効な記録から読んだ module 1 つ分。"""

    records: tuple[Record, ...]
    bound_names: frozenset[str]
    """module の最上位で束縛された名(定義・代入・import — Hy の importer の記録)。"""
    decorators: dict[str, tuple[str, ...]]
    """最上位の定義のうち decorator の付いた物の名 → decorator の頭の名(``pytest.fixture`` のように、名に依らず pytest に意味を持たせうる)。"""
    top_level_calls: frozenset[str]
    """module の実行の時に、関数と class の本体の外で呼ぶ関数の頭の名(``pytest.skip`` は module ごと飛ばす)。"""


# ---------------------------------------------------------------------------
# JSON の境界(記録の形と型の相互の変換はここだけ)
# ---------------------------------------------------------------------------


class MalformedRecord(ValueError):
    """記録がこの module の書く形でない(版の違う doeff-hy が書いた等)。"""


def _encode_value(value: ParamValue) -> dict[str, Any]:
    """params の値 1 つを記録の JSON へ(書く側の境界)。"""
    match value:
        case LiteralValue(v):
            return {"literal": v}
        case OpaqueValue():
            return {"opaque": True}


def _encode_decorator(decorator: Decorator) -> dict[str, Any]:
    """decorator 1 つを記録の JSON へ(書く側の境界)。"""
    match decorator:
        case Parametrize(argnames, values):
            return {"parametrize": argnames, "values": [_encode_value(v) for v in values]}
        case Mark(name):
            return {"mark": name}
        case SkipIf():
            return {"skipif": True}


def _encode(record: Record) -> dict[str, Any]:
    """記録 1 つを JSON へ — Hy の importer が .hydeps に書く形。"""
    match record:
        case FunctionItem(name, argnames, decorators):
            return {
                "function": name,
                "args": list(argnames),
                "decorators": [_encode_decorator(d) for d in decorators],
            }
        case ModuleMarks(names):
            return {"pytestmark": list(names)}
        case Dynamic(where, reason):
            return {"dynamic": where, "reason": reason}


def _decode_value(raw: Mapping[str, Any]) -> ParamValue:
    """記録の JSON から params の値 1 つを読む(読む側の境界)。"""
    if "literal" in raw:
        return LiteralValue(cast(JsonValue, raw["literal"]))
    if raw.get("opaque") is True:
        return OpaqueValue()
    raise MalformedRecord(f"params の値 {raw!r}")


def _decode_decorator(raw: Mapping[str, Any]) -> Decorator:
    """記録の JSON から decorator 1 つを読む(読む側の境界)。"""
    if "parametrize" in raw:
        return Parametrize(str(raw["parametrize"]), tuple(_decode_value(v) for v in raw["values"]))
    if "mark" in raw:
        return Mark(str(raw["mark"]))
    if raw.get("skipif") is True:
        return SkipIf()
    raise MalformedRecord(f"decorator {raw!r}")


def _decode(raw: Mapping[str, Any]) -> Record:
    """記録の JSON 1 つを型へ(読む側の境界)。"""
    if "function" in raw:
        return FunctionItem(
            name=str(raw["function"]),
            argnames=tuple(str(a) for a in raw["args"]),
            decorators=tuple(_decode_decorator(d) for d in raw["decorators"]),
        )
    if "pytestmark" in raw:
        return ModuleMarks(tuple(str(n) for n in raw["pytestmark"]))
    if "dynamic" in raw:
        return Dynamic(str(raw["dynamic"]), str(raw["reason"]))
    raise MalformedRecord(f"記録 {raw!r}")


# ---------------------------------------------------------------------------
# 書く側(macro が展開の時に呼ぶ)
# ---------------------------------------------------------------------------


class _NotStatic(Exception):
    """form が展開の時に値の決まらない物だった(記録は Dynamic になる)。"""


_CONTAINERS = (hy.models.List, hy.models.Tuple, hy.models.Dict, hy.models.Set, hy.models.Keyword)
_CONSTANTS: dict[str, JsonValue] = {"True": True, "False": False, "None": None}


def _param_value(form: object) -> ParamValue:
    """params の値の form 1 つを、pytest の id が同じになる記録の値へ直す。"""
    match form:
        case hy.models.FString():
            raise _NotStatic("f-string")
        case hy.models.String() as text:
            return LiteralValue(str(text))
        case hy.models.Integer() as number:
            return LiteralValue(int(number))
        case hy.models.Float() as number:
            return LiteralValue(float(number))
        case hy.models.Symbol() if str(form) in _CONSTANTS:
            return LiteralValue(_CONSTANTS[str(form)])
        case _ if isinstance(form, _CONTAINERS):
            return OpaqueValue()
        case _:
            raise _NotStatic(hy.repr(form))


def _values(form: object) -> list[ParamValue]:
    """parametrize の値の list の form を読む — list / tuple の literal でなければ本数が決まらない。"""
    match form:
        case hy.models.List() | hy.models.Tuple():
            return [_param_value(value) for value in form]
        case _:
            raise _NotStatic(f"values {hy.repr(form)}")


def _record(compiler: Any, record: Record) -> None:
    """展開中の module に記録を 1 つ足す(Hy の importer が pyc と一緒に書く)。"""
    add_compile_record(compiler.module, NAMESPACE, _encode(record))


def record_function(
    compiler: Any,
    name: object,
    argnames: Sequence[object],
    decorators: Sequence[tuple[str, object, object]],
) -> None:
    """test 関数を作る macro が、その関数の pytest の形を記録するための口。

    ``decorators`` は source の順に次の 3 つの形の組:
    ``("parametrize", <引数の名の文字列>, <値の list の form>)`` /
    ``("mark", <印の名の form>, None)`` / ``("skipif", None, None)``。
    """
    fname = hy.mangle(str(name))
    try:
        specs: list[Decorator] = []
        for kind, first, second in decorators:
            match kind:
                case "parametrize":
                    specs.append(Parametrize(str(first), tuple(_values(second))))
                case "mark":
                    specs.append(Mark(hy.mangle(str(first))))
                case "skipif":
                    specs.append(SkipIf())
                case _:
                    raise ValueError(f"pytest_items: 知らない decorator の種類 {kind!r}")
    except _NotStatic as exc:
        _record(compiler, Dynamic(fname, str(exc)))
        return
    _record(compiler, FunctionItem(fname, tuple(hy.mangle(str(a)) for a in argnames), tuple(specs)))


def _mark_name(form: object) -> str:
    """``pytest.mark.<名>`` の形の form から印の名を読む(他の形は値が決まらない)。"""
    # Hy の reader は ``pytest.mark.real-world`` を ``(. pytest mark real-world)`` の式に読む。
    match form:
        case hy.models.Expression() if len(form) == 4 and all(
            isinstance(part, hy.models.Symbol) for part in form
        ) and [str(part) for part in list(form)[:3]] == [".", "pytest", "mark"]:
            return hy.mangle(str(list(form)[3]))
        case _:
            raise _NotStatic(hy.repr(form))


def record_module_binding(compiler: Any, head: str, args: Sequence[object]) -> None:
    """module の直下の ``(val pytestmark …)`` / ``(var pytestmark …)`` の印を記録するための口(他の束縛は何もしない)。"""
    if head not in ("val", "var") or len(args) < 2:
        return
    if not isinstance(args[0], hy.models.Symbol) or hy.mangle(str(args[0])) != "pytestmark":
        return
    value = args[-1]
    try:
        match value:
            case hy.models.List() | hy.models.Tuple():
                forms = list(value)
            case _:
                forms = [value]
        names = tuple(_mark_name(form) for form in forms)
    except _NotStatic as exc:
        _record(compiler, Dynamic("pytestmark", str(exc)))
        return
    _record(compiler, ModuleMarks(names))


# ---------------------------------------------------------------------------
# 読む側(plugin が収集で呼ぶ)
# ---------------------------------------------------------------------------


def read_module(records: Mapping[str, Iterable[Any]]) -> RecordedModule:
    """plugin が import せずに item を作るため、``hy.importer.read_valid_records`` の記録からこの module の item を読む。

    記録の形が違えば ``MalformedRecord``(呼ぶ側はその file を import して収集する)。
    """
    try:
        items = tuple(_decode(raw) for raw in records.get(NAMESPACE, ()))
        bound = frozenset(str(n) for n in records.get(BOUND_NAMES_RECORD, ()))
        raw_decorators = records.get(DECORATORS_RECORD, {})
        if not isinstance(raw_decorators, Mapping):
            raise MalformedRecord(f"decorator の記録 {raw_decorators!r}")
        decorators = {str(name): tuple(str(h) for h in heads) for name, heads in raw_decorators.items()}
        calls = frozenset(str(n) for n in records.get(TOP_LEVEL_CALLS_RECORD, ()))
    except (KeyError, TypeError, AttributeError) as exc:
        raise MalformedRecord(str(exc)) from exc
    return RecordedModule(items, bound, decorators, calls)
