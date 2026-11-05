"""The effect RegisterSemgrep that puts a Semgrep check in the table, the effect AssertSemgrep that runs semgrep and checks that sample hits and misses are right, and
the factory semgrep_enforcements that returns a handler answering both.

Decision: make both RegisterSemgrep and AssertSemgrep effects
(ADR-DOE-API-001 R22). The bodies moved to the inner registry._register_semgrep_enforcement and registry._assert_semgrep_enforcement.
The former functions register_semgrep_enforcement and assert_semgrep_enforcement were removed after rewriting callers in other repos to effects
(agora-controllers cef8e6e88a).

The handler is kept separate from the GetAdr adr_registry: AssertSemgrep starts an external process (semgrep), so the semgrep execution is not mixed into the
handler set of a program that only reads ADRs. The expansion of the macro defsemgrep runs its own registration and check under this handler (it does not rely on the project's
doeff_interpreter fixture -- defsemgrep in another repo works just by bumping the doeff version). When a test uses a different table or a different
semgrep, install semgrep_enforcements(table=..., semgrep=...) around part of the program: the innermost handler answers first.
"""

from dataclasses import KW_ONLY, dataclass
from os import PathLike, fspath

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr._registry import (
    EnforcementMode,
    EnforcementRef,
    FixtureSpec,
    SemgrepSpec,
    _assert_semgrep_enforcement,
    _register_semgrep_enforcement,
)


@dataclass(frozen=True)
class RegisterSemgrep(EffectBase[SemgrepSpec]):
    """Register a Semgrep check in the table: yield RegisterSemgrep("id", rule_id=..., hit_fixtures=[...], clean_fixtures=[...]) -> SemgrepSpec.

    The installed rule form is rule_id plus the samples that hit (hit_fixtures) and the samples that do not hit (clean_fixtures); the in-place rule form is pattern plus
    bad and good. The fields are the same as the defsemgrep expansion (declared_in = the declaring file -- a relative config is read from that dir). ValueError if the same id is already in the
    table, or if there is neither pattern nor rule_id.
    """

    enforcement_id: str
    _: KW_ONLY
    pattern: str | None = None
    rule_id: str | None = None
    config: str = ".semgrep.yaml"
    declared_in: str | None = None
    expand_hy: bool = False
    languages: list[str] | tuple[str, ...] | None = None
    message: str = "ADR Semgrep enforcement failed"
    severity: str = "ERROR"
    bad: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None
    good: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None
    hit_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None
    clean_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class AssertSemgrep(EffectBase[None]):
    """Check a Semgrep check in the table: yield AssertSemgrep("id") -> None. Runs semgrep and answers if the rule hits at least one hitting sample and
    hits no non-hitting sample. AssertionError on a mismatch, a missing semgrep, or a run that did not execute; KeyError for an id not in the table."""

    enforcement_id: str


def semgrep_enforcements(
    *,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
    semgrep: str | PathLike[str] | None = None,
) -> ProgramHandler:
    """Handler that answers RegisterSemgrep and AssertSemgrep. The semgrep process is started when this handler answers AssertSemgrep.

    If table is not given, register into and read from the process registry (where defadr and defsemgrep register); if table is given, register into and read from that dict
    (an id not in the given dict raises KeyError instead of falling through to the outer registry). If semgrep is not given, semgrep on PATH is looked up when answering; if given,
    that executable is started (a nonexistent path is an AssertionError).

    Usage: semgrep_enforcements()(program), semgrep_enforcements(table={}, semgrep="/path/to/semgrep")(program).
    """
    if table is not None and not isinstance(table, dict):
        raise TypeError(
            f"semgrep_enforcements(table=…) takes a dict from id to enforcement, got {type(table).__name__}"
        )
    executable = None if semgrep is None else fspath(semgrep)

    @handler
    def answer_semgrep(effect: RegisterSemgrep | AssertSemgrep, k: K):
        # Throw deterministic registration and check failures back to the site that raised the effect (same shape as GetAdr in the precedent _effects; finding 5 of the structure review).
        try:
            if isinstance(effect, RegisterSemgrep):
                answer: SemgrepSpec | None = _register_semgrep_enforcement(
                    effect.enforcement_id,
                    table=table,
                    pattern=effect.pattern,
                    rule_id=effect.rule_id,
                    config=effect.config,
                    declared_in=effect.declared_in,
                    expand_hy=effect.expand_hy,
                    languages=effect.languages,
                    message=effect.message,
                    severity=effect.severity,
                    bad=effect.bad,
                    good=effect.good,
                    hit_fixtures=effect.hit_fixtures,
                    clean_fixtures=effect.clean_fixtures,
                    mode=effect.mode,
                )
            else:
                answer = _assert_semgrep_enforcement(
                    effect.enforcement_id, table=table, semgrep=executable
                )
        except (AssertionError, KeyError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, answer))

    return answer_semgrep
