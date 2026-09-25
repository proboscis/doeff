"""doeff-validation の値の型 — 検査の指定(``CheckSpec``)と失敗の記録と ``ValidationException``。

``CheckSpec`` は ``validate`` に渡す値を作るだけで、単独では Program にならない(``yield`` できない)。
これが「check は validate の直下にだけ書ける」を Python の型で表したもの。

設計の記録: ``docs/design/doeff-validation/design.md``。
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckArgument:
    """検査に渡す引数 1 つ — 書いた字面と、項目の中で評価する thunk と、効果の印。

    ``perform`` が真(Hy の ``(! …)`` / Python の ``perform(…)``)なら thunk の値は Program か効果で、
    項目の中で実行した結果の値を比べる。偽なら thunk の値をそのまま比べる(Program の値でも実行しない)。
    """

    source: str
    evaluate: Callable[[], object]
    perform: bool


@dataclass(frozen=True)
class Performed:
    """Python の check で、引数を効果として項目の中で実行する印(Hy の ``(! …)`` に当たる)。"""

    program: object


@dataclass(frozen=True)
class CheckSpec:
    """検査 1 件の指定。``validate`` の直下の項目としてだけ意味を持つ。

    ``predicate`` に評価した後の引数を渡し、その値の真偽で判定する。
    """

    expression: str
    predicate: Callable[..., object]
    arguments: tuple[CheckArgument, ...]
    reason: object | None


@dataclass(frozen=True)
class EvaluatedArgument:
    """落ちた検査の引数 1 つ — 書いた字面と、評価した後の値(効果の印があれば実行した結果)。"""

    source: str
    value: object


@dataclass(frozen=True)
class CheckFailure:
    """判定が偽だった検査 1 件の記録(式の字面・評価した後の各引数の値・理由)。"""

    expression: str
    arguments: tuple[EvaluatedArgument, ...]
    reason: object | None

    def describe(self) -> str:
        """例外のメッセージの 1 行 — pytest の assert のように、式と各引数の値を並べる。"""
        values = "  ".join(f"{arg.source} = {arg.value!r}" for arg in self.arguments)
        reason = f"  [{self.reason}]" if self.reason is not None else ""
        return f"{self.expression}{'  ' + values if values else ''}{reason}"


@dataclass(frozen=True)
class CheckError:
    """引数か判定の評価が例外で落ちた検査 1 件の記録(その項目の失敗として集める)。

    ``evaluated`` は落ちる前に評価できた引数。
    """

    expression: str
    evaluated: tuple[EvaluatedArgument, ...]
    error: Exception
    reason: object | None

    def describe(self) -> str:
        """例外のメッセージの 1 行 — 式と、評価の途中で投げられた例外を並べる。"""
        values = "  ".join(f"{arg.source} = {arg.value!r}" for arg in self.evaluated)
        reason = f"  [{self.reason}]" if self.reason is not None else ""
        return (
            f"{self.expression}{'  ' + values if values else ''}"
            f"  評価が失敗: {type(self.error).__name__}: {self.error}{reason}"
        )


class ValidationException(Exception):  # noqa: N818 - operator が名指した名前(2026-09-26 "throw ValidationException")
    """``validate`` の項目のどれかが落ちた。落ちた項目の失敗を項目の順に全部持つ。

    ``failures`` の要素は ``CheckFailure``(判定が偽)・``CheckError``(評価が例外)・入れ子の
    ``ValidationException``(Program の項目の中の ``validate`` が落ちた — 1 件として数える)。空にならない。
    """

    def __init__(
        self,
        failures: "tuple[CheckFailure | CheckError | ValidationException, ...]",
        context: str | None = None,
    ) -> None:
        """``context`` はどこの検査か(契約なら ``"<関数名> pre-condition"`` など)。"""
        if not failures:
            raise ValueError("ValidationException requires at least one failure")
        self.failures = failures
        self.context = context
        super().__init__(self._message())

    @property
    def reasons(self) -> list[object | None]:
        """直下の失敗の理由の列。入れ子の失敗はその ``ValidationException`` そのもの。"""
        return [f if isinstance(f, ValidationException) else f.reason for f in self.failures]

    def _message(self) -> str:
        """全部の失敗を 1 行ずつ並べたメッセージ(入れ子は字下げして中の失敗を並べる)。"""
        where = f"{self.context}: " if self.context else ""
        lines = [f"{where}{len(self.failures)} 件の検査が落ちました:"]
        for failure in self.failures:
            match failure:
                case ValidationException():
                    nested = str(failure).replace("\n", "\n    ")
                    lines.append(f"  入れ子の validate: {nested}")
                case CheckFailure() | CheckError():
                    lines.append(f"  {failure.describe()}")
        return "\n".join(lines)


class ItemFailedError(Exception):
    """check の項目が落ちた合図(項目だけを止める)。``validate`` の中だけで使う内部の例外。"""

    def __init__(self, failure: CheckFailure | CheckError) -> None:
        super().__init__(failure.describe())
        self.failure = failure
