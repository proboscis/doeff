"""The check that the expansion of defrecord's :check calls from __post_init__ (read by both the runtime and the type-check expansions).

It used to be a deff in doeff_hy.record, but doeff_hy.record is the boundary of the macros that Hy's require reads by path, and the only names it exposes are macros,
so it lives in this inner module (ADR-DOE-API-001 R14). Because this is a Python module, pyright reads the call in the type-check expansion
without a stub. It is a leaf module that imports no library either — modules inside doeff_hy also use defrecord, so
what the expansion reads must not carry an import of doeff_hy (same reason as doeff_hy._record_runtime).
"""


def require_check(
    record: str, fields: tuple[str, ...], check: str, passed: bool, values: dict[str, object]
) -> None:
    """Check the result of one :check expression of a defrecord. If false, raise ValueError naming which field of which type failed which check, with the field values
    (an expression that returned a Program has already been turned into a TypeError inside the expansion). It is called from the dataclass __post_init__, so it is not
    a defk that returns a Program."""
    if not passed:
        shown = " ".join(f"{k}={v!r}" for k, v in values.items())
        noun = "field" if len(fields) == 1 else "fields"
        raise ValueError(f"{record}: {noun} {', '.join(fields)} failed check {check}: {shown}")
