"""doeff-validation の利用者向けの関数 — ``validate`` と ``check``。

``validate`` は doeff-traverse の上の薄い層: 項目の列を ``Traverse`` で走らせ(逐次か並行か
fail-fast かは組み立ての根に置いた traverse の handler が決める)、要素ごとの分離で項目ごとの失敗を
集める。Hy では ``(require doeff-hy.macros [defk validate check])`` で同じ意味のマクロを使う。
"""

from collections.abc import Callable
from types import BuiltinFunctionType, FunctionType, MethodType
from typing import TYPE_CHECKING

from doeff_traverse import Inspect, Traverse

from doeff import DoExpr, EffectBase, EffectGenerator, do
from doeff_validation.failures import (
    CheckArgument,
    CheckError,
    CheckFailure,
    CheckSpec,
    EvaluatedArgument,
    ItemFailedError,
    Performed,
    ValidationException,
)

if TYPE_CHECKING:
    from doeff import Program


def _is_program(value: object) -> bool:
    """値が Program か効果か(validate の項目の確かめと、判定の値の取り違えの検出に使う)。"""
    return isinstance(value, (DoExpr, EffectBase))


def perform(program: "Program[object] | EffectBase") -> Performed:
    """check の引数を、効果として項目の中で実行する印を付ける(Hy の ``(! …)`` に当たる)。

    ``check(operator.eq, perform(CountSeats(node)), 0)`` — 印の無い引数は、Program の値でも実行せず
    そのまま比べる。
    """
    if not _is_program(program):
        raise TypeError(f"perform: Program か効果を渡してください — {type(program).__name__}")
    return Performed(program)


def _callable_name(fn: Callable[..., object]) -> str:
    """Python の check の式の字面に使う、述語の名前。"""
    if isinstance(fn, (FunctionType, BuiltinFunctionType, MethodType)):
        return fn.__name__
    return repr(fn)


def _python_argument(value: object) -> CheckArgument:
    """Python の check の引数を CheckArgument にする(``perform`` の印があれば効果として実行する)。"""
    match value:
        case Performed(program=program):
            return CheckArgument(f"perform({program!r})", lambda: program, True)
        case _:
            return CheckArgument(repr(value), lambda: value, False)


def check(
    predicate: Callable[..., object],
    *args: object,
    reason: object | None = None,
    expression: str | None = None,
) -> CheckSpec:
    """検査 1 件の指定を作る。``validate`` の引数としてだけ意味を持つ(単独では Program にならない)。

    ``check(operator.eq, job.phase, Phase.PENDING, reason=R)`` — 各引数は普通の値か、
    ``perform(program)`` の印を付けた効果。印の付いた引数は項目の中で実行し、結果の値を
    ``predicate`` に渡す。
    """
    if not callable(predicate):
        raise TypeError("check: 第 1 引数には述語(callable)を渡してください — check(operator.eq, a, b)")
    return CheckSpec(
        expression=expression
        or f"{_callable_name(predicate)}({', '.join(repr(a) for a in args)})",
        predicate=predicate,
        arguments=tuple(_python_argument(a) for a in args),
        reason=reason,
    )


@do
def _evaluate(argument: CheckArgument) -> EffectGenerator[object]:
    """引数 1 つを項目の中で評価する(効果の印があれば実行して結果の値にする)。"""
    value = argument.evaluate()
    if argument.perform:
        return (yield value)
    return value


def judge(verdict: object) -> bool:
    """判定の値を真偽にする。Program か効果なら取り違え(印の付け忘れ)として TypeError を投げる。

    validate の項目と、defk / do! の契約の check(macros.hy の contract-check-block)の共通の 1 点。
    Program の値は常に真なので、そのまま真偽にすると検査が黙って通ってしまう。
    """
    if _is_program(verdict):
        raise TypeError(
            f"check: 判定の値が Program です({verdict!r})— 効果の判定なら (! …) / perform(…) の"
            " 印を付けた引数にしてください"
        )
    return bool(verdict)


@do
def _run_check(spec: CheckSpec) -> EffectGenerator[None]:
    """check の項目 1 つを走らせる: 引数を左から評価し(Program は実行)、判定し、落ちたら項目を止める。"""
    evaluated: list[EvaluatedArgument] = []
    try:
        for argument in spec.arguments:
            value = yield _evaluate(argument)
            evaluated.append(EvaluatedArgument(argument.source, value))
        verdict = judge(spec.predicate(*(arg.value for arg in evaluated)))
    except ItemFailedError:
        raise
    except Exception as error:  # 評価の失敗はこの項目の失敗として集める(捨てない — 下で記録して投げ直す)
        raise ItemFailedError(
            CheckError(spec.expression, tuple(evaluated), error, spec.reason)
        ) from error
    if not verdict:
        raise ItemFailedError(CheckFailure(spec.expression, tuple(evaluated), spec.reason))


def _as_program(item: object) -> object:
    """Traverse の f — 項目を 1 要素の処理にする(check は検査の Program に、Program はそのまま)。"""
    match item:
        case CheckSpec():
            return _run_check(item)
        case _:
            return item


@do
def _validate(items: tuple[object, ...]) -> EffectGenerator[None]:
    """項目を Traverse で走らせ、要素ごとの結果から失敗を集め、1 つでもあれば例外を投げる。"""
    try:
        collection = yield Traverse(_as_program, items, label="validate")
    except ItemFailedError as failed:
        # fail-fast の traverse の handler(parallel_fail_fast)は最初の失敗をそのまま投げる。
        raise ValidationException((failed.failure,)) from None
    except ValidationException as nested:
        raise ValidationException((nested,)) from None
    results = yield Inspect(collection)
    failures: list[CheckFailure | CheckError | ValidationException] = []
    for result in results:
        if not result.failed:
            continue
        match result.value:
            case ItemFailedError(failure=failure):
                failures.append(failure)
            case ValidationException() as nested:
                failures.append(nested)
            case BaseException() as abnormal:
                # Program の項目の中の、検査の失敗ではない異常(プログラムの誤り・続けられない失敗)は
                # 集めずにそのまま投げる。
                raise abnormal
            case other:
                raise TypeError(
                    f"validate: item {result.index} failed with a non-exception value {other!r}"
                )
    if failures:
        raise ValidationException(tuple(failures))


def validate(*items: "CheckSpec | Program[object]") -> "Program[None]":
    """独立した項目を全部走らせ、落ちた項目の失敗を全部集める。

    - 項目は ``check`` の指定か任意の Program。互いの結果に依らない(並行に走らせてよい)。
    - check の項目: 引数を左から評価して(効果の印の付いた引数は実行して)判定し、偽なら
      ``CheckFailure``、評価が例外なら ``CheckError``。
    - Program の項目: 中で投げられた ``ValidationException`` を 1 件の失敗として受ける
      (検査のまとまりの使い回しは、helper の中に自分の ``validate`` を持たせる)。
    - 失敗が 1 つでもあれば ``ValidationException`` を投げる。無ければ ``None``(「確かめて、だめなら
      投げる」文なので値は返さない。値が要る処理は validate の前か後に書く)。
    - 逐次か並行か fail-fast かは、組み立ての根に置いた doeff-traverse の handler で選ぶ
      (``sequential()`` / ``parallel(n)`` / ``parallel_fail_fast(n)``)。
    """
    for item in items:
        if not isinstance(item, CheckSpec) and not _is_program(item):
            raise TypeError(
                f"validate: 項目は check か Program です — {type(item).__name__} が渡されました"
            )
    return _validate(items)
