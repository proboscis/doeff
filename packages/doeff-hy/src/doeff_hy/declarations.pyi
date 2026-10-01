"""declarations.hy の公開面の型(型検査のための宣言 — 実行時は declarations.hy を読む・agora-redesign #2335・#2324 の子)。

declarations.hy は Hy の module なので、pyright は中を読めず、defk / deff / defp / defhandler の :tags・:effects の展開が名指す
`doeff_hy.declarations.DefinitionTags`・`doeff_hy.declarations.effect_types` が Unknown になる。module の直下の定義では
型検査の展開が記帳(`setattr(名, '__doeff_…__', …)`)を外すので見えないが、class の中の deff(method)の記帳は class の
本体に置かれて残り、使う側の method ごとに書き手に直せない reportUnknownMemberType が 1 組(`declarations`・`DefinitionTags`)
出ていた。ここで型を宣言する。

- DefinitionTags は frozen の dataclass(context・role・spells)。
- effect_refusal・effect_types は展開した定義の頭が module の読み込みの時に呼ぶ普通の関数。effect_types は受けた tuple を
  検めてそのまま返す。
- 残りの関数は macro の展開の時に呼ぶ(Hy の form を受けて Hy の form を返す)。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeVar

from hy.models import Dict, Object, String, Symbol

_Items = TypeVar("_Items", bound=tuple[object, ...])

ROLES: tuple[str, ...]
CONTRACT_KEYS: tuple[str, ...]
DECLARATION_KEYS: tuple[str, ...]
TAG_KEYS: tuple[str, ...]
OPTIONAL_TAG_KEYS: tuple[str, ...]
SPELLS: tuple[str, ...]
EFFECT_KEYS: tuple[str, ...]
OUTCOME_KEYS: tuple[str, ...]

@dataclass(frozen=True)
class DefinitionTags:
    """定義の文脈と役(context = 文脈の名・role = ROLES の 1 つ・spells = 綴る外の形か None・reads = 型へ読む外の形か None)。"""

    context: str
    role: str
    spells: str | None = None
    reads: str | None = None

def refuse_unknown_keys(contract: Dict, allowed: tuple[str, ...], where: str) -> None: ...
def field_targets(forms: Iterable[Object]) -> list[Symbol]: ...
def declared_value(contract: Dict, key: str) -> Object | None: ...
def effects_form(form: Object | None, where: str) -> Object: ...
def effect_refusal(item: object) -> str | None: ...
def effect_types(where: str, items: _Items) -> _Items: ...
def tags_form(form: Object | None, where: str) -> Object: ...
def outcome_type_forms(contract: Dict, answer: Object | None, where: str) -> dict[str, list[Object]]: ...
def effect_field_forms(form: Object | None, where: str) -> tuple[list[Object], list[str]]: ...
def runs_carried_names(form: Object | None, names: list[str], where: str) -> list[str]: ...
def answer_value_form(answer: Object) -> Object: ...
def defeffect_form(
    name: Symbol, docstring: String | None, contract: Dict, where: str, pre_code: list[Object]
) -> Object: ...
def needs_names(form: Object, where: str) -> list[str]: ...
def needs_form(form: Object | None, where: str) -> Object: ...
def declaration_setters(name: Symbol, contract: Dict | None, where: str) -> list[Object]: ...
