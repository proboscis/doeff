"""C5 tests for attention-tier review routing."""

from __future__ import annotations

from typing import Any

import pytest
from doeff_agents.result_validation import validate_result_payload
from doeff_conductor.effects import (
    BLOCKER_FINDING,
    CALIBRATION_SAMPLE_BUDGET_KEY,
    DEFAULT_REVIEW_ROUTE_TABLE,
    REVIEW_VERDICT_RESULT_SCHEMA,
    TIER1_REVIEW_BUDGET_KEY,
    TIER2_ESCALATION_BUDGET_KEY,
    CalibrationEscapeRecord,
    CalibrationLedger,
    CalibrationPolicy,
    DefaultReviewRouter,
    DurableReviewBudget,
    OpenGate,
    OpenGateReason,
    RemainingReviewBudget,
    ReviewerAgentLost,
    ReviewEscalationReason,
    ReviewEscalationTerminal,
    ReviewFinding,
    ReviewItem,
    ReviewRoutingResult,
    ReviewSeverity,
    ReviewStakes,
    ReviewStakesLevel,
    ReviewVerdict,
    ReviewVerdictArtifact,
    ReviewVerdictTerminal,
    is_closure_terminal,
    route_review_item,
    run_review_routing_demo,
)


def _stakes(
    *,
    verification_class: str = "test-verifiable",
    level: ReviewStakesLevel = ReviewStakesLevel.NORMAL,
) -> ReviewStakes:
    return ReviewStakes(
        verification_class=verification_class,
        level=level,
        blast_radius="repository",
        reversibility="revertible",
    )


def _item(
    item_id: str,
    *,
    verification_class: str = "test-verifiable",
    level: ReviewStakesLevel = ReviewStakesLevel.NORMAL,
    lane: str = "default",
) -> ReviewItem:
    return ReviewItem(
        item_id=item_id,
        lane=lane,
        stakes=_stakes(verification_class=verification_class, level=level),
    )


def _pass() -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(verdict=ReviewVerdict.PASS, findings=())


def _major_change(title: str = "Needs fix") -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        findings=(
            ReviewFinding(
                title=title,
                severity=ReviewSeverity.MAJOR,
                detail="The implementation violates the expected behavior.",
                file="src/app.py",
            ),
        ),
    )


def _blocker() -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        findings=(
            ReviewFinding(
                title="Data loss",
                severity=ReviewSeverity.BLOCKER,
                detail="The change can delete user data.",
                file="src/app.py",
            ),
        ),
    )


def _budget(*, tier1: int = 10, tier2: int = 10, calibration: int = 10) -> DurableReviewBudget:
    return DurableReviewBudget.from_limits(
        {
            TIER1_REVIEW_BUDGET_KEY: tier1,
            TIER2_ESCALATION_BUDGET_KEY: tier2,
            CALIBRATION_SAMPLE_BUDGET_KEY: calibration,
        }
    )


def test_verdict_schema_is_importable_and_matches_agent_contract() -> None:
    valid_payload: dict[str, Any] = {
        "verdict": "CHANGES_REQUESTED",
        "findings": [
            {
                "title": "Missing guard",
                "severity": "MAJOR",
                "detail": "The check does not fail closed.",
                "file": "src/app.py",
            }
        ],
    }
    assert validate_result_payload(valid_payload, REVIEW_VERDICT_RESULT_SCHEMA) is None

    extra_transcript_payload = {
        **valid_payload,
        "raw_transcript": "this must stay out of the tier-2 path",
    }
    assert validate_result_payload(extra_transcript_payload, REVIEW_VERDICT_RESULT_SCHEMA)

    artifact = ReviewVerdictArtifact.from_dict(valid_payload)
    assert artifact.to_dict() == valid_payload
    assert artifact.blocker_findings() == ()


def test_verdict_schema_names_blocker_findings() -> None:
    artifact = ReviewVerdictArtifact(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        findings=(BLOCKER_FINDING(title="Unsafe migration", detail="Drops live rows"),),
    )

    assert artifact.to_dict()["findings"][0]["severity"] == "BLOCKER"
    assert artifact.blocker_findings()[0].title == "Unsafe migration"


def test_default_router_is_pure_table_driven_and_has_no_silent_fallback() -> None:
    router = DefaultReviewRouter()
    normal_budget = RemainingReviewBudget(tier1=3, tier2=1)
    exhausted_tier2 = RemainingReviewBudget(tier1=3, tier2=0)

    assert DEFAULT_REVIEW_ROUTE_TABLE
    assert router.route("mechanical", _stakes(verification_class="mechanical"), normal_budget) == (
        "cheap-coder"
    )
    assert router.route(
        "test-verifiable",
        _stakes(verification_class="test-verifiable"),
        normal_budget,
    ) == "cheap-coder"
    assert router.route(
        "semantic",
        _stakes(verification_class="semantic", level=ReviewStakesLevel.HIGH),
        normal_budget,
    ) == "frontier-author"
    assert router.route(
        "semantic",
        _stakes(verification_class="semantic", level=ReviewStakesLevel.HIGH),
        exhausted_tier2,
    ) == "frontier-author"

    with pytest.raises(ValueError, match="unknown verification class"):
        router.route("unclassified", _stakes(verification_class="unclassified"), normal_budget)


def test_durable_budget_is_status_keyed_and_exhaustion_opens_gate() -> None:
    item = _item("budget-exhausted")
    result = route_review_item(
        item=item,
        tier1_results=(_pass(),),
        budget=_budget(tier1=0),
    )

    assert isinstance(result.terminal, OpenGate)
    assert result.terminal.reason is OpenGateReason.BUDGET_EXHAUSTED
    assert result.terminal.stakes.verification_class == "test-verifiable"
    assert result.terminal.stakes.blast_radius == "repository"
    assert result.terminal.stakes.reversibility == "revertible"
    assert result.budget.spent_entries == ()


def test_disagreement_routes_up_even_when_majority_passes() -> None:
    result = route_review_item(
        item=_item("high-stakes-disagreement", level=ReviewStakesLevel.HIGH),
        tier1_results=(_pass(), _pass(), _major_change()),
        budget=_budget(),
    )

    assert isinstance(result.terminal, ReviewEscalationTerminal)
    assert result.terminal.request.reason is ReviewEscalationReason.DISAGREEMENT
    assert [verdict.verdict for verdict in result.terminal.request.verdicts] == [
        ReviewVerdict.PASS,
        ReviewVerdict.PASS,
        ReviewVerdict.CHANGES_REQUESTED,
    ]


def test_blocker_routes_to_tier2_with_verdicts_and_findings_only() -> None:
    tier2_requests = []
    result = route_review_item(
        item=_item("blocker"),
        tier1_results=(_blocker(),),
        budget=_budget(),
        tier2_callback=tier2_requests.append,
    )

    assert isinstance(result.terminal, ReviewEscalationTerminal)
    assert result.terminal.request.reason is ReviewEscalationReason.BLOCKER_FOUND
    assert tier2_requests == [result.terminal.request]
    assert result.terminal.request.findings[0].severity is ReviewSeverity.BLOCKER
    assert not hasattr(result.terminal.request, "raw_transcript")
    assert not hasattr(result.terminal.request, "transcript")


def test_calibration_v1_has_manual_sampling_and_records_escapes_without_adjusting_rate() -> None:
    policy = CalibrationPolicy.manual_rates({"release": 1.0})
    ledger = CalibrationLedger()
    record = CalibrationEscapeRecord(
        item_id="release-pass",
        lane="release",
        detail="Tier-2 found a missing invariant after a sampled tier-1 PASS.",
    )

    updated_ledger = ledger.record_escape(record)

    assert policy.sample_rate("release") == 1.0
    assert updated_ledger.records == (record,)
    assert policy.sample_rate("release") == 1.0


def test_closure_law_stub_scenarios_have_no_silent_terminal() -> None:
    scenarios = {
        "all-tier1-pass": route_review_item(
            item=_item("pass"),
            tier1_results=(_pass(), _pass()),
            budget=_budget(),
        ),
        "disagree": route_review_item(
            item=_item("disagree"),
            tier1_results=(_pass(), _major_change()),
            budget=_budget(),
        ),
        "blocker-found": route_review_item(
            item=_item("blocker-found"),
            tier1_results=(_blocker(),),
            budget=_budget(),
        ),
        "budget-exhausted": route_review_item(
            item=_item("budget-exhausted"),
            tier1_results=(_pass(),),
            budget=_budget(tier1=0),
        ),
        "reviewer-agent-lost": route_review_item(
            item=_item("reviewer-agent-lost"),
            tier1_results=(ReviewerAgentLost(reviewer_id="tier1-a", detail="session lost"),),
            budget=_budget(),
        ),
    }

    assert isinstance(scenarios["all-tier1-pass"].terminal, ReviewVerdictTerminal)
    assert isinstance(scenarios["disagree"].terminal, ReviewEscalationTerminal)
    assert isinstance(scenarios["blocker-found"].terminal, ReviewEscalationTerminal)
    assert isinstance(scenarios["budget-exhausted"].terminal, OpenGate)
    assert isinstance(scenarios["reviewer-agent-lost"].terminal, OpenGate)
    assert all(is_closure_terminal(result.terminal) for result in scenarios.values())


def test_review_routing_demo_only_sends_blockers_disagreements_and_samples_to_tier2() -> None:
    tier2_requests = []
    result = run_review_routing_demo(tier2_callback=tier2_requests.append)

    assert isinstance(result, ReviewRoutingResult)
    assert [request.reason for request in tier2_requests] == [
        ReviewEscalationReason.BLOCKER_FOUND,
        ReviewEscalationReason.DISAGREEMENT,
        ReviewEscalationReason.CALIBRATION_SAMPLE,
    ]
    assert [request.item.item_id for request in tier2_requests] == [
        "blocker-change",
        "disagreed-change",
        "sampled-pass-change",
    ]
    assert "plain-pass-change" not in {request.item.item_id for request in tier2_requests}
    assert all(not hasattr(request, "raw_transcript") for request in tier2_requests)
