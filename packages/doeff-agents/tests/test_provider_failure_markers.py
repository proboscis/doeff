"""Provider-side failure families must be recognised from REAL captured frames.

ACP ADR 0049 R9 third revision (2026-08-12).  R9 already established that an
action-terminal (solicitation budget exhausted / prompt stall / reason-less
failed) must consult the pane's provider marker before defaulting to a
seat-attributed category — but the only family it consulted was the provider
*limit* family.  Every other provider-side failure (re-auth demanded, org
access revoked, context exhausted, transport error) fell through to
``run_failed`` / ``retryable=false``, which argus stamps as ``deterministic``
and gates on the first occurrence with no automatic re-arm (ACP ADR 0042 R4).

The load-bearing incident: ``ledger-integrity-steward`` stopped for 13 hours on
2026-08-12 after its attend ended on ``Not logged in · Please run /login``.

Measured population (agentd.sqlite terminals joined to seat transcripts,
2026-08-12): of the 19 ``turn-end without report_result`` terminals since the
possessive-family fix landed (2026-08-08), **12 were provider-side** — 11 auth,
1 context exhaustion — and 0 were missed limit-family cases.

Every fixture under ``data/provider_failure_screens/`` is a VERBATIM record:
either a ``tmux capture-pane`` tail stored by agentd in
``agent_sessions.output_snippet``, or the provider's own error text as recorded
in the seat transcript (``isApiErrorMessage: true``).  Nothing here is
synthesised — the family predicates were written against these captures, not
the other way round (the launch-ready-gate incident of 2026-08-11 showed that a
predicate chosen without real frames can be wrong on 100% of live traffic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hy  # noqa: F401  (enables the .hy import hook)

from doeff_agents.sessionhost.impls.markers import provider_failure_class

SCREENS = Path(__file__).parent / "data" / "provider_failure_screens"
READY_SCREENS = Path(__file__).parent / "data" / "ready_screens"


def _read(directory: Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8")


# (fixture, expected family, provenance)
POSITIVE_CORPUS = [
    (
        "reauth_status_line.txt",
        "reauth-required",
        "agent_inv_wi_56204a2df7e6ea47_a1 @2026-08-09 — status-line form "
        "'Not logged in · Run /login' (note: no 'Please')",
    ),
    (
        "reauth_transcript_line.txt",
        "reauth-required",
        "agent_inv_wi_b329fd82250fad42_a1 @2026-08-08 — transcript form "
        "'⎿ Not logged in · Please run /login'",
    ),
    (
        "reauth_login_expired.txt",
        "reauth-required",
        "agent_inv_wi_d1cdf52d885fb8eb_a1 @2026-07-24 — '⏺ Login expired · "
        "Please run /login' (different state noun, same remediation)",
    ),
    (
        "reauth_with_403_transport.txt",
        "reauth-required",
        "cryptic-x transcript @2026-08-10 — remediation first, transport "
        "detail second; auth outranks transport",
    ),
    (
        "access_revoked_provider_line.txt",
        "access-revoked",
        "cryptic-x transcript @2026-08-10 — org-level revocation; killed "
        "7 seats in 24h",
    ),
    (
        "context_exhausted.txt",
        "context-exhausted",
        "agent_inv_wi_96352bbc8ba58f08_a1 @2026-08-11 — "
        "'⎿ Context limit reached · /compact or /clear to continue'",
    ),
    (
        "transport_500.txt",
        "transport-failure",
        "agent_inv_wi_5902a4ded40f2b99_a1 transcript @2026-07-29",
    ),
    (
        "transport_529_overloaded.txt",
        "transport-failure",
        "agent_inv_wi_55c8b7405771a6bb_a1 transcript @2026-07-25",
    ),
    (
        "transport_connection_closed.txt",
        "transport-failure",
        "cryptic-x transcript @2026-08-10",
    ),
    (
        "transport_server_error_midresponse.txt",
        "transport-failure",
        "agent_inv_wi_5902a4ded40f2b99_a1 transcript @2026-07-29",
    ),
]


@pytest.mark.parametrize(
    "fixture,expected,provenance",
    POSITIVE_CORPUS,
    ids=[c[0] for c in POSITIVE_CORPUS],
)
def test_real_provider_failure_frames_are_classified(fixture, expected, provenance):
    got = provider_failure_class(_read(SCREENS, fixture))
    assert got == expected, f"{fixture} ({provenance}): expected {expected}, got {got}"


def test_wording_drift_within_one_family_is_covered_by_one_predicate():
    """The four re-auth captures differ in wording; one bounded family covers all.

    This is the property R9 bought with the possessive family: verbatim
    enumeration broke three times (2026-07-20 / 07-26 / 08-06) because the
    provider varies the wording around a stable core.  Here the stable core is
    the ``/login`` remediation; the state noun and the verb both vary.
    """
    reauth = [f for f, e, _ in POSITIVE_CORPUS if e == "reauth-required"]
    assert len(reauth) >= 4
    assert {provider_failure_class(_read(SCREENS, f)) for f in reauth} == {
        "reauth-required"
    }


# ---------------------------------------------------------------------------
# Negative controls — a healthy or merely-unlucky pane must never latch
# ---------------------------------------------------------------------------

NEGATIVE_READY_SCREENS = [
    "claude_screen_reader_ready.txt",
    "claude_screen_reader_trust_dialog.txt",
    "codex_login.txt",
    "codex_mcp_boot.txt",
    "codex_ready.txt",
    "codex_trust_dialog.txt",
    "codex_update_dialog.txt",
]


@pytest.mark.parametrize("fixture", NEGATIVE_READY_SCREENS)
def test_ready_screens_never_latch_a_provider_failure(fixture):
    """Real launch-stage frames — including codex's own login screen.

    ``codex_login.txt`` is the sharp one: it is a login *screen*, not a
    provider refusal, and a looser predicate ("mentions login") would
    misclassify every codex cold start as an auth failure.
    """
    assert provider_failure_class(_read(READY_SCREENS, fixture)) is None


def test_solicitation_banner_alone_is_not_a_provider_failure():
    """The exact final frame of the ledger-integrity-steward attempt.

    By terminal time the auth notice had been pushed off the tail and only the
    result-solicitation banner remained.  The predicate must NOT fire on this
    frame — which is precisely why the fact is latched at observation time
    instead of being read from the terminal snapshot.
    """
    assert provider_failure_class(_read(SCREENS, "negative_solicitation_only.txt")) is None


def test_known_limit_truncated_snippet_tail_does_not_classify():
    """Documented limit, kept as a fixture so it cannot rot silently.

    ``agent_sessions.output_snippet`` stores only a 500-character tail, so a
    wrapped announcement can lose its head.  This capture keeps only
    ``…ur admin to enable access``.  The marker window is NOT this snippet —
    it is ``tail-30 lines`` of a 100-line ``tmux capture-pane``, where the whole
    announcement is present (see ``access_revoked_provider_line.txt``).  We
    assert the truncated form does not classify rather than widening the family
    to chase a storage artefact: widening it to match ``to enable access`` is
    exactly the unbounded matching R9 (ii) forbids.
    """
    truncated = _read(SCREENS, "access_revoked_snippet_tail_truncated.txt")
    assert "organization has disabled" not in truncated
    assert provider_failure_class(truncated) is None


def test_families_are_disjoint_on_the_corpus():
    """No capture may satisfy two families — the precedence must never be load-bearing.

    Precedence exists for genuinely ambiguous frames (auth outranks transport
    when a 403 carries both), but a corpus where every entry needs the ordering
    to disambiguate would mean the families are badly cut.
    """
    seen = {}
    for fixture, expected, _ in POSITIVE_CORPUS:
        seen.setdefault(expected, []).append(fixture)
    assert set(seen) == {
        "reauth-required",
        "access-revoked",
        "context-exhausted",
        "transport-failure",
    }, "every declared family needs at least one real capture"
