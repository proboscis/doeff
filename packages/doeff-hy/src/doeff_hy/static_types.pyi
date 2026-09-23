"""型検査のための展開(doeff_hy/static_view.py)だけが参照する型の宣言。

実行時の展開はこの module を import しない。pyright にだけ見せる。型そのものは doeff core
の物(`Expand[T, E]`・`Program[T, E]`・`EffectBase[T]` — docs/23-static-typing.md)を使い、
ここは Hy の展開の形に合わせた口だけを持つ。

- `do`: defk / defp などの関数を包む。core の `doeff.do.do` と同じ型(`Expand[T, E]`)に、
  本体に yield の無い関数(yield の無い defk は普通の関数になる)の overload を足した物。
- `_doeff_bound(e, sent)`: `(<- x e)` の x の型 = e の答えの型。effect は `EffectBase[T]` の T、
  Program(defk を呼んだ結果など)は `Program[T, E]` の T。型の分からない値(`object`・Unknown)
  は Any として通す — 誤検出を出さないことを優先する。
"""

from collections.abc import Callable, Generator
from typing import Any, Never, ParamSpec, TypeVar, overload

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
def _doeff_bound(program: Program[_T, Any], sent: object, /) -> _T: ...
@overload
def _doeff_bound(program: object, sent: object, /) -> Any: ...
