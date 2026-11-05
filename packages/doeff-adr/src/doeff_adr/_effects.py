"""The effects GetAdr and AdrIds that read design records (ADR), the effects AssertAdrContract and AssertAllAdrContracts that check the shape of an ADR,
the effect RegisterDeftest that puts a deftest enforcement in the table, the effects RegisterAdr, ClearRegistry, EnforcementIds and
GetEnforcement that read and write the registry, and the factory adr_registry that returns a handler answering from the registry.

Decision: GetAdr is an effect (ADR-DOE-API-001 R20). AdrIds is also an effect
(ADR-DOE-API-001 R21). The former function get_adr was removed after the callers in outside repos were
rewritten (its body is the inner registry._get_adr). The former function adr_ids was removed after the callers in outside repos were rewritten (its body is the inner registry._adr_ids).
AssertAdrContract, AssertAllAdrContracts and RegisterDeftest were also made effects, fixing the remaining doeff-adr names in the same direction
(ADR-DOE-API-001 R30). The former functions
assert_adr_contract, assert_all_adr_contracts and register_deftest_enforcement have no callers in outside repos, so they were removed without leaving a transition layer
(their bodies are the inner registry._assert_adr_contract and registry._register_deftest_enforcement).
RegisterAdr, ClearRegistry, EnforcementIds and GetEnforcement get the same answer (ADR-DOE-API-001 R32). The former functions
register_adr, clear_registry, enforcement_ids and get_enforcement also have no callers in outside repos, so they were removed without leaving a transition layer (their bodies are
the inner registry._register_adr, _clear_registry, _enforcement_ids and _get_enforcement). The expansion of the macro defadr also runs the ADR registration
as RegisterAdr under this handler at import time.

Checking and registering read the same ADR table as GetAdr and the enforcement table, so the same handler answers (no outside process is started —
the Semgrep enforcement that starts semgrep is in another factory, semgrep_enforcements). The expansion of the macro defadr runs the deftest registration at import time and the ADR shape
check inside the generated test, under this handler (without relying on the project's doeff_interpreter fixture).

To use it with deftest, the project's doeff_interpreter fixture installs adr_registry() (doeff-adr does not choose the handler set —
see the "doeff_interpreter" section of the README). To use another table inside a test, install adr_registry(specs=[spec, …], enforcements={…}) around part of the program:
the inner handler answers first, GetAdr raises KeyError for an id not in the given list instead of falling through to the outside registry, and AdrIds returns only the ids of the given list.
"""

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import Any

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr._registry import (
    AdrSpec,
    EnforcementMode,
    EnforcementRef,
    SemgrepSpec,
    _adr_ids,
    _assert_adr_contract,
    _clear_registry,
    _enforcement_ids,
    _get_adr,
    _get_enforcement,
    _register_adr,
    _register_deftest_enforcement,
)


@dataclass(frozen=True)
class GetAdr(EffectBase[AdrSpec]):
    """Look up an ADR by id: yield GetAdr("ADR-…") → AdrSpec. An id that is not registered is KeyError(id)."""

    adr_id: str


@dataclass(frozen=True)
class AdrIds(EffectBase[list[str]]):
    """List of registered ADR ids: yield AdrIds() → an ordered list[str] ([] if none are registered)."""


@dataclass(frozen=True)
class AssertAdrContract(EffectBase[None]):
    """Check the shape of one ADR: yield AssertAdrContract("ADR-…") → None. AssertionError when an adopted ADR has no enforcement, a named enforcement is not in the table, or
    an expected-red enforcement is not tied to a plan; KeyError(id) for an id that is not registered."""

    adr_id: str


@dataclass(frozen=True)
class AssertAllAdrContracts(EffectBase[None]):
    """Check the shape of all registered ADRs: yield AssertAllAdrContracts() → None. Checks in id order and turns the first failure into the
    same AssertionError as AssertAdrContract (answers without checking anything if none are registered)."""


@dataclass(frozen=True)
class RegisterDeftest(EffectBase[EnforcementRef]):
    """Put a deftest enforcement in the table: yield RegisterDeftest("test_…") → EnforcementRef(kind="deftest"). ValueError when the same id is already in the table, or when
    mode is outside green / expected-red."""

    enforcement_id: str
    _: KW_ONLY
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class RegisterAdr(EffectBase[AdrSpec]):
    """Put an ADR in the registry: yield RegisterAdr("ADR-…", title=…, status=…, …) → the registered AdrSpec.

    The fields are the same as the expansion of the macro defadr (status = one of the words proposed, accepted, superseded, rejected; enforcement = an EnforcementRef,
    a SemgrepSpec, or a list of enforcement ids — the kind of an id is read from the enforcements of the same registry). ValueError when the same id is already registered or when status is not one of the fixed words;
    TypeError when an enforcement item has an unreadable type.
    """

    adr_id: str
    _: KW_ONLY
    title: str
    status: str
    scope: list[Any] | tuple[Any, ...] | None = None
    problem: list[Any] | tuple[Any, ...] | None = None
    context: list[Any] | tuple[Any, ...] | None = None
    decision: list[Any] | tuple[Any, ...] | None = None
    laws: list[Any] | tuple[Any, ...] | None = None
    enforcement: list[Any] | tuple[Any, ...] | None = None
    plans: list[Any] | tuple[Any, ...] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClearRegistry(EffectBase[None]):
    """Empty the registry: yield ClearRegistry() → None. Removes both the registered ADRs and the enforcements."""


@dataclass(frozen=True)
class EnforcementIds(EffectBase[list[str]]):
    """List of registered enforcement ids (defadr's deftest and defsemgrep): yield EnforcementIds() → an ordered list[str]."""


@dataclass(frozen=True)
class GetEnforcement(EffectBase[EnforcementRef | SemgrepSpec]):
    """Look up an enforcement by id: yield GetEnforcement("id") → EnforcementRef or SemgrepSpec. An id that is not registered is KeyError(id)."""

    enforcement_id: str


def _table(specs: list[AdrSpec] | tuple[AdrSpec, ...]) -> dict[str, AdrSpec]:
    """Turn the given list of ADRs into a table keyed by id (duplicate ids and non-ADR values are rejected at construction)."""
    if not isinstance(specs, list | tuple):
        raise TypeError(
            f"adr_registry(specs=…) takes a list of AdrSpec, got {type(specs).__name__}"
        )
    by_id: dict[str, AdrSpec] = {}
    for spec in specs:
        if not isinstance(spec, AdrSpec):
            raise TypeError(
                f"adr_registry(specs=…) takes AdrSpec values, got {type(spec).__name__}"
            )
        if spec.id in by_id:
            raise ValueError(f"duplicate ADR id in the table: {spec.id}")
        by_id[spec.id] = spec
    return by_id


def adr_registry(
    *,
    specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None,
    enforcements: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> ProgramHandler:
    """GetAdr・AdrIds・AssertAdrContract・AssertAllAdrContracts・RegisterDeftest・RegisterAdr・ClearRegistry・EnforcementIds・
    A handler that answers GetEnforcement.

    With neither specs nor enforcements, it reads and writes the process registry (where defadr and defsemgrep register) when answering. If either is given, it uses
    a separate registry: ADRs are read only from the specs list and enforcements only from the enforcements dict (the one not given is empty — specs=[] is also an empty table, not the
    registry). The ADR table is a copy of specs made once when the factory is called, and RegisterAdr registers into it. RegisterDeftest registers into the given dict, and
    when enforcements is not given it registers into an empty dict made when the factory is called (registrations remain while the same handler value is reused).
    If the same dict is passed to semgrep_enforcements(table=…), the RegisterSemgrep registrations are read by GetEnforcement and by the enforcement ids of RegisterAdr.
    An id not in the separate registry is KeyError instead of falling through to the outside registry, and ClearRegistry empties only the two tables of the separate registry.

    Usage: adr_registry()(program), adr_registry(specs=[spec], enforcements={})(program).
    """
    if enforcements is not None and not isinstance(enforcements, dict):
        raise TypeError(
            "adr_registry(enforcements=…) takes a dict from id to enforcement, "
            f"got {type(enforcements).__name__}"
        )
    lookup: Callable[[str], AdrSpec]
    ids: Callable[[], list[str]]
    adrs: dict[str, AdrSpec] | None
    table: dict[str, EnforcementRef | SemgrepSpec] | None
    if specs is None and enforcements is None:
        lookup, ids, adrs, table = _get_adr, _adr_ids, None, None
    else:
        by_id = _table(() if specs is None else specs)
        lookup, ids, adrs = by_id.__getitem__, lambda: sorted(by_id), by_id
        table = {} if enforcements is None else enforcements

    def write_or_read_entries(
        effect: RegisterAdr | ClearRegistry | EnforcementIds | GetEnforcement,
    ) -> AdrSpec | list[str] | EnforcementRef | SemgrepSpec | None:
        """Build the answers for registering, clearing and looking up enforcements from this handler's two tables (the enforcement ids of RegisterAdr read the kind
        from this handler's enforcement table)."""
        match effect:
            case RegisterAdr():
                return _register_adr(
                    effect.adr_id,
                    title=effect.title,
                    status=effect.status,
                    scope=effect.scope,
                    problem=effect.problem,
                    context=effect.context,
                    decision=effect.decision,
                    laws=effect.laws,
                    enforcement=effect.enforcement,
                    plans=effect.plans,
                    metadata=effect.metadata,
                    adrs=adrs,
                    table=table,
                )
            case ClearRegistry():
                return _clear_registry(adrs=adrs, table=table)
            case EnforcementIds():
                return _enforcement_ids(table)
            case GetEnforcement(enforcement_id=enforcement_id):
                return _get_enforcement(enforcement_id, table=table)

    def answer(
        effect: GetAdr
        | AdrIds
        | AssertAdrContract
        | AssertAllAdrContracts
        | RegisterDeftest
        | RegisterAdr
        | ClearRegistry
        | EnforcementIds
        | GetEnforcement,
    ) -> AdrSpec | list[str] | EnforcementRef | SemgrepSpec | None:
        """Build the answer for one effect from this handler's tables (the ADR list and the enforcement dict) — a failure is returned to the handler as an exception."""
        match effect:
            case GetAdr(adr_id=adr_id):
                return lookup(adr_id)
            case AdrIds():
                return ids()
            case AssertAdrContract(adr_id=adr_id):
                return _assert_adr_contract(lookup(adr_id), table=table)
            case AssertAllAdrContracts():
                for adr_id in ids():
                    _assert_adr_contract(lookup(adr_id), table=table)
                return None
            case RegisterDeftest(enforcement_id=enforcement_id, mode=mode):
                return _register_deftest_enforcement(enforcement_id, table=table, mode=mode)
            case RegisterAdr() | ClearRegistry() | EnforcementIds() | GetEnforcement():
                return write_or_read_entries(effect)

    @handler
    def answer_adr_registry(
        effect: GetAdr
        | AdrIds
        | AssertAdrContract
        | AssertAllAdrContracts
        | RegisterDeftest
        | RegisterAdr
        | ClearRegistry
        | EnforcementIds
        | GetEnforcement,
        k: K,
    ):
        # Fixed failures of lookup, checking and registering are thrown back to where the effect was raised (the same shape as the precedent _semgrep_effects).
        try:
            value = answer(effect)
        except (AssertionError, KeyError, TypeError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, value))

    return answer_adr_registry
