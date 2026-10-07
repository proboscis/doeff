# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = macros.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff_hy.pytest_items import record_function as _record_test_function

def _kw_text(x: Incomplete) -> Incomplete:
    ...

def _symbol_text(x: Incomplete) -> Incomplete:
    ...

def _test_symbol(prefix: Incomplete, name: Incomplete) -> Incomplete:
    ...

def _pairs_to_dict(forms: Incomplete) -> Incomplete:
    ...

def _expr_head_text(form: Incomplete) -> Incomplete:
    ...

def _inline_enforcement_name(form: Incomplete) -> Incomplete:
    ...

def _normalize_enforcements(items: Incomplete) -> Incomplete:
    ...

def fact(text: str, **extra: object) -> dict[str, object]:
    ...

def interpretation(text: str, **extra: object) -> dict[str, object]:
    ...

def counterexample(text: str, **extra: object) -> dict[str, object]:
    ...
