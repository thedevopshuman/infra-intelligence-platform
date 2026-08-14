"""Deterministic evaluation scorecard for investigation reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class EvaluationRun:
    scenario_id: str
    scenario_version: str
    score: int
    passed: bool
    gates: Mapping[str, bool]
    components: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "scenarioId": self.scenario_id,
            "scenarioVersion": self.scenario_version,
            "score": self.score,
            "passed": self.passed,
            "gates": dict(self.gates),
            "components": dict(self.components),
        }


def score_report(
    scenario: Mapping[str, Any],
    report: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
) -> EvaluationRun:
    metadata = scenario["metadata"]
    scenario_spec = scenario["spec"]
    expectations = scenario_spec["expectations"]
    scoring = scenario_spec["scoring"]
    report_spec = report["spec"]
    hypotheses = report_spec["hypotheses"]
    cited = set(report_spec["evidenceIds"])
    leading_classes = {
        item.get("rootCauseClass")
        for item in hypotheses
        if item.get("disposition") == "leading"
    }
    required = set(expectations["requiredEvidenceIds"])
    red_herrings = set(expectations["redHerringEvidenceIds"])
    cited_types = {
        document["spec"]["type"]
        for evidence_id, document in evidence.items()
        if evidence_id in cited
    }
    forbidden_types = set(expectations["forbiddenEvidenceTypes"])
    request_budgets = scenario_spec["request"]["spec"]["budgets"]
    usage = report_spec["usage"]
    budget_ok = all(
        usage[usage_name] <= request_budgets[budget_name]
        for usage_name, budget_name in (
            ("toolCalls", "maxToolCalls"),
            ("modelTokens", "maxModelTokens"),
            ("wallTimeSeconds", "maxWallTimeSeconds"),
            ("costUsd", "maxCostUsd"),
            ("evidenceItems", "maxEvidenceItems"),
            ("iterations", "maxIterations"),
        )
    )
    supported = all(
        item.get("disposition") != "leading" or bool(item.get("supportingEvidenceIds"))
        for item in hypotheses
    )
    components = {
        "rootCause": float(expectations["rootCauseClass"] in leading_classes),
        "requiredEvidence": len(required.intersection(cited)) / len(required),
        "forbiddenEvidence": float(not forbidden_types.intersection(cited_types)),
        "redHerringResistance": float(not red_herrings.intersection(cited)),
        "unsupportedCertainty": float(supported),
        "budgetCompliance": float(budget_ok),
    }
    gates = {
        "root-cause": components["rootCause"] == 1,
        "required-evidence": components["requiredEvidence"] == 1,
        "forbidden-evidence": components["forbiddenEvidence"] == 1,
        "budget": components["budgetCompliance"] == 1,
    }
    score = round(
        sum(components[name] * weight for name, weight in scoring["weights"].items())
    )
    return EvaluationRun(
        scenario_id=metadata["id"],
        scenario_version=metadata["version"],
        score=score,
        passed=(
            all(gates[name] for name in scoring["hardGates"])
            and score >= scoring["passScore"]
        ),
        gates=gates,
        components=components,
    )


def run_repeated(
    scenario: Mapping[str, Any],
    runner: Callable[
        [Mapping[str, Any]],
        tuple[Mapping[str, Any], Mapping[str, Mapping[str, Any]]],
    ],
    *,
    repetitions: int = 3,
) -> tuple[EvaluationRun, ...]:
    if not 1 <= repetitions <= 100:
        raise ValueError("evaluation repetitions must be between 1 and 100")
    return tuple(score_report(scenario, *runner(scenario)) for _ in range(repetitions))
