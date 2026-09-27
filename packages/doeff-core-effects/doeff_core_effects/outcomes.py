"""不在と失敗の実行時(ADR-DOE-CORE-EFFECTS-003)— effect の答えの宣言、``<-`` が出す開き、境目の handler。

語彙:

- ``Absent`` / ``Raise``(``doeff_core_effects.effects``)— 想定内の不在(Maybe の側)と失敗(Result の側)。
- ``Outcomes`` — effect の答えの型を 成功・不在・失敗・値 に分ける宣言。``defeffect`` の頭の辞書の
  ``:absent`` / ``:failure`` / ``:value`` から作られ、effect の型の属性 ``__doeff_outcomes__`` に残る(R5)。
- ``open_bind`` — ``(<- x e)`` と ``(! e)`` が yield する物を返す。宣言を持つ effect は不在の答えを ``Absent`` に、
  失敗の答えを ``Raise(答え)`` に変えて呼び手のスコープで出す Program に、Result / Maybe の値は開く Program に
  する。それ以外(宣言の無い effect・Program)は **受け取った物をそのまま** 返す — 今までと同じ物を yield する(R6)。
- 境目の handler(effect → 値に畳む): ``maybe``・``result``・``on_raise``・``absent_as``(R2・R7・R8)。
  続きを再開しない(スコープをその値で終える)。再開してよいのは ``absent_as`` が、自分の字面の中の ``<-`` が出した
  ``Absent`` を再開する時だけ。

handler は値で答え、変換は呼び手の本文の ``<-`` で行う(R4)— handler の節の中で ``Absent`` / ``Raise`` を出すと、
その handler より外へ行き、呼び手の ``maybe`` / ``result`` を飛び越えるため。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Generic, Never, TypeAlias, TypeVar, Union, get_args, get_origin

from doeff_vm import EffectBase, Err, Ok

from doeff.do import do
from doeff.program import Pass, ProgramHandler, Transfer
from doeff.program import handler as _program_handler
from doeff.result import Maybe, Nothing, Some, _NothingType
from doeff_core_effects.effects import Absent, Raise

if TYPE_CHECKING:
    # doeff の最上位は doeff_core_effects.effects を読み込む途中でこの module に来るので、最上位の後半で定義される
    # 名前(DoExpr・EffectGenerator)は型の注記にだけ使う(文字列の注記 — 実行時に評価しない)。
    from doeff import DoExpr, EffectGenerator

    # ``<-`` に渡せる物: Program・effect・Result / Maybe の値。
    Bindable: TypeAlias = DoExpr | EffectBase | Ok | Err | Some | _NothingType

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


def _opened(expr: object, token: AbsentAsToken | None) -> object:
    """expr を開いた Program(開く物でなければ expr そのもの)。"""
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


def open_bind(expr: object, absent: Callable[[], object] | None = None) -> object:
    """``(<- x expr)`` / ``(! expr)`` が yield する物(doeff-hy の <- の展開の 1 点が呼ぶ)。

    - 宣言(``__doeff_outcomes__``)を持つ effect → 答えを宣言に従って開く Program
    - Ok / Err / Some / Nothing → 開く Program
    - Absent / Raise → 出し、再開されたら誤りにする Program
    - それ以外(宣言の無い effect・Program)→ **expr そのもの**(今までと同じ物を yield する)
    - ``absent`` を渡すと、その 1 つの束ねを狭い受け手で包み、中で出た Absent を ``Raise(absent())`` に変える
      (``<-`` の ``:absent <失敗>``)。受け手は呼び手の本文の内側にあるので、その Raise は呼び手の境目に届く。
    """
    opened = _opened(expr, None)
    if absent is None:
        return opened
    return absent_raises(absent)(_performed(opened))


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


_absent_to_nothing_handler: ProgramHandler = _program_handler(_absent_to_nothing)
_raise_to_err_handler: ProgramHandler = _program_handler(_raise_to_err)


@do
def _some_of(program: object) -> "EffectGenerator[Maybe]":
    return Some((yield open_bind(program)))


@do
def _ok_of(program: object) -> "EffectGenerator[Ok]":
    return Ok((yield open_bind(program)))


def maybe(program: "Bindable") -> "DoExpr":
    """``(maybe body)`` — Absent を Nothing に、成功を Some に畳む(Raise は素通り)。

    body は ``<-`` に渡せる物なら何でもよい(宣言を持つ effect をそのまま渡しても開いてから畳む)。
    """
    return _absent_to_nothing_handler(_some_of(program))


def result(program: "Bindable") -> "DoExpr":
    """``(result body)`` — Raise(理由) を Err(理由) に、成功を Ok に畳む(Absent は素通り)。

    入れ子の順で組み合わせの型を選ぶ: ``result(maybe(b))`` は Ok(Some v)・Ok(Nothing)・Err(e)、
    ``maybe(result(b))`` は Some(Ok v)・Some(Err e)・Nothing(R2)。
    """
    return _raise_to_err_handler(_ok_of(program))


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


def on_raise(program: "Bindable", *cases: RaiseCase) -> "DoExpr":
    """``(on-raise body パターン 写し先 …)`` の実行時 — 合う受けのある Raise だけを業務の答えへ写してスコープを終える。

    合う受けの無い Raise は外へ渡す。Python の例外は受けない(Raise だけを見る)。成功はそのまま返す。
    """
    if not cases:
        raise TypeError("on-raise には受けが 1 つ以上要る")

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

    return _program_handler(recover_raise)(_performed(open_bind(program)))


def absent_as(default: _D, program: "Bindable", token: AbsentAsToken | None = None) -> "DoExpr":
    """``(absent-as 既定値 body)`` の実行時(R8)。

    中で出た Absent のうち、印 ``token`` の付いた物(absent-as の字面の中の ``<-`` が値の代わりに出した物)は
    既定値で再開し、ほか(呼んだ defk の奥の Absent・直に書いた (<- (Absent …)))は再開せず既定値でスコープを終える。
    """

    @do
    def default_absent(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Absent):
            if token is not None and isinstance(effect, BoundAbsent) and effect.token is token:
                return (yield Transfer(k, AbsentDefault(token, default)))
            return default
        yield Pass(effect, k)

    return _program_handler(default_absent)(_performed(open_bind(program)))


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

