"""不在と失敗の実行時(ADR-DOE-CORE-EFFECTS-003)— effect の答えの宣言、``<-`` が出す開き、境目の handler。

語彙:

- ``Absent`` / ``Raise``(``doeff_core_effects._effects``)— 想定内の不在(Maybe の側)と失敗(Result の側)。
- ``Outcomes`` — effect の答えの型を 成功・不在・失敗・値 に分ける宣言。``defeffect`` の頭の辞書の
  ``:absent`` / ``:failure`` / ``:value`` から作られ、effect の型の属性 ``__doeff_outcomes__`` に残る(R5)。
- ``open_bind`` — ``(<- x e)`` と ``(! e)`` が yield する物を返す。宣言を持つ effect は不在の答えを ``Absent`` に、
  失敗の答えを ``Raise(答え)`` に変えて呼び手のスコープで出す Program に、Result / Maybe の値は開く Program に
  する。それ以外(宣言の無い effect・Program)は **受け取った物をそのまま** 返す — 今までと同じ物を yield する(R6)。
- 境目の handler(effect → 値に畳む): ``maybe``・``result``・``on_raise``・``absent_as``(R2・R7・R8)。
  設定の無い ``maybe``・``result`` は handler の値で ``maybe(p)``・``result(p)`` と当て(ADR-DOE-API-002 R1 — 旧い呼び
  ``maybe()`` は 1 つの版の間 DeprecationWarning で同じ handler を返す)、設定を受ける ``on_raise``・``absent_as`` は
  handler を返す工場で ``on_raise(受け…)(p)``・``absent_as(既定値)(p)`` と当てる(ADR-DOE-API-001 R6)。
  続きを再開しない(スコープをその値で終える)。再開してよいのは ``absent_as`` が、自分の字面の中の ``<-`` が出した
  ``Absent`` を再開する時だけ。

handler は値で答え、変換は呼び手の本文の ``<-`` で行う(R4)— handler の節の中で ``Absent`` / ``Raise`` を出すと、
その handler より外へ行き、呼び手の ``maybe`` / ``result`` を飛び越えるため。
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from types import GeneratorType, NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Never,
    Protocol,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    overload,
)

from doeff import BindOpener, Call, EffectBase, Err, Expand, IRStream, Ok, Pure
from doeff.do import do
from doeff.program import Pass, ProgramHandler, Transfer, _called_as_former_factory
from doeff.program import handler as _program_handler
from doeff.result import Maybe, Nothing, Some, _NothingType
from doeff_core_effects._effects import Absent, Raise

if TYPE_CHECKING:
    from typing_extensions import deprecated

    # doeff の最上位は doeff_core_effects._effects を読み込む途中でこの module に来るので、最上位の後半で定義される
    # 名前(DoExpr・EffectGenerator)は型の注記にだけ使う(文字列の注記 — 実行時に評価しない)。
    from doeff import DoExpr, EffectGenerator, Program

    # ``<-`` に渡せる物: Program・effect・Result / Maybe の値。
    Bindable: TypeAlias = DoExpr | Program[Any, Any] | EffectBase[Any] | Ok[Any] | Err[Any] | Some[Any] | _NothingType
else:

    def deprecated(message: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """実行時の ``typing_extensions.deprecated`` の代わり: 型の検査は本物を読み、実行時の警告は handler が出す。"""
        return lambda function: function


_D = TypeVar("_D")


# ---------------------------------------------------------------------------
# 答えの宣言(R5)
# ---------------------------------------------------------------------------


class Outcome(Enum):
    """effect の答え 1 つの意味。"""

    SUCCESS = "success"  # 宣言の残り — <- は中身を束ねる
    ABSENT = "absent"  # <- は Absent を出す
    FAILURE = "failure"  # <- は Raise(答え) を出す
    VALUE = "value"  # 業務で普通に扱う答え(版の負けなど)— <- は中身を束ねる(失敗と宣言しない印)


def _members(answer: object) -> list[type]:
    """答えの型(型 1 つか union)を要素の型の list にする — 宣言の要素が :answer の中に在るかを照らすため。``None`` は ``NoneType``。"""
    if answer is None:
        return [NoneType]
    if isinstance(answer, UnionType) or get_origin(answer) is Union:
        return [member for arg in get_args(answer) for member in _members(arg)]
    if isinstance(answer, type):
        return [answer]
    raise TypeError(f"答えの型は型か union: {answer!r}")


def _declared_types(where: str, listed: tuple[object, ...]) -> list[type]:
    """:absent / :failure / :value の 1 つに書いた型の list(None は NoneType・重なりは TypeError)。"""
    out = [member for item in listed for member in _members(item)]
    if len(set(out)) != len(out):
        raise TypeError(f"{where} に同じ型が 2 回ある: {out!r}")
    return out


@dataclass(frozen=True)
class Outcomes:
    """effect の答えの型を 成功・不在・失敗・値 に分ける宣言(R5 — 型の名から推し量らない)。

    ``absent`` / ``failure`` / ``value`` は答えの union の要素で、互いに重ならない。残りの要素が ``success``。
    """

    effect: str
    success: tuple[type, ...]
    absent: tuple[type, ...]
    failure: tuple[type, ...]
    value: tuple[type, ...]

    @classmethod
    def declare(
        cls,
        effect: str,
        answer: object,
        absent: tuple[object, ...],
        failure: tuple[object, ...],
        value: tuple[object, ...],
    ) -> "Outcomes":
        """defeffect の :answer と :absent / :failure / :value から宣言を作る(食い違いは TypeError)。"""
        members = _members(answer)
        declared = {
            ":absent": _declared_types(f"{effect} の :absent", absent),
            ":failure": _declared_types(f"{effect} の :failure", failure),
            ":value": _declared_types(f"{effect} の :value", value),
        }
        seen: dict[type, str] = {}
        for key, types in declared.items():
            for member in types:
                if member not in members:
                    raise TypeError(
                        f"{effect} の {key} の {member.__name__} は :answer の要素ではない"
                        f"(:answer = {' | '.join(m.__name__ for m in members)})"
                    )
                if member in seen:
                    raise TypeError(f"{effect}: {member.__name__} が {seen[member]} と {key} の両方にある")
                seen[member] = key
        return cls(
            effect=effect,
            success=tuple(m for m in members if m not in seen),
            absent=tuple(declared[":absent"]),
            failure=tuple(declared[":failure"]),
            value=tuple(declared[":value"]),
        )

    @property
    def converts(self) -> bool:
        """<- が答えを変換しうるか(不在か失敗の型を 1 つでも宣言したか)。"""
        return bool(self.absent or self.failure)

    def classify(self, answer: object) -> Outcome:
        """答えの値 1 つの意味。"""
        if self.absent and isinstance(answer, self.absent):
            return Outcome.ABSENT
        if self.failure and isinstance(answer, self.failure):
            return Outcome.FAILURE
        if self.value and isinstance(answer, self.value):
            return Outcome.VALUE
        return Outcome.SUCCESS


def outcomes_of(effect_type: type) -> Outcomes | None:
    """effect の型の答えの宣言(宣言していなければ None)。"""
    declared = getattr(effect_type, "__doeff_outcomes__", None)
    if declared is None or isinstance(declared, Outcomes):
        return declared
    raise TypeError(f"{effect_type.__name__}.__doeff_outcomes__ は Outcomes: {declared!r}")


# ---------------------------------------------------------------------------
# absent-as の字面の印(R8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class AbsentAsToken:
    """absent-as の 1 回の評価の印。同一性で比べる(macro absent-as が評価のたびに作る)。"""


@dataclass(frozen=True)
class DirectBind:
    """absent-as の字面の中に直に書いた ``<-`` / ``!`` の式(macro absent-as が包む)。

    ``open_bind`` はこれを開き、出した ``Absent`` に印を付ける — absent-as はその Absent だけを既定値で再開する。
    """

    token: AbsentAsToken
    expr: object


@dataclass(frozen=True)
class BoundAbsent(Absent):
    """absent-as の字面の中の ``<-`` が値の代わりに出した Absent(印つき)。ほかの受け手には普通の Absent。"""

    token: AbsentAsToken = field(compare=False, repr=False)


@dataclass(frozen=True)
class AbsentDefault(Generic[_D]):
    """absent-as が BoundAbsent を再開する時に渡す値(印が合うことを開く側が確かめる)。"""

    token: AbsentAsToken
    value: _D


_ABSENT_RESUMED = (
    "Absent が再開された — Absent を再開できるのは absent-as だけで、それも absent-as の字面の中の <- が"
    "出した物だけ(ADR-DOE-CORE-EFFECTS-003 R8)。再開した handler: 続きを捨てるか(finish)外へ渡す(reperform)"
)
_RAISE_RESUMED = (
    "Raise が再開された — Raise は誰も再開しない(失敗したのに成功したかのように続くため・"
    "ADR-DOE-CORE-EFFECTS-003 R1)。受けた handler は finish でスコープを終えるか外へ渡す(reperform)"
)


# ---------------------------------------------------------------------------
# 開く(値 → effect)— <- と ! が yield する物(R6)
# ---------------------------------------------------------------------------


@do
def _absent(why: str, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """Absent を出す(印があれば BoundAbsent)。absent-as の再開の値だけを返し、ほかの再開は誤り。"""
    if token is None:
        yield Absent(why)
        raise RuntimeError(_ABSENT_RESUMED)
    resumed = yield BoundAbsent(why, token)
    if isinstance(resumed, AbsentDefault) and resumed.token is token:
        return resumed.value
    raise RuntimeError(_ABSENT_RESUMED)


@do
def _raise(reason: object) -> "EffectGenerator[Never]":
    """Raise(reason) を出す。再開されたら誤り。"""
    yield Raise(reason)
    raise RuntimeError(_RAISE_RESUMED)


@do
def _open_declared(effect: EffectBase, outcomes: Outcomes, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """宣言を持つ effect を出し、答えを宣言に従って開く(成功と値は中身・不在は Absent・失敗は Raise(答え))。"""
    answer = yield effect
    match outcomes.classify(answer):
        case Outcome.ABSENT:
            return (yield _absent(f"{outcomes.effect} の答えは不在: {answer!r}", token))
        case Outcome.FAILURE:
            return (yield _raise(answer))
        case Outcome.SUCCESS | Outcome.VALUE:
            return answer


@do
def _open_value(value: Ok | Err | Some | _NothingType, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """Result / Maybe の値を開く: Ok・Some → 中身、Err(理由) → Raise(理由)、Nothing → Absent。

    Err の中身が Python の例外(Try が畳んだ実装の誤り)なら、Raise にせず例外のまま上げる(R11 — 混ぜない)。
    """
    match value:
        case Ok():
            return value.value
        case Some():
            return value.value
        case Err() if isinstance(value.error, BaseException):
            raise value.error
        case Err():
            return (yield _raise(value.error))
        case _NothingType():
            return (yield _absent("Nothing を <- で開いた", token))


@do
def _perform_unresumable(effect: Absent | Raise) -> "EffectGenerator[Never]":
    """本文に直に書いた (<- (Absent …)) / (<- (Raise e)) を出す。再開されたら誤り(黙って続けない)。"""
    yield effect
    raise RuntimeError(_ABSENT_RESUMED if isinstance(effect, Absent) else _RAISE_RESUMED)


_VALUE_TYPES: frozenset[type] = frozenset({Ok, Err, Some, _NothingType})


def _settled(call: Call) -> object:
    """効果を出さない @do の呼び(本体に yield の無い関数 — defk の判断など)をその場で呼んだ答えの ``Pure``。

    VM がその呼びを走らせても、関数を呼んで答えをそのまま返すだけ(本体は何も出さない)なので、束ねる所で
    呼んでも意味は同じ — 呼ぶ時点(束ねた所)・例外の上がる位置・``:pre`` / ``:post`` の検めは変わらず、VM の往復
    だけが消える(agora-redesign #844)。本体に yield の在る関数の呼びは ``call`` そのもの(VM が走らせる)。
    yield の無い関数が生成器を返したら、VM と同じくその生成器を Program として走らせる。
    """
    definition = call.function
    if definition.yields:
        return call
    value = definition.function(*call.args, **call.kwargs)
    if type(value) is GeneratorType:
        return Expand(Pure(IRStream(value)))
    return Pure(value)


def _opened(expr: object, token: AbsentAsToken | None) -> object:
    """expr を開いた Program(開く物でなければ expr そのもの)。"""
    if isinstance(expr, Call):
        return _settled(expr)
    kind = type(expr)
    if kind is DirectBind:
        return _opened(expr.expr, expr.token)
    if kind in _VALUE_TYPES:
        return _open_value(expr, token)
    outcomes = getattr(kind, "__doeff_outcomes__", None)
    if outcomes is not None and outcomes.converts:
        return _open_declared(expr, outcomes, token)
    if isinstance(expr, (Absent, Raise)):
        return _perform_unresumable(expr)
    return expr


def _open_bind(expr: object, absent: Callable[[], object] | None = None) -> object:
    """``(<- x expr)`` / ``(! expr)`` が yield する物(doeff-hy の <- の展開の 1 点が呼ぶ ``open_bind`` の Python の道)。

    - 宣言(``__doeff_outcomes__``)を持つ effect → 答えを宣言に従って開く Program
    - Ok / Err / Some / Nothing → 開く Program
    - Absent / Raise → 出し、再開されたら誤りにする Program
    - 効果を出さない @do の呼び(本体に yield の無い関数)→ その場で呼んだ答えの ``Pure``(``_settled``)。
      doeff-hy の束ねは ``Pure`` を受けたら yield せずに ``.value`` を束ねる(VM の往復を省く — #844)。
      yield しても同じ答え(``Pure`` はその答えを返す Program)
    - それ以外(宣言の無い effect・Program)→ **expr そのもの**(今までと同じ物を yield する)
    - ``absent`` を渡すと、その 1 つの束ねを狭い受け手で包み、中で出た Absent を ``Raise(absent())`` に変える
      (``<-`` の ``:absent <失敗>``)。受け手は呼び手の本文の内側にあるので、その Raise は呼び手の境目に届く。
    """
    opened = _opened(expr, None)
    if absent is None:
        return opened
    return absent_raises(absent)(_performed(opened))


def _passes_through(kind: type) -> bool:
    """型 kind の operand を ``absent`` 無しで束ねた時、``_open_bind`` がそれをそのまま返すか(``_opened`` の振り分けのどれにも
    当たらない — 呼び・DirectBind・値の型・変換する宣言を持つ effect・Absent / Raise のどれでもない)。答えは型だけで決まるので、
    ``BindOpener`` が型ごとに覚え、その型の operand の束ねを Python の振り分けへ渡さない(card ki-5c3e73834e73)。"""
    if issubclass(kind, (Call, Absent, Raise)) or kind is DirectBind or kind in _VALUE_TYPES:
        return False
    outcomes = getattr(kind, "__doeff_outcomes__", None)
    return outcomes is None or not outcomes.converts


def _generator_program(value: GeneratorType) -> object:
    """効果を出さない @do の関数が生成器を返した時の Program(VM と同じくその生成器を走らせる — ``_settled`` と同じ形)。"""
    return Expand(Pure(IRStream(value)))


# 束ねの 1 点。熱い道(``absent`` 無しで、効果を出さない @do の呼び ``Call`` を束ねる — defk の判断)は doeff-vm の
# ``BindOpener`` が Rust の中でその場で呼んで ``Pure`` を返し、それ以外は ``_open_bind`` へ渡す(答えは同じ・
# agora-redesign #844 — 画面の 1 行で 10〜16 回束ねる判断で、Python の振り分け open_bind → _opened → _settled が
# 1 回の束ねの一番大きい残りの費用だった)。``absent`` 無しで効果を出す @do の呼び(本体に yield の在る ``Call``)も
# ``BindOpener`` がその呼びそのものを返す — ``_settled`` が呼びをそのまま返すのと同じ答え(agora-redesign #2449 —
# 預かり所の契約の例で Python の振り分けへ来た束ね 16.3k 回のうち 13.8k 回がこの形だった)。``absent`` 無しで、変換する
# 宣言を持たない effect(``_passes_through`` が真の型)を束ねる時も、``BindOpener`` が型ごとに覚えた答えでその effect
# そのものを返す(card ki-5c3e73834e73 — 記録の service の要求ごとの effect の束ねが毎回 Python の振り分けへ来ていた)。
# doeff-hy の <- の展開と effect-analyzer は、この object を名で引く。
open_bind: BindOpener = BindOpener(_open_bind, _generator_program, _passes_through)


@do
def _performed(expr: object) -> "EffectGenerator[object]":
    """expr を出して答えを返す Program(境目の handler の本文を DoExpr にするため)。"""
    return (yield expr)


# ---------------------------------------------------------------------------
# 畳む(effect → 値)— 境目の handler(R2・R7・R8)
# ---------------------------------------------------------------------------


@do
def _absent_to_nothing(effect: EffectBase, k: object) -> "EffectGenerator[object]":
    """Absent を受けたら続きを捨て、スコープを Nothing で終える。ほかは外へ渡す。"""
    if isinstance(effect, Absent):
        return Nothing
    yield Pass(effect, k)


@do
def _raise_to_err(effect: EffectBase, k: object) -> "EffectGenerator[object]":
    """Raise(理由) を受けたら続きを捨て、スコープを Err(理由) で終える。ほかは外へ渡す。"""
    if isinstance(effect, Raise):
        return Err(effect.reason)
    yield Pass(effect, k)


class BoundaryHandler(Protocol):
    """境目の handler(``maybe``・``result`` と、``on_raise(…)``・``absent_as(…)`` が返す物)の型。

    印 ``_doeff_is_handler_fn`` を持つ handler で、``h1(h2(p))`` で当て、``with_handlers`` と ``stacked_handlers`` にも置ける。
    program は ``<-`` に渡せる物なら何でもよく(宣言を持つ effect をそのまま渡しても開いてから畳む)、答えの型は包んだ
    program と変わりうる(maybe は Some / Nothing・result は Ok / Err)ので、答えの型を保つ ``ProgramHandler`` とは型を分ける。
    """

    _doeff_is_handler_fn: Literal[True] = True

    def __call__(self, program: "Bindable", /) -> "DoExpr": ...


class FormerFactoryBoundaryHandler(BoundaryHandler, Protocol):
    """設定の無い境目の handler(``maybe``・``result``)の型 — 前は引数の無い工場だった(ADR-DOE-API-002 R3)。

    program に ``maybe(p)`` で当て、列には ``maybe`` のまま置く。1 つの版の間、旧い呼び ``maybe()`` は
    DeprecationWarning を出して同じ handler を返す(型の検査も deprecated と言う — doeff.program の ``FormerFactoryHandler``
    と同じ形)。
    """

    @overload
    def __call__(self, program: "Bindable", /) -> "DoExpr": ...

    @overload
    @deprecated(
        "This handler has no settings, so it is not a handler factory: write the handler itself, "
        "without the call (ADR-DOE-API-002)"
    )
    def __call__(self) -> "FormerFactoryBoundaryHandler": ...


_NO_PROGRAM: Any = object()


def _boundary(name: str, clauses: ProgramHandler, opened: Callable[[object], object]) -> BoundaryHandler:
    """境目の handler: program を ``opened`` で開いて包み、その周りに ``clauses``(Absent か Raise を受ける節)を置く。

    印と、節の関数(``__doeff_handler_data__`` — doeff-effect-analyzer が節を読む所)は ``clauses`` の物を持つ。
    """

    def install(program: "Bindable") -> "DoExpr":
        return clauses(opened(program))

    return _marked(install, name, clauses)


def _former_factory_boundary(
    name: str, clauses: ProgramHandler, opened: Callable[[object], object], doc: str
) -> FormerFactoryBoundaryHandler:
    """``_boundary`` の、前は引数の無い工場だった handler(``maybe``・``result``)の形: program 無しの呼び ``maybe()`` は
    1 つの版の間 DeprecationWarning を出して handler そのものを返す(ADR-DOE-API-002 R3)。"""

    def install(program: "Bindable" = _NO_PROGRAM) -> object:
        if program is _NO_PROGRAM:
            warnings.warn(_called_as_former_factory(name), DeprecationWarning, stacklevel=2)
            return install
        return clauses(opened(program))

    install.__doc__ = doc
    return cast(FormerFactoryBoundaryHandler, _marked(install, name, clauses))


def _marked(install: Callable[..., object], name: str, clauses: ProgramHandler) -> BoundaryHandler:
    install.__name__ = name
    install.__qualname__ = name
    install_meta = cast(Any, install)  # 関数に印の欄を置く(型の無い欄 — doeff.program の installer と同じ置き方)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = cast(Any, clauses).__doeff_handler_data__  # handler が置く型の無い欄を読む
    return cast(BoundaryHandler, install)  # 印をすぐ上で置いた


@do
def _some_of(program: object) -> "EffectGenerator[Maybe]":
    return Some((yield open_bind(program)))


@do
def _ok_of(program: object) -> "EffectGenerator[Ok]":
    return Ok((yield open_bind(program)))


def _opened_only(program: object) -> object:
    """program を開くだけ(畳んだ答えはそのまま返す — on_raise・absent_as の本文)。"""
    return _performed(open_bind(program))


maybe: FormerFactoryBoundaryHandler = _former_factory_boundary(
    "maybe",
    _program_handler(_absent_to_nothing),
    _some_of,
    """``(maybe body)`` / ``maybe(body)`` — Absent を Nothing に、成功を Some に畳む handler(Raise は素通り)。

    設定を持たないので工場でなく handler の値(ADR-DOE-API-002 R1)。body は ``<-`` に渡せる物なら何でもよい
    (宣言を持つ effect をそのまま渡しても開いてから畳む)。旧い呼び ``maybe()`` は 1 つの版の間、
    DeprecationWarning を出して同じ handler を返す。
    """,
)

result: FormerFactoryBoundaryHandler = _former_factory_boundary(
    "result",
    _program_handler(_raise_to_err),
    _ok_of,
    """``(result body)`` / ``result(body)`` — Raise(理由) を Err(理由) に、成功を Ok に畳む handler(Absent は素通り)。

    設定を持たないので工場でなく handler の値(ADR-DOE-API-002 R1)。入れ子の順で組み合わせの型を選ぶ:
    ``result(maybe(b))`` は Ok(Some v)・Ok(Nothing)・Err(e)、``maybe(result(b))`` は Some(Ok v)・Some(Err e)・Nothing(R2)。
    旧い呼び ``result()`` は 1 つの版の間、DeprecationWarning を出して同じ handler を返す。
    """,
)


@dataclass(frozen=True)
class RaiseCase:
    """on-raise の 1 つの受け: 理由の型 ``reasons`` の Raise を ``recover`` で業務の答えへ写す。

    ``recover`` は Some(答え) か Nothing(型は合ったが形が合わない — 次の受けへ)を返す。何でも受ける型
    (object・例外の型)は置けない(R7)— macro on-raise はパターンの型からこれを作る。
    """

    reasons: tuple[type, ...]
    recover: Callable[[object], Maybe]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise TypeError("RaiseCase.reasons は空にできない")
        for reason in self.reasons:
            if not isinstance(reason, type):
                raise TypeError(f"RaiseCase.reasons の要素は型: {reason!r}")
            if reason is object or issubclass(reason, BaseException):
                raise TypeError(
                    f"on-raise は {reason.__name__} を受けられない — 何でも受ける形と Python の例外は"
                    "受けない(ADR-DOE-CORE-EFFECTS-003 R7)。失敗の型を名指す"
                )


def on_raise(*cases: RaiseCase) -> BoundaryHandler:
    """``(on-raise body パターン 写し先 …)`` の実行時 — 呼ぶと、合う受けのある Raise だけを業務の答えへ写してスコープを終える
    handler を返す(macro on-raise は ``((on_raise 受け…) body)`` に展開する)。

    合う受けの無い Raise は外へ渡す。Python の例外は受けない(Raise だけを見る)。成功はそのまま返す。
    """
    if not cases:
        raise TypeError("on-raise には受けが 1 つ以上要る")
    stray = [case for case in cases if not isinstance(case, RaiseCase)]
    if stray:
        raise TypeError(
            f"on_raise の受けは RaiseCase: {stray[0]!r} — on_raise は handler を返す工場で、program は返った handler に"
            "当てる: on_raise(受け…)(program)(ADR-DOE-API-001 R6)"
        )

    @do
    def recover_raise(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Raise):
            for case in cases:
                if isinstance(effect.reason, case.reasons):
                    chosen = case.recover(effect.reason)
                    if isinstance(chosen, Some):
                        return chosen.value
                    if chosen is not Nothing:
                        raise TypeError(f"on-raise の受けは Some か Nothing を返す: {chosen!r}")
        yield Pass(effect, k)

    return _boundary("on_raise", _program_handler(recover_raise), _opened_only)


def absent_as(default: _D, *, token: AbsentAsToken | None = None) -> BoundaryHandler:
    """``(absent-as 既定値 body)`` の実行時(R8)— 呼ぶと、中で出た Absent を既定値に畳む handler を返す
    (macro absent-as は ``((absent_as 既定値 :token 印) body)`` に展開する)。

    中で出た Absent のうち、印 ``token`` の付いた物(absent-as の字面の中の ``<-`` が値の代わりに出した物)は
    既定値で再開し、ほか(呼んだ defk の奥の Absent・直に書いた (<- (Absent …)))は再開せず既定値でスコープを終える。
    前の macro の展開(``absent_as(既定値, body, 印)``)を compile した bytecode は、doeff-hy の bytecode の見張りが
    macro の変わりを見て compile し直す。
    """

    @do
    def default_absent(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Absent):
            if token is not None and isinstance(effect, BoundAbsent) and effect.token is token:
                return (yield Transfer(k, AbsentDefault(token, default)))
            return default
        yield Pass(effect, k)

    return _boundary("absent_as", _program_handler(default_absent), _opened_only)


def absent_raises(reason: Callable[[], object]) -> ProgramHandler:
    """中で出た Absent を ``Raise(reason())`` に変える受け手(``<-`` の ``:absent`` が束ね 1 つを包む)。"""

    @do
    def absent_to_raise(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Absent):
            return (yield _raise(reason()))
        yield Pass(effect, k)

    return _program_handler(absent_to_raise)


def new_absent_token() -> AbsentAsToken:
    """absent-as の 1 回の評価の印を作る(macro absent-as の展開が呼ぶ)。"""
    return AbsentAsToken()


def direct_bind(token: AbsentAsToken, expr: object) -> DirectBind:
    """absent-as の字面の中の束ね 1 つの式を印で包む(macro absent-as の展開が呼ぶ)。"""
    return DirectBind(token, expr)

