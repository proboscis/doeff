"""Types for the public surface of declarations.hy (declarations for type checking — at runtime declarations.hy is read; a child of #2324).

declarations.hy is a Hy module, so pyright cannot read inside it, and `doeff_hy._declarations.DefinitionTags` and `doeff_hy._declarations.effect_types`, which the expansion of :tags and :effects of defk / deff / defp / defhandler names, become Unknown. For definitions directly at module level
this is invisible because the type-checking expansion removes the bookkeeping (`setattr(name, '__doeff_...__', ...)`), but the bookkeeping of a deff (method) inside a class is placed in the class
body and stays, so a reportUnknownMemberType that the writer could not fix appeared as a pair (`declarations`, `DefinitionTags`) for each method on the user side.
This declares the types.

- DefinitionTags is a frozen dataclass (context, role, spells).
- effect_refusal and effect_types are ordinary functions that the head of the expanded definition calls at module load time. effect_types checks the tuple it receives and
  returns it as is.
- The remaining functions are called at macro expansion time (they receive a Hy form and return a Hy form).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeVar

from hy.models import Dict, Object, String, Symbol

_Items = TypeVar("_Items", bound=tuple[object, ...])

ROLES: tuple[str, ...]
CONTRACT_KEYS: tuple[str, ...]
FUNCTION_CONTRACT_KEYS: tuple[str, ...]
DECLARATION_KEYS: tuple[str, ...]
TAG_KEYS: tuple[str, ...]
OPTIONAL_TAG_KEYS: tuple[str, ...]
SPELLS: tuple[str, ...]
EFFECT_KEYS: tuple[str, ...]
OUTCOME_KEYS: tuple[str, ...]

@dataclass(frozen=True)
class DefinitionTags:
    """The context and role of a definition (context = the context name, role = one of ROLES, spells = the outer form to spell out or None, reads = the outer form to read into a type or None)."""

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
def record_names(form: Object, where: str, key: str) -> list[str]: ...
def needs_form(form: Object | None, where: str) -> Object: ...
def declaration_setters(name: Symbol, contract: Dict | None, where: str) -> list[Object]: ...
