"""hy.macros の型(型検査のための宣言 — 実行時は hy を読む・doeff-hy が配る部分の stub)。

Hy の `(defmacro 名 …)` は `hy.macros.macro('名')(_hy_anon_N)` に展開される。hy.macros は型の宣言を持たないので、pyright strict は
その 1 文に「macro の型が分からない」(reportUnknownMemberType)と「hy の属性に無い」(reportAttributeAccessIssue)を出していた —
defmacro を書いた .hy の file ごとに macro 1 つにつき 2 件、書き手に直せない赤(agora-controllers の controllers/shared/intent/patrols.hy の
6 件・card acp:kanban-issue:ki-5ecd80461f13)。ここは Hy の展開が出す名(macro・require)だけを宣言する — doeff と agora-controllers の
code が hy.macros から読む名はこの 2 つだけ(2026-10-10 の git grep)。ほかの名を読む code を足す時は、ここに宣言を足す。
"""

from collections.abc import Callable, Sequence
from types import ModuleType
from typing import TypeVar

_F = TypeVar("_F", bound=Callable[..., object])

#: `(defmacro 名 …)` の展開が呼ぶ decorator — 名を受け、macro の関数を登録して同じ関数(名を替えた物)を返す。
def macro(name: str) -> Callable[[_F], _F]: ...

#: `(require …)` の展開が呼ぶ macro の取り込み(doeff_hy.sexpr・検が module を指して直に呼ぶ)。target = module の名・module・名前の表
#: (dict)・None(呼んだ module)。assignments = "ALL" か "EXPORTS" か (macro の名, 別名) の組の列。答え = 実際に移した macro の
#: (新しい名, 元の名, macro の関数) の列。
def require(
    source_module: str | ModuleType,
    target: str | ModuleType | dict[str, object] | None,
    assignments: str | Sequence[Sequence[str]],
    prefix: str = "",
    target_module_name: str | None = None,
    compiler: object | None = None,
) -> list[tuple[str, str, Callable[..., object]]]: ...
