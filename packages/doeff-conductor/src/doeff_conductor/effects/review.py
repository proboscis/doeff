"""Attention-tier review routing primitives for conductor workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ReviewVerdict(str, Enum):
    """Structured reviewer verdict values."""

    PASS = "PASS"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ReviewSeverity(str, Enum):
    """Structured reviewer finding severities."""

    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


REVIEW_VERDICT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [ReviewVerdict.PASS.value, ReviewVerdict.CHANGES_REQUESTED.value],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "severity", "detail"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "severity": {
                        "type": "string",
                        "enum": [
                            ReviewSeverity.BLOCKER.value,
                            ReviewSeverity.MAJOR.value,
                            ReviewSeverity.MINOR.value,
                        ],
                    },
                    "detail": {"type": "string", "minLength": 1},
                    "file": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True, kw_only=True)
class ReviewFinding:
    """One structured review finding."""

    title: str
    severity: ReviewSeverity
    detail: str
    file: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ReviewFinding:
        title_value = data["title"]
        severity_value = data["severity"]
        detail_value = data["detail"]
        if not isinstance(title_value, str):
            raise TypeError("finding title must be a string")
        if not isinstance(severity_value, str):
            raise TypeError("finding severity must be a string")
        if not isinstance(detail_value, str):
            raise TypeError("finding detail must be a string")

        file_value: str | None = None
        if "file" in data:
            raw_file_value = data["file"]
            if not isinstance(raw_file_value, str):
                raise TypeError("finding file must be a string when present")
            file_value = raw_file_value

        return cls(
            title=title_value,
            severity=ReviewSeverity(severity_value),
            detail=detail_value,
            file=file_value,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "severity": self.severity.value,
            "detail": self.detail,
        }
        if self.file is not None:
            payload["file"] = self.file
        return payload


class _BlockerFindingFactory:
    def __call__(self, *, title: str, detail: str, file: str | None = None) -> ReviewFinding:
        return ReviewFinding(
            title=title,
            severity=ReviewSeverity.BLOCKER,
            detail=detail,
            file=file,
        )


BLOCKER_FINDING = _BlockerFindingFactory()


@dataclass(frozen=True, kw_only=True)
class ReviewVerdictArtifact:
    """Schema-shaped artifact returned by tier reviewers."""

    verdict: ReviewVerdict
    findings: tuple[ReviewFinding, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ReviewVerdictArtifact:
        verdict_value = data["verdict"]
        findings_value = data["findings"]
        if not isinstance(verdict_value, str):
            raise TypeError("review verdict must be a string")
        if not isinstance(findings_value, Sequence) or isinstance(findings_value, str):
            raise TypeError("review findings must be a sequence")

        findings: list[ReviewFinding] = []
        for finding_value in findings_value:
            if not isinstance(finding_value, Mapping):
                raise TypeError("review finding must be an object")
            findings.append(ReviewFinding.from_dict(finding_value))

        return cls(verdict=ReviewVerdict(verdict_value), findings=tuple(findings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def blocker_findings(self) -> tuple[ReviewFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity is ReviewSeverity.BLOCKER
        )


class ReviewStakesLevel(str, Enum):
    """Intrinsic risk level for a review item."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True, kw_only=True)
class ReviewStakes:
    """Stakes metadata carried by open gates and router calls."""

    verification_class: str
    blast_radius: str
    reversibility: str
    level: ReviewStakesLevel = ReviewStakesLevel.NORMAL

    def to_dict(self) -> dict[str, str]:
        return {
            "verification_class": self.verification_class,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "level": self.level.value,
        }


@dataclass(frozen=True, kw_only=True)
class RemainingReviewBudget:
    """Snapshot of remaining review capacity passed into router policy."""

    tier1: int
    tier2: int


class ReviewRouter(Protocol):
    """Pure injected router interface."""

    def route(
        self,
        verification_class: str,
        stakes: ReviewStakes,
        remaining_budget: RemainingReviewBudget,
    ) -> str:
        """Return the semantic profile name for the task."""
        ...


@dataclass(frozen=True, kw_only=True)
class ReviewRouteRule:
    """One default router table row."""

    verification_class: str
    profile_name: str
    stakes_level: ReviewStakesLevel | None = None

    def matches(self, verification_class: str, stakes: ReviewStakes) -> bool:
        class_matches = self.verification_class == verification_class
        level_matches = self.stakes_level is None or self.stakes_level is stakes.level
        return class_matches and level_matches


DEFAULT_REVIEW_ROUTE_TABLE: tuple[ReviewRouteRule, ...] = (
    ReviewRouteRule(
        verification_class="semantic",
        stakes_level=ReviewStakesLevel.HIGH,
        profile_name="frontier-author",
    ),
    ReviewRouteRule(verification_class="semantic", profile_name="frontier-reviewer"),
    ReviewRouteRule(verification_class="review", profile_name="cheap-reviewer"),
    ReviewRouteRule(verification_class="test-verifiable", profile_name="cheap-coder"),
    ReviewRouteRule(verification_class="mechanical", profile_name="cheap-coder"),
)


@dataclass(frozen=True, kw_only=True)
class DefaultReviewRouter:
    """Default pure router backed by a static policy table."""

    route_table: tuple[ReviewRouteRule, ...] = DEFAULT_REVIEW_ROUTE_TABLE

    def route(
        self,
        verification_class: str,
        stakes: ReviewStakes,
        remaining_budget: RemainingReviewBudget,
    ) -> str:
        if remaining_budget.tier1 < 0 or remaining_budget.tier2 < 0:
            raise ValueError("remaining review budget cannot be negative")

        known_classes = {rule.verification_class for rule in self.route_table}
        if verification_class not in known_classes:
            raise ValueError(f"unknown verification class: {verification_class}")

        for rule in self.route_table:
            if rule.matches(verification_class, stakes):
                return rule.profile_name

        raise ValueError(f"no route rule matched verification class: {verification_class}")


class ReviewTier(str, Enum):
    """Review budget tiers."""

    TIER_1 = "tier-1"
    TIER_2 = "tier-2"


class ReviewBudgetStatus(str, Enum):
    """Durable budget counter status keys."""

    TIER1_REVIEW = "tier1-review"
    TIER2_ESCALATION = "tier2-escalation"
    CALIBRATION_SAMPLE = "calibration-sample"


@dataclass(frozen=True, kw_only=True)
class BudgetCounterKey:
    """Status-keyed durable budget key."""

    tier: ReviewTier
    status: ReviewBudgetStatus


@dataclass(frozen=True, kw_only=True)
class BudgetCounterEntry:
    """Immutable budget counter entry."""

    key: BudgetCounterKey
    units: int


TIER1_REVIEW_BUDGET_KEY = BudgetCounterKey(
    tier=ReviewTier.TIER_1,
    status=ReviewBudgetStatus.TIER1_REVIEW,
)
TIER2_ESCALATION_BUDGET_KEY = BudgetCounterKey(
    tier=ReviewTier.TIER_2,
    status=ReviewBudgetStatus.TIER2_ESCALATION,
)
CALIBRATION_SAMPLE_BUDGET_KEY = BudgetCounterKey(
    tier=ReviewTier.TIER_2,
    status=ReviewBudgetStatus.CALIBRATION_SAMPLE,
)


@dataclass(frozen=True, kw_only=True)
class BudgetConsumption:
    """Result of attempting to consume a durable budget counter."""

    consumed: bool
    budget: DurableReviewBudget


@dataclass(frozen=True, kw_only=True)
class DurableReviewBudget:
    """Per-tier durable counters keyed by review status, not work item liveness."""

    limit_entries: tuple[BudgetCounterEntry, ...]
    spent_entries: tuple[BudgetCounterEntry, ...] = ()

    @classmethod
    def from_limits(cls, limits: Mapping[BudgetCounterKey, int]) -> DurableReviewBudget:
        entries = tuple(
            BudgetCounterEntry(key=key, units=units)
            for key, units in sorted(
                limits.items(),
                key=lambda item: (item[0].tier.value, item[0].status.value),
            )
        )
        for entry in entries:
            if entry.units < 0:
                raise ValueError("budget limits cannot be negative")
        return cls(limit_entries=entries)

    def limit_for(self, key: BudgetCounterKey) -> int:
        for entry in self.limit_entries:
            if entry.key == key:
                return entry.units
        return 0

    def spent_for(self, key: BudgetCounterKey) -> int:
        for entry in self.spent_entries:
            if entry.key == key:
                return entry.units
        return 0

    def remaining(self, key: BudgetCounterKey) -> int:
        return self.limit_for(key) - self.spent_for(key)

    def try_consume(self, key: BudgetCounterKey, units: int = 1) -> BudgetConsumption:
        if units < 0:
            raise ValueError("budget consumption units cannot be negative")
        if self.remaining(key) < units:
            return BudgetConsumption(consumed=False, budget=self)
        return BudgetConsumption(consumed=True, budget=self._with_spent(key, units))

    def _with_spent(self, key: BudgetCounterKey, units: int) -> DurableReviewBudget:
        updated_entries: list[BudgetCounterEntry] = []
        replaced = False
        for entry in self.spent_entries:
            if entry.key == key:
                updated_entries.append(BudgetCounterEntry(key=key, units=entry.units + units))
                replaced = True
            else:
                updated_entries.append(entry)
        if not replaced:
            updated_entries.append(BudgetCounterEntry(key=key, units=units))
        return DurableReviewBudget(
            limit_entries=self.limit_entries,
            spent_entries=tuple(updated_entries),
        )


@dataclass(frozen=True, kw_only=True)
class CalibrationLaneRate:
    """Manual calibration sampling rate for one lane."""

    lane: str
    sample_rate: float


@dataclass(frozen=True, kw_only=True)
class CalibrationPolicy:
    """Manual calibration sampling policy with no autonomous adjustment."""

    lane_rates: tuple[CalibrationLaneRate, ...] = ()

    @classmethod
    def disabled(cls) -> CalibrationPolicy:
        return cls()

    @classmethod
    def manual_rates(cls, rates: Mapping[str, float]) -> CalibrationPolicy:
        lane_rates: list[CalibrationLaneRate] = []
        for lane, sample_rate in sorted(rates.items()):
            if sample_rate < 0.0 or sample_rate > 1.0:
                raise ValueError("calibration sample rates must be between 0.0 and 1.0")
            lane_rates.append(CalibrationLaneRate(lane=lane, sample_rate=sample_rate))
        return cls(lane_rates=tuple(lane_rates))

    def sample_rate(self, lane: str) -> float:
        for lane_rate in self.lane_rates:
            if lane_rate.lane == lane:
                return lane_rate.sample_rate
        return 0.0

    def should_sample(self, *, item_id: str, lane: str) -> bool:
        sample_rate = self.sample_rate(lane)
        if sample_rate <= 0.0:
            return False
        if sample_rate >= 1.0:
            return True
        digest = hashlib.sha256(f"{lane}:{item_id}".encode()).digest()
        threshold = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        return threshold < sample_rate


@dataclass(frozen=True, kw_only=True)
class CalibrationEscapeRecord:
    """Recorded calibration escape; rate adjustment is deliberately deferred."""

    item_id: str
    lane: str
    detail: str


@dataclass(frozen=True, kw_only=True)
class CalibrationLedger:
    """Append-only calibration escape ledger."""

    records: tuple[CalibrationEscapeRecord, ...] = ()

    def record_escape(self, record: CalibrationEscapeRecord) -> CalibrationLedger:
        return CalibrationLedger(records=(*self.records, record))


@dataclass(frozen=True, kw_only=True)
class ReviewItem:
    """One item entering attention-tier review routing."""

    item_id: str
    lane: str
    stakes: ReviewStakes


@dataclass(frozen=True, kw_only=True)
class ReviewerAgentLost:
    """Typed tier-1 reviewer loss result."""

    reviewer_id: str
    detail: str


class ReviewEscalationReason(str, Enum):
    """Reasons that tier-1 review routes upward."""

    BLOCKER_FOUND = "blocker-found"
    DISAGREEMENT = "disagreement"
    CALIBRATION_SAMPLE = "calibration-sample"


class OpenGateReason(str, Enum):
    """Reasons review routing opens an overseer gate."""

    BUDGET_EXHAUSTED = "budget-exhausted"
    REVIEWER_AGENT_LOST = "reviewer-agent-lost"


@dataclass(frozen=True, kw_only=True)
class Tier2ReviewRequest:
    """Tier-2 callback payload containing only verdicts and findings."""

    item: ReviewItem
    reason: ReviewEscalationReason
    verdicts: tuple[ReviewVerdictArtifact, ...]
    findings: tuple[ReviewFinding, ...]


@dataclass(frozen=True, kw_only=True)
class GateOption:
    """Closure-preserving gate option."""

    name: str
    outcome: str


@dataclass(frozen=True, kw_only=True)
class OpenGate:
    """Open overseer gate with stakes metadata."""

    gate_id: str
    reason: OpenGateReason
    stakes: ReviewStakes
    detail: str
    options: tuple[GateOption, ...]


@dataclass(frozen=True, kw_only=True)
class ReviewVerdictTerminal:
    """Review flow terminal containing a verdict."""

    item: ReviewItem
    verdict: ReviewVerdict
    findings: tuple[ReviewFinding, ...]


@dataclass(frozen=True, kw_only=True)
class ReviewEscalationTerminal:
    """Review flow terminal containing an escalation."""

    request: Tier2ReviewRequest


ClosureTerminal = ReviewVerdictTerminal | ReviewEscalationTerminal | OpenGate
Tier1ReviewResult = ReviewVerdictArtifact | ReviewerAgentLost
Tier2Callback = Callable[[Tier2ReviewRequest], object]


@dataclass(frozen=True, kw_only=True)
class ReviewRoutingResult:
    """Result of one review-routing decision."""

    terminal: ClosureTerminal
    budget: DurableReviewBudget
    calibration_ledger: CalibrationLedger = field(default_factory=CalibrationLedger)


@dataclass(frozen=True, kw_only=True)
class _AcceptedTier1Results:
    """Validated tier-1 results after budget and liveness checks."""

    budget: DurableReviewBudget
    verdicts: tuple[ReviewVerdictArtifact, ...]
    findings: tuple[ReviewFinding, ...]


def route_review_item(
    *,
    item: ReviewItem,
    tier1_results: Sequence[Tier1ReviewResult],
    budget: DurableReviewBudget,
    calibration_policy: CalibrationPolicy | None = None,
    calibration_ledger: CalibrationLedger | None = None,
    tier2_callback: Tier2Callback | None = None,
) -> ReviewRoutingResult:
    """Route tier-1 review results into verdict, escalation, or open gate."""

    active_policy = calibration_policy or CalibrationPolicy.disabled()
    active_ledger = calibration_ledger or CalibrationLedger()

    accepted = _accept_tier1_results(
        item=item,
        tier1_results=tier1_results,
        budget=budget,
        calibration_ledger=active_ledger,
    )
    if isinstance(accepted, ReviewRoutingResult):
        return accepted
    return _route_accepted_verdicts(
        item=item,
        accepted=accepted,
        calibration_policy=active_policy,
        calibration_ledger=active_ledger,
        tier2_callback=tier2_callback,
    )


def _accept_tier1_results(
    *,
    item: ReviewItem,
    tier1_results: Sequence[Tier1ReviewResult],
    budget: DurableReviewBudget,
    calibration_ledger: CalibrationLedger,
) -> ReviewRoutingResult | _AcceptedTier1Results:
    if not tier1_results:
        return _open_gate_result(
            item=item,
            budget=budget,
            calibration_ledger=calibration_ledger,
            reason=OpenGateReason.REVIEWER_AGENT_LOST,
            detail="no tier-1 reviewer result was produced",
        )

    tier1_consumption = budget.try_consume(TIER1_REVIEW_BUDGET_KEY, len(tier1_results))
    if not tier1_consumption.consumed:
        return _open_gate_result(
            item=item,
            budget=budget,
            calibration_ledger=calibration_ledger,
            reason=OpenGateReason.BUDGET_EXHAUSTED,
            detail="tier-1 review budget exhausted",
        )

    lost_reviewers = tuple(
        result for result in tier1_results if isinstance(result, ReviewerAgentLost)
    )
    if lost_reviewers:
        lost_ids = ", ".join(result.reviewer_id for result in lost_reviewers)
        return _open_gate_result(
            item=item,
            budget=tier1_consumption.budget,
            calibration_ledger=calibration_ledger,
            reason=OpenGateReason.REVIEWER_AGENT_LOST,
            detail=f"tier-1 reviewer result missing: {lost_ids}",
        )

    verdicts = tuple(
        result for result in tier1_results if isinstance(result, ReviewVerdictArtifact)
    )
    return _AcceptedTier1Results(
        budget=tier1_consumption.budget,
        verdicts=verdicts,
        findings=_collect_findings(verdicts),
    )


def _route_accepted_verdicts(
    *,
    item: ReviewItem,
    accepted: _AcceptedTier1Results,
    calibration_policy: CalibrationPolicy,
    calibration_ledger: CalibrationLedger,
    tier2_callback: Tier2Callback | None,
) -> ReviewRoutingResult:
    blocker_findings = tuple(
        finding for finding in accepted.findings if finding.severity is ReviewSeverity.BLOCKER
    )
    if blocker_findings:
        return _escalation_result(
            item=item,
            verdicts=accepted.verdicts,
            findings=blocker_findings,
            budget=accepted.budget,
            calibration_ledger=calibration_ledger,
            reason=ReviewEscalationReason.BLOCKER_FOUND,
            budget_key=TIER2_ESCALATION_BUDGET_KEY,
            tier2_callback=tier2_callback,
        )

    if _verdicts_disagree(accepted.verdicts):
        return _escalation_result(
            item=item,
            verdicts=accepted.verdicts,
            findings=accepted.findings,
            budget=accepted.budget,
            calibration_ledger=calibration_ledger,
            reason=ReviewEscalationReason.DISAGREEMENT,
            budget_key=TIER2_ESCALATION_BUDGET_KEY,
            tier2_callback=tier2_callback,
        )

    if calibration_policy.should_sample(item_id=item.item_id, lane=item.lane):
        return _escalation_result(
            item=item,
            verdicts=accepted.verdicts,
            findings=accepted.findings,
            budget=accepted.budget,
            calibration_ledger=calibration_ledger,
            reason=ReviewEscalationReason.CALIBRATION_SAMPLE,
            budget_key=CALIBRATION_SAMPLE_BUDGET_KEY,
            tier2_callback=tier2_callback,
        )

    return ReviewRoutingResult(
        terminal=ReviewVerdictTerminal(
            item=item,
            verdict=accepted.verdicts[0].verdict,
            findings=accepted.findings,
        ),
        budget=accepted.budget,
        calibration_ledger=calibration_ledger,
    )


def is_closure_terminal(terminal: object) -> bool:
    return isinstance(terminal, ReviewVerdictTerminal | ReviewEscalationTerminal | OpenGate)


def run_review_routing_demo(tier2_callback: Tier2Callback | None = None) -> ReviewRoutingResult:
    """Run the C5 review-routing demo with deterministic scenario stubs."""

    budget = DurableReviewBudget.from_limits(
        {
            TIER1_REVIEW_BUDGET_KEY: 20,
            TIER2_ESCALATION_BUDGET_KEY: 20,
            CALIBRATION_SAMPLE_BUDGET_KEY: 20,
        }
    )
    calibration_policy = CalibrationPolicy.manual_rates({"default": 0.0, "sampled": 1.0})
    cases: tuple[tuple[ReviewItem, tuple[Tier1ReviewResult, ...]], ...] = (
        (
            _demo_item("plain-pass-change", lane="default"),
            (_demo_pass(), _demo_pass()),
        ),
        (
            _demo_item("blocker-change", lane="default"),
            (_demo_blocker(),),
        ),
        (
            _demo_item("disagreed-change", lane="default"),
            (_demo_pass(), _demo_major_change()),
        ),
        (
            _demo_item("sampled-pass-change", lane="sampled"),
            (_demo_pass(), _demo_pass()),
        ),
    )

    final_result: ReviewRoutingResult | None = None
    active_budget = budget
    for item, tier1_results in cases:
        final_result = route_review_item(
            item=item,
            tier1_results=tier1_results,
            budget=active_budget,
            calibration_policy=calibration_policy,
            tier2_callback=tier2_callback,
        )
        active_budget = final_result.budget

    if final_result is None:
        raise AssertionError("review routing demo has no scenarios")
    return final_result


def _collect_findings(verdicts: Sequence[ReviewVerdictArtifact]) -> tuple[ReviewFinding, ...]:
    return tuple(finding for verdict in verdicts for finding in verdict.findings)


def _verdicts_disagree(verdicts: Sequence[ReviewVerdictArtifact]) -> bool:
    if not verdicts:
        return False
    first_verdict = verdicts[0].verdict
    return any(verdict.verdict is not first_verdict for verdict in verdicts[1:])


def _escalation_result(
    *,
    item: ReviewItem,
    verdicts: tuple[ReviewVerdictArtifact, ...],
    findings: tuple[ReviewFinding, ...],
    budget: DurableReviewBudget,
    calibration_ledger: CalibrationLedger,
    reason: ReviewEscalationReason,
    budget_key: BudgetCounterKey,
    tier2_callback: Tier2Callback | None,
) -> ReviewRoutingResult:
    budget_consumption = budget.try_consume(budget_key)
    if not budget_consumption.consumed:
        return _open_gate_result(
            item=item,
            budget=budget,
            calibration_ledger=calibration_ledger,
            reason=OpenGateReason.BUDGET_EXHAUSTED,
            detail=f"{reason.value} tier-2 budget exhausted",
        )

    request = Tier2ReviewRequest(
        item=item,
        reason=reason,
        verdicts=verdicts,
        findings=findings,
    )
    if tier2_callback is not None:
        tier2_callback(request)
    return ReviewRoutingResult(
        terminal=ReviewEscalationTerminal(request=request),
        budget=budget_consumption.budget,
        calibration_ledger=calibration_ledger,
    )


def _open_gate_result(
    *,
    item: ReviewItem,
    budget: DurableReviewBudget,
    calibration_ledger: CalibrationLedger,
    reason: OpenGateReason,
    detail: str,
) -> ReviewRoutingResult:
    gate = OpenGate(
        gate_id=f"{item.item_id}:{reason.value}",
        reason=reason,
        stakes=item.stakes,
        detail=detail,
        options=(
            GateOption(
                name="proceed",
                outcome="resume the blocked review step after the condition is cleared",
            ),
            GateOption(
                name="redirect",
                outcome="resume after the overseer edits workflow state or budget",
            ),
            GateOption(name="abort", outcome="abort the dependent review subtree"),
        ),
    )
    return ReviewRoutingResult(
        terminal=gate,
        budget=budget,
        calibration_ledger=calibration_ledger,
    )


def _demo_item(item_id: str, *, lane: str) -> ReviewItem:
    return ReviewItem(
        item_id=item_id,
        lane=lane,
        stakes=ReviewStakes(
            verification_class="test-verifiable",
            level=ReviewStakesLevel.HIGH,
            blast_radius="repository",
            reversibility="revertible",
        ),
    )


def _demo_pass() -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(verdict=ReviewVerdict.PASS, findings=())


def _demo_major_change() -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        findings=(
            ReviewFinding(
                title="Missing assertion",
                severity=ReviewSeverity.MAJOR,
                detail="The implementation lacks an assertion for the main invariant.",
                file="tests/test_feature.py",
            ),
        ),
    )


def _demo_blocker() -> ReviewVerdictArtifact:
    return ReviewVerdictArtifact(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        findings=(
            ReviewFinding(
                title="Unsafe migration",
                severity=ReviewSeverity.BLOCKER,
                detail="The migration can drop production data.",
                file="migrations/001.py",
            ),
        ),
    )
