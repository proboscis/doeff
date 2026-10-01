"""型検査のための展開(doeff_hy/static_view.py)だけが参照する型の宣言。

実行時の展開はこの module を import しない。pyright にだけ見せる。型そのものは doeff core
の物(`Expand[T, E]`・`Program[T, E]`・`EffectBase[T]` — docs/23-static-typing.md)を使い、
ここは Hy の展開の形に合わせた口だけを持つ。

- `do`: defk / defp などの関数を包む。core の `doeff.do.do` と同じ型(`Expand[T, E]`)に、
  本体に yield の無い関数(yield の無い defk は普通の関数になる)の overload を足した物。
- `_doeff_perform(e)`: `(<- x e)` の x の型 = e の答えの型(Python の `@effectful` の
  `x = perform(e)` と同じ形・docs/24-effectful-perform.md)。effect は `EffectBase[T]` の T、
  Program(defk を呼んだ結果など)は `Program[T, E]` の T。型の分からない値(`object`・Unknown)
  は Any として通す — 誤検出を出さないことを優先する。Hy には effect の集合を宣言する口が
  まだ無いので、`perform: Effects[E]` の E の突き合わせはしない。
- deftest の引数の型(`DeftestInterpreter` と pytest の組み込みの fixture の型・下の節)。
"""

from collections.abc import Callable, Generator, Mapping
from pathlib import Path
from typing import Any, Never, ParamSpec, Protocol, TypeAlias, TypeVar, overload

import pytest
from doeff_vm import Expand

from doeff import Program

_P = ParamSpec("_P")
_T = TypeVar("_T")
_E = TypeVar("_E")

@overload
def do(fn: Callable[_P, Generator[_E, Any, _T]], /) -> Callable[_P, Expand[_T, _E]]: ...
@overload
def do(fn: Callable[_P, _T], /) -> Callable[_P, Expand[_T, Never]]: ...
@overload
def do(
    *, non_tail: bool = False
) -> Callable[[Callable[_P, Generator[_E, Any, _T]]], Callable[_P, Expand[_T, _E]]]: ...
@overload
def _doeff_perform(effect: Program[_T, Any], /) -> _T: ...
@overload
def _doeff_perform(effect: object, /) -> Any: ...

# ---------------------------------------------------------------------------
# deftest の展開の引数の型(agora-redesign #2214)
# ---------------------------------------------------------------------------
# 型検査のための展開では、deftest が作る関数の引数に注記を付ける。注記の名は doeff_hy/static_view.py の
# DEFTEST_FIXTURE_TYPES(fixture の名 → ここの型の名の表・唯一の定義元)が引き、doeff-hy-check が module の頭で
# `_doeff_<型の名>` として import する。表に無い fixture(利用者が conftest で定義した物)は、書き手が `#^ T 名`
# と注記すればそれを、しなければ `object` を付ける。

class DeftestInterpreter(Protocol):
    """fixture `doeff_interpreter` — deftest の Program を走らせる関数(消費 repo の conftest が定義する)。

    展開は `(doeff_interpreter program)` か、`:env` がある時は `(doeff_interpreter program :env {…})` と呼ぶ。
    答えは展開が使わない(検の関数は None を返す)ので object。Program は答えと effect の両方で共変なので、
    `Program[object, object]` はどの Program も受ける。"""

    def __call__(
        self, program: Program[object, object], /, *, env: Mapping[str, object] = ...
    ) -> object: ...

# pytest の組み込みの fixture の型(pytest の公開の名)。
TmpPath: TypeAlias = Path
TmpPathFactory: TypeAlias = pytest.TempPathFactory
TmpdirFactory: TypeAlias = pytest.TempdirFactory
MonkeyPatch: TypeAlias = pytest.MonkeyPatch
CaptureStr: TypeAlias = pytest.CaptureFixture[str]
CaptureBytes: TypeAlias = pytest.CaptureFixture[bytes]
LogCapture: TypeAlias = pytest.LogCaptureFixture
FixtureRequest: TypeAlias = pytest.FixtureRequest
PytestConfig: TypeAlias = pytest.Config
WarningsRecorder: TypeAlias = pytest.WarningsRecorder
PytestCache: TypeAlias = pytest.Cache
Subtests: TypeAlias = pytest.Subtests
RecordProperty: TypeAlias = Callable[[str, object], None]
DoctestNamespace: TypeAlias = dict[str, object]
