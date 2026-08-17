from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, InvestigationCompletionSloReport
from iip.adapters.memory import AllowTenantPolicy
from iip.application.investigation_dispatch import InvestigationDispatchService
from iip.application.ports import ActorContext, InvestigationCompletionSloState
from iip.application.query_investigation_completion_slo import (
    GetInvestigationCompletionSloCommand,
    InvestigationCompletionSloAuthorizationError,
    InvestigationCompletionSloObjectives,
    InvestigationCompletionSloService,
    InvestigationCompletionSloStateError,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "investigation-slo-token-0123456789abcdef0123456789abcdef"


def document(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class FixedClock:
    def __init__(self, value: str = "2026-08-17T13:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class InvestigationCompletionSloTests(unittest.TestCase):
    def test_report_matches_contract_and_uses_exact_tenant_window(self) -> None:
        expected = document("investigation-completion-slo-report.json")

        class Jobs:
            def get_investigation_completion_slo_state(self, tenant_id, **parameters):
                self.call = (tenant_id, parameters)
                return InvestigationCompletionSloState(
                    tenant_id="tenant-acme",
                    window_start="2026-08-17T12:00:00Z",
                    window_end="2026-08-17T13:00:00Z",
                    maturity_cutoff="2026-08-17T12:55:00Z",
                    accepted_jobs=102,
                    immature_jobs=2,
                    eligible_jobs=100,
                    within_objective_jobs=99,
                    late_completed_jobs=0,
                    failed_jobs=0,
                    cancelled_jobs=0,
                    unfinished_jobs=1,
                )

        jobs = Jobs()
        service = InvestigationCompletionSloService(
            jobs,
            AllowTenantPolicy(),
            FixedClock(),
        )
        actor = ActorContext("operator", "tenant-acme", ("platform-admin",))

        report = service.get(GetInvestigationCompletionSloCommand(actor)).to_dict()

        self.assertEqual(report, expected)
        self.assertEqual(
            jobs.call,
            (
                "tenant-acme",
                {
                    "window_start": "2026-08-17T12:00:00Z",
                    "window_end": "2026-08-17T13:00:00Z",
                    "maturity_cutoff": "2026-08-17T12:55:00Z",
                    "completion_objective_seconds": 300,
                },
            ),
        )
        with self.assertRaises(InvestigationCompletionSloAuthorizationError):
            service.get(
                GetInvestigationCompletionSloCommand(
                    ActorContext("viewer", "tenant-acme", ("developer",))
                )
            )

    def test_empty_low_sample_and_failure_misses_are_explicit(self) -> None:
        class Jobs:
            state = InvestigationCompletionSloState(
                tenant_id="tenant-acme",
                window_start="2026-08-17T12:00:00Z",
                window_end="2026-08-17T13:00:00Z",
                maturity_cutoff="2026-08-17T12:55:00Z",
                accepted_jobs=0,
                immature_jobs=0,
                eligible_jobs=0,
                within_objective_jobs=0,
                late_completed_jobs=0,
                failed_jobs=0,
                cancelled_jobs=0,
                unfinished_jobs=0,
            )

            def get_investigation_completion_slo_state(self, tenant_id, **parameters):
                del tenant_id, parameters
                return self.state

        jobs = Jobs()
        service = InvestigationCompletionSloService(
            jobs,
            AllowTenantPolicy(),
            FixedClock(),
        )
        actor = ActorContext("operator", "tenant-acme", ("platform-admin",))

        report = service.get(GetInvestigationCompletionSloCommand(actor)).to_dict()
        self.assertEqual(report["spec"]["status"], "no-data")
        self.assertIsNone(report["spec"]["measurement"]["attainmentBasisPoints"])
        jobs.state = InvestigationCompletionSloState(
            **{
                **jobs.state.__dict__,
                "accepted_jobs": 2,
                "eligible_jobs": 2,
                "within_objective_jobs": 1,
                "failed_jobs": 1,
            }
        )
        self.assertEqual(
            service.get(GetInvestigationCompletionSloCommand(actor)).to_dict()["spec"]["status"],
            "insufficient-data",
        )
        jobs.state = InvestigationCompletionSloState(
            **{
                **jobs.state.__dict__,
                "accepted_jobs": 20,
                "eligible_jobs": 20,
                "within_objective_jobs": 19,
                "failed_jobs": 1,
            }
        )
        report = service.get(GetInvestigationCompletionSloCommand(actor)).to_dict()
        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(report["spec"]["measurement"]["attainmentBasisPoints"], 9500)

    def test_inconsistent_or_cross_tenant_aggregate_fails_closed(self) -> None:
        class Jobs:
            def get_investigation_completion_slo_state(self, tenant_id, **parameters):
                del tenant_id
                return InvestigationCompletionSloState(
                    tenant_id="another-tenant",
                    window_start=parameters["window_start"],
                    window_end=parameters["window_end"],
                    maturity_cutoff=parameters["maturity_cutoff"],
                    accepted_jobs=1,
                    immature_jobs=0,
                    eligible_jobs=1,
                    within_objective_jobs=1,
                    late_completed_jobs=1,
                    failed_jobs=0,
                    cancelled_jobs=0,
                    unfinished_jobs=0,
                )

        service = InvestigationCompletionSloService(
            Jobs(), AllowTenantPolicy(), FixedClock()
        )
        with self.assertRaisesRegex(
            InvestigationCompletionSloStateError,
            "investigation.completion-slo.state-invalid",
        ):
            service.get(
                GetInvestigationCompletionSloCommand(
                    ActorContext("operator", "tenant-acme", ("platform-admin",))
                )
            )

    def test_in_memory_jobs_count_once_and_fast_failure_is_a_miss(self) -> None:
        objectives = InvestigationCompletionSloObjectives(
            window_seconds=300,
            maximum_completion_seconds=60,
            minimum_attainment_basis_points=6000,
            minimum_eligible_jobs=2,
        )
        runtime = build_local_runtime(
            investigation_completion_slo_objectives=objectives
        )
        actor = ActorContext("operator", "local", ("platform-admin",))
        queued_at = datetime.now(timezone.utc)
        for suffix in ("1", "2"):
            investigation_id = f"inv_{suffix * 32}"
            request = {
                "metadata": {"tenantId": "local", "id": investigation_id}
            }
            status = InvestigationDispatchService.queued_status(
                actor,
                investigation_id,
                request,
                queued_at.isoformat().replace("+00:00", "Z"),
                attempts=0,
            )
            runtime.investigation_jobs.enqueue_investigation_job(
                actor, investigation_id, request, status
            )

        claim_time = (queued_at + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        lease_time = (queued_at + timedelta(seconds=30)).isoformat().replace(
            "+00:00", "Z"
        )
        for state in ("completed", "failed"):
            claim = runtime.investigation_jobs.claim_investigation_job(
                "local", "worker", claim_time, lease_time
            )
            self.assertIsNotNone(claim)
            current = runtime.investigation_jobs.get_investigation_job(
                actor, claim.investigation_id
            )
            self.assertIsNotNone(current)
            terminal = InvestigationDispatchService.terminal_status(
                current,
                state=state,
                completed_at=(queued_at + timedelta(seconds=10)).isoformat().replace(
                    "+00:00", "Z"
                ),
                report_ref=(
                    f"investigation://{claim.investigation_id}"
                    if state == "completed"
                    else None
                ),
                error_code=("investigation.runtime.failed" if state == "failed" else None),
            )
            self.assertTrue(
                runtime.investigation_jobs.finish_investigation_job(
                    "local",
                    claim.investigation_id,
                    "worker",
                    claim.claim_token,
                    terminal,
                )
            )

        service = InvestigationCompletionSloService(
            runtime.investigation_jobs,
            AllowTenantPolicy(),
            FixedClock(
                (queued_at + timedelta(seconds=61)).isoformat().replace(
                    "+00:00", "Z"
                )
            ),
            objectives,
        )
        report = service.get(GetInvestigationCompletionSloCommand(actor)).to_dict()
        measurement = report["spec"]["measurement"]
        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(measurement["eligibleJobs"], 2)
        self.assertEqual(measurement["withinObjectiveJobs"], 1)
        self.assertEqual(measurement["failedJobs"], 1)
        self.assertEqual(measurement["attainmentBasisPoints"], 5000)

        historical_end = queued_at + timedelta(seconds=6)
        historical = runtime.investigation_jobs.get_investigation_completion_slo_state(
            "local",
            window_start=(historical_end - timedelta(seconds=300))
            .isoformat()
            .replace("+00:00", "Z"),
            window_end=historical_end.isoformat().replace("+00:00", "Z"),
            maturity_cutoff=(historical_end - timedelta(seconds=5))
            .isoformat()
            .replace("+00:00", "Z"),
            completion_objective_seconds=5,
        )
        self.assertEqual(historical.eligible_jobs, 2)
        self.assertEqual(historical.unfinished_jobs, 2)
        self.assertEqual(historical.within_objective_jobs, 0)
        self.assertEqual(historical.failed_jobs, 0)
        runtime.close()

    def test_configuration_is_bounded(self) -> None:
        for changes in (
            {"window_seconds": 299},
            {"maximum_completion_seconds": 3600},
            {"minimum_attainment_basis_points": 0},
            {"minimum_eligible_jobs": 0},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, "investigation.completion-slo.configuration.invalid"
            ):
                InvestigationCompletionSloObjectives(**changes)


class InvestigationCompletionSloHttpAndSdkTests(unittest.TestCase):
    def _handler(self, roles: tuple[str, ...]) -> ApiHandler:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", "local", roles)

        handler = object.__new__(ApiHandler)
        handler.runtime = build_local_runtime(Authenticator())
        handler.path = "/v1/operations/investigations/completion-slo"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_is_tenant_scoped_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        self.assertEqual(handler.responses[0][1]["metadata"]["tenantId"], "local")
        self.assertEqual(handler.responses[0][1]["spec"]["status"], "no-data")

        denied = self._handler(("developer",))
        denied.do_GET()
        self.assertEqual(
            denied.responses[0],
            (HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}}),
        )
        invalid = self._handler(("platform-admin",))
        invalid.path += "?tenantId=another"
        invalid.do_GET()
        self.assertEqual(invalid.responses[0][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_calls_completion_route_and_parses_contract(self) -> None:
        payload = document("investigation-completion-slo-report.json")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        client = Client("https://control.example", TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen", return_value=Response()
        ) as send:
            report = client.get_investigation_completion_slo()

        self.assertIsInstance(report, InvestigationCompletionSloReport)
        self.assertEqual(report.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/operations/investigations/completion-slo",
        )


if __name__ == "__main__":
    unittest.main()
