"""pytest の item の記録 — item を作る macro が展開の時に書き、pytest の plugin が test module を import せずに読む。

agora-redesign #1211(pytest の収集は 1 秒以内)の形: 収集で test module を import すると module の最上位の実行が
走り、1 file で 10 秒かかる。そこで item を作る macro(deftest・defadr・defsemgrep と、module の直下の
``(val pytestmark …)``)が、展開の時に「この module が pytest に見せる関数と印」を記録する。記録は展開の式として
module に入り(``record-at-import`` が module の ``__doeff_pytest_items__`` に JSON の文字列を積む — pyc に焼き込まれる)、
pytest の plugin(doeff-adr)は 1 度 import した時にそれを取り出して、source の内容の hash を鍵に自分のキャッシュへ保存する。
Hy の importer には手を入れない(agora-redesign #1290 の利用者の決定 "not touch hy at all"・#1291)。

この module が記録の形の**唯一の定義元**: 書く関数(macro が呼ぶ)と読む関数(plugin が呼ぶ)を両方持つ。
deftest の鍵(``:marks``・``:params`` …)の意味は macro の側で読み、ここへは pytest の言葉(関数の名・引数の名・
parametrize・mark・skipif)に直した形で渡る。plugin は deftest の鍵を知らない。

params の値の扱い: 展開の時に見えるのは式の形だけなので、値が literal(文字列・整数・小数・真偽・None)なら値を
そのまま、literal の入れ物(list・tuple・dict・set・keyword)なら「中身を問わない値」(pytest の id は
``<引数の名><番号>`` になり値に依らない)として記録する。それ以外(式・名の参照)は本数が実行するまで決まらない
ので「動的」と記録する。pluginは初回のimport後に実値と明示idを補い、保存できれば次の収集から記録を使う。
"""

import json
import types
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import hy

# 展開の式が記録を積む module の名(import した module から plugin が取り出す)。
ITEMS_ATTR = "__doeff_pytest_items__"

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
    ids: tuple[str | None, ...] | None = None


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
        case Parametrize(argnames, values, ids):
            return {"parametrize": argnames, "values": [_encode_value(v) for v in values], "ids": ids}
        case Mark(name):
            return {"mark": name}
        case SkipIf():
            return {"skipif": True}


def _encode(record: Record) -> dict[str, Any]:
    """記録 1 つを JSON へ — 展開の式が module に積み、doeff-adr がキャッシュに保存する形。"""
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
        ids = raw.get("ids")
        return Parametrize(
            str(raw["parametrize"]), tuple(_decode_value(v) for v in raw["values"]),
            None if ids is None else tuple(None if value is None else str(value) for value in ids),
        )
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


# 記録の式の固定の部分(点を含む名は hy.models.Symbol で直に作れないので reader で読む)。
_RECORD_IMPORT = hy.read("(import doeff-hy.pytest-items)")
_RECORD_AT_IMPORT = hy.read("doeff-hy.pytest-items.record-at-import")


def _record_form(record: Record) -> hy.models.Expression:
    """記録 1 つを、展開に足す式にする — import の時に module の ``__doeff_pytest_items__`` へ JSON の文字列を積む。"""
    text = json.dumps(_encode(record), ensure_ascii=False, sort_keys=True)
    return hy.models.Expression(
        [
            hy.models.Symbol("do"),
            _RECORD_IMPORT,
            hy.models.Expression(
                [
                    _RECORD_AT_IMPORT,
                    hy.models.Expression([hy.models.Symbol("globals")]),
                    hy.models.String(text),
                ]
            ),
        ]
    )


def record_at_import(module_globals: MutableMapping[str, object], text: str) -> None:
    """展開の式が import の時に呼ぶ口 — module に記録(JSON の文字列)を 1 つ積む。"""
    items = module_globals.setdefault(ITEMS_ATTR, [])
    if not isinstance(items, list):
        raise MalformedRecord(f"{ITEMS_ATTR} が list でない: {type(items).__name__}")
    items.append(text)


def record_function(
    name: object,
    argnames: Sequence[object],
    decorators: Sequence[tuple[str, object, object]],
) -> hy.models.Expression:
    """test 関数を作る macro が、その関数の pytest の形の記録を展開に足すための口(足す式を返す)。

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
        return _record_form(Dynamic(fname, str(exc)))
    return _record_form(FunctionItem(fname, tuple(hy.mangle(str(a)) for a in argnames), tuple(specs)))


# module の印のうち、収集の結果(item の本数・fixture の閉包)を変える物 — 呼び出しの形なら値が要るので記録しない。
_COLLECTION_SHAPING_MARKS = frozenset({"parametrize", "usefixtures"})


def _mark_name(form: object) -> str:
    """module の直下の印の form から、収集で ``-m`` が読む印の名を読む(他の形は値が決まらない)。

    ``pytest.mark.<名>`` と、その呼び出し ``(pytest.mark.<名> …)`` を読む。呼び出しの引数(skipif の条件など)は
    記録しない — plugin が item の setup で実物の module の印に替えてから評価する。ただし収集の結果を変える印
    (parametrize・usefixtures)の呼び出しは値が要るので、決まらない物として扱う。
    """
    # Hy の reader は ``pytest.mark.real-world`` を ``(. pytest mark real-world)`` の式に読む。
    match form:
        case hy.models.Expression() if len(form) == 4 and all(
            isinstance(part, hy.models.Symbol) for part in form
        ) and [str(part) for part in list(form)[:3]] == [".", "pytest", "mark"]:
            return hy.mangle(str(list(form)[3]))
        case hy.models.Expression() if len(form) >= 1:
            name = _mark_name(list(form)[0])
            if name in _COLLECTION_SHAPING_MARKS:
                raise _NotStatic(hy.repr(form))
            return name
        case _:
            raise _NotStatic(hy.repr(form))


def record_module_binding(head: str, args: Sequence[object]) -> hy.models.Expression | None:
    """module の直下の ``(val pytestmark …)`` / ``(var pytestmark …)`` の印の記録を展開に足すための口(足す式を返す)。
    他の束縛は記録しない(None)。"""
    if head not in ("val", "var") or len(args) < 2:
        return None
    if not isinstance(args[0], hy.models.Symbol) or hy.mangle(str(args[0])) != "pytestmark":
        return None
    value = args[-1]
    try:
        match value:
            case hy.models.List() | hy.models.Tuple():
                forms = list(value)
            case _:
                forms = [value]
        names = tuple(_mark_name(form) for form in forms)
    except _NotStatic as exc:
        return _record_form(Dynamic("pytestmark", str(exc)))
    return _record_form(ModuleMarks(names))


# ---------------------------------------------------------------------------
# 読む側(plugin が収集で呼ぶ)
# ---------------------------------------------------------------------------


def encode_records(records: Iterable[Record]) -> list[str]:
    """import後に補った実値も、macroと同じ記録の形式で保存する。"""
    return [json.dumps(_encode(record), ensure_ascii=False, sort_keys=True) for record in records]


def decode_records(texts: Iterable[object]) -> list[Record]:
    """記録の JSON の文字列の並び(module に積まれた物か、plugin のキャッシュに保存した物)を型へ読む。

    形が違えば ``MalformedRecord``(呼ぶ側はその file を import して収集する)。
    """
    try:
        return [_decode(json.loads(cast(str, text))) for text in texts]
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise MalformedRecord(str(exc)) from exc


def module_record_texts(module: types.ModuleType) -> list[str]:
    """import した module に展開の式が積んだ記録(JSON の文字列)。記録を作る macro を 1 つも使わない module は空。"""
    items = vars(module).get(ITEMS_ATTR, [])
    if not isinstance(items, list) or not all(isinstance(t, str) for t in items):
        raise MalformedRecord(f"{module.__name__}.{ITEMS_ATTR} が文字列の list でない")
    return list(items)
