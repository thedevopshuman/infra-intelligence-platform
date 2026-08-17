"""Privileged rolling objective for useful asynchronous investigations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    InvestigationCompletionSloState,
    InvestigationJobRepository,
    PolicyDecisionPoint,
)


_MAX_COUNTER = 9_007_199_254_740_991


class InvestigationCompletionSloAuthorizationError(PermissionError):
    """The actor cannot inspect the tenant investigation objective."""


class InvestigationCompletionSloStateError(RuntimeError):
    """Storage returned inconsistent or unsafe completion facts."""


@dataclass(frozen=True)
class InvestigationCompletionSloObjectives:
    """Deployment-owned rolling window and useful-completion objective."""

    window_seconds: int = 3600
    maximum_completion_seconds: int = 300
    minimum_attainment_basis_points: int = 9900
    minimum_eligible_jobs: int = 20

    def __post_init__(self) -> None:
        values = (
            self.window_seconds,
            self.maximum_completion_seconds,
            self.minimum_attainment_basis_points,
            self.minimum_eligible_jobs,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.window_seconds <= 2_592_000
            or not 1 <= self.maximum_completion_seconds <= 86_400
            or self.maximum_completion_seconds >= self.window_seconds
            or not 1 <= self.minimum_attainment_basis_points <= 10_000
            or not 1 <= self.minimum_eligible_jobs <= 1_000_000
        ):
            raise ValueError("investigation.completion-slo.configuration.invalid")


@dataclass(frozen=True)
class GetInvestigationCompletionSloCommand:
    actor: ActorContext


@dataclass(frozen=True)
class InvestigationCompletionSloReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class InvestigationCompletionSloService:
    """Authorize, validate, and calculate one exact-tenant completion report."""

    def __init__(
        self,
        jobs: InvestigationJobRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
        objectives: InvestigationCompletionSloObjectives | None = None,
    ) -> None:
        self._jobs = jobs
        self._policy = policy
        self._clock = clock
        self._objectives = objectives or InvestigationCompletionSloObjectives()

    def get(
        self, command: GetInvestigationCompletionSloCommand
    ) -> InvestigationCompletionSloReport:
        if "platform-admin" not in command.actor.roles:
            raise InvestigationCompletionSloAuthorizationError(
                "investigation.completion-slo.role-required"
            )
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "windowSeconds": self._objectives.window_seconds,
            "maximumCompletionSeconds": self._objectives.maximum_completion_seconds,
            "minimumAttainmentBasisPoints": (
                self._objectives.minimum_attainment_basis_points
            ),
            "minimumEligibleJobs": self._objectives.minimum_eligible_jobs,
        }
        decision = self._policy.decide(
            command.actor,
            "investigation-completion-slo:read",
            policy_input,
        )
        if not decision.allowed:
            raise InvestigationCompletionSloAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        window_start = evaluated_at - timedelta(seconds=self._objectives.window_seconds)
        maturity_cutoff = evaluated_at - timedelta(
            seconds=self._objectives.maximum_completion_seconds
        )
        formatted_start = self._format_time(window_start)
        formatted_end = self._format_time(evaluated_at)
        formatted_cutoff = self._format_time(maturity_cutoff)
        try:
            state = self._jobs.get_investigation_completion_slo_state(
                command.actor.tenant_id,
                window_start=formatted_start,
                window_end=formatted_end,
                maturity_cutoff=formatted_cutoff,
                completion_objective_seconds=(
                    self._objectives.maximum_completion_seconds
                ),
            )
            self._validate_state(
                command,
                state,
                formatted_start,
                formatted_end,
                formatted_cutoff,
            )
        except InvestigationCompletionSloStateError:
            raise
        except Exception:
            raise InvestigationCompletionSloStateError(
                "investigation.completion-slo.state-invalid"
            ) from None

        attainment = (
            None
            if state.eligible_jobs == 0
            else state.within_objective_jobs * 10_000 // state.eligible_jobs
        )
        if state.eligible_jobs == 0:
            status = "no-data"
        elif state.eligible_jobs < self._objectives.minimum_eligible_jobs:
            status = "insufficient-data"
        elif attainment is not None and attainment >= (
            self._objectives.minimum_attainment_basis_points
        ):
            status = "meeting"
        else:
            status = "breached"

        return InvestigationCompletionSloReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "InvestigationCompletionSloReport",
                "metadata": {
                    "tenantId": command.actor.tenant_id,
                    "evaluatedAt": formatted_end,
                },
                "spec": {
                    "status": status,
                    "window": {
                        "durationSeconds": self._objectives.window_seconds,
                        "start": formatted_start,
                        "end": formatted_end,
                        "maturityCutoff": formatted_cutoff,
                    },
                    "objective": {
                        "maximumCompletionSeconds": (
                            self._objectives.maximum_completion_seconds
                        ),
                        "minimumAttainmentBasisPoints": (
                            self._objectives.minimum_attainment_basis_points
                        ),
                        "minimumEligibleJobs": self._objectives.minimum_eligible_jobs,
                    },
                    "measurement": {
                        "acceptedJobs": state.accepted_jobs,
                        "immatureJobs": state.immature_jobs,
                        "eligibleJobs": state.eligible_jobs,
                        "withinObjectiveJobs": state.within_objective_jobs,
                        "lateCompletedJobs": state.late_completed_jobs,
                        "failedJobs": state.failed_jobs,
                        "cancelledJobs": state.cancelled_jobs,
                        "unfinishedJobs": state.unfinished_jobs,
                        "attainmentBasisPoints": attainment,
                    },
                },
            }
        )

    @staticmethod
    def _validate_state(
        command: GetInvestigationCompletionSloCommand,
        state: InvestigationCompletionSloState,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
    ) -> None:
        counts = (
            state.accepted_jobs,
            state.immature_jobs,
            state.eligible_jobs,
            state.within_objective_jobs,
            state.late_completed_jobs,
            state.failed_jobs,
            state.cancelled_jobs,
            state.unfinished_jobs,
        )
        if (
            state.tenant_id != command.actor.tenant_id
            or state.window_start != window_start
            or state.window_end != window_end
            or state.maturity_cutoff != maturity_cutoff
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
                for value in counts
            )
            or state.accepted_jobs != state.immature_jobs + state.eligible_jobs
            or state.eligible_jobs
            != (
                state.within_objective_jobs
                + state.late_completed_jobs
                + state.failed_jobs
                + state.cancelled_jobs
                + state.unfinished_jobs
            )
        ):
            raise InvestigationCompletionSloStateError(
                "investigation.completion-slo.state-invalid"
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise InvestigationCompletionSloStateError(
                "investigation.completion-slo.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise InvestigationCompletionSloStateError(
                "investigation.completion-slo.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
