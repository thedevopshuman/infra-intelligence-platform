"""Protected catalog configuration, freezing, authority, and planning tests."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from iip.adapters.investigation_catalog import (
    InvestigationSignalCatalogConfigurationError,
    StaticInvestigationSignalCatalog,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.investigate import (
    InvestigationCatalogConfigurationError,
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime


ROOT = Path(__file__).resolve().parents[1]


def document(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def local_catalog() -> dict[str, object]:
    catalog = document("investigation-signal-catalog.json")
    profile = catalog["profiles"][0]
    profile["tenantId"] = "local"
    profile["profileId"] = "local-kubernetes"
    profile["selections"] = {
        "changeSelections": profile["selections"]["changeSelections"]
    }
    return catalog


def request(resource_uid: str, suffix: str) -> dict[str, object]:
    payload = document("investigation-request.json")
    payload["metadata"]["id"] = "inv_" + suffix * 32
    payload["spec"]["scope"]["resourceUids"] = [resource_uid]
    payload["spec"]["evidenceTypes"] = [
        "kubernetes.resource-status",
        "resource.change",
    ]
    payload["spec"]["allowedTools"] = [
        "resources/query",
        "evidence/fetch",
    ]
    return payload


class InvestigationSignalCatalogAdapterTests(unittest.TestCase):
    def test_example_resolves_only_the_exact_tenant_and_returns_copies(self) -> None:
        catalog = StaticInvestigationSignalCatalog.from_json(
            json.dumps(local_catalog())
        )

        first = catalog.get_profile("local")
        self.assertIsNotNone(first)
        first["profileId"] = "changed"  # type: ignore[index]
        self.assertEqual(
            catalog.get_profile("local")["profileId"],  # type: ignore[index]
            "local-kubernetes",
        )
        self.assertIsNone(catalog.get_profile("another-tenant"))

    def test_duplicate_tenant_and_closed_configuration_fail_at_startup(self) -> None:
        catalog = local_catalog()
        catalog["profiles"].append(copy.deepcopy(catalog["profiles"][0]))
        with self.assertRaisesRegex(
            InvestigationSignalCatalogConfigurationError,
            "investigation.catalog.configuration.invalid",
        ):
            StaticInvestigationSignalCatalog.from_json(json.dumps(catalog))

        for raw in ("{}", '{"profiles":[]}', "not-json", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(InvestigationSignalCatalogConfigurationError):
                    StaticInvestigationSignalCatalog.from_json(raw)


class InvestigationSignalCatalogServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local", ("developer",))
        self.catalog = StaticInvestigationSignalCatalog.from_json(
            json.dumps(local_catalog())
        )
        self.runtime = build_local_runtime(signal_catalog=self.catalog)
        resource = self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, document("resource.json"))
        )
        self.resource_uid = resource.identity.uid

    def tearDown(self) -> None:
        self.runtime.close()

    def test_catalog_candidates_are_frozen_and_report_their_origin(self) -> None:
        raw = request(self.resource_uid, "a")
        prepared = self.runtime.investigations.validate_for_dispatch(
            RunInvestigationCommand(self.actor, raw)
        )
        snapshot = prepared["spec"]["catalogSnapshot"]

        self.assertEqual(snapshot["strategy"], "protected-catalog-v1")
        self.assertEqual(snapshot["profileId"], "local-kubernetes")
        self.assertEqual(
            snapshot["generatedSelections"],
            [
                {
                    "signal": "resource.change",
                    "selectionId": "cqs_6a28c9f31db44ea2",
                }
            ],
        )
        report = self.runtime.investigations.execute_prepared(
            RunInvestigationCommand(self.actor, prepared)
        )
        plan = report["spec"]["signalPlan"]
        self.assertEqual(plan["catalog"]["snapshotDigest"], snapshot["snapshotDigest"])
        self.assertEqual(plan["steps"][0]["origin"], "protected-catalog")
        self.assertEqual(plan["steps"][0]["decision"], "scheduled")

    def test_explicit_signal_candidates_win_without_mixing_trust_sources(self) -> None:
        raw = request(self.resource_uid, "b")
        explicit = copy.deepcopy(
            local_catalog()["profiles"][0]["selections"]["changeSelections"][0]
        )
        explicit["id"] = "cqs_1111111111111111"
        raw["spec"]["changeSelections"] = [explicit]

        prepared = self.runtime.investigations.validate_for_dispatch(
            RunInvestigationCommand(self.actor, raw)
        )

        self.assertNotIn("catalogSnapshot", prepared["spec"])
        self.assertEqual(
            prepared["spec"]["changeSelections"][0]["id"],
            "cqs_1111111111111111",
        )

    def test_request_bounds_still_defer_catalog_candidates(self) -> None:
        raw = request(self.resource_uid, "c")
        raw["spec"]["evidenceTypes"] = ["kubernetes.resource-status"]
        report = self.runtime.investigations.execute(
            RunInvestigationCommand(self.actor, raw)
        )

        step = report["spec"]["signalPlan"]["steps"][0]
        self.assertEqual(step["origin"], "protected-catalog")
        self.assertEqual(step["decision"], "deferred")
        self.assertEqual(step["reason"], "request-upper-bound")

    def test_forged_or_modified_snapshot_fails_before_execution(self) -> None:
        raw = request(self.resource_uid, "d")
        prepared = self.runtime.investigations.validate_for_dispatch(
            RunInvestigationCommand(self.actor, raw)
        )

        with self.assertRaisesRegex(
            InvalidInvestigationError,
            "investigation.catalog.snapshot-forbidden",
        ):
            self.runtime.investigations.execute(
                RunInvestigationCommand(self.actor, prepared)
            )

        modified = copy.deepcopy(prepared)
        modified["spec"]["changeSelections"][0]["limits"]["maxChanges"] = 99
        with self.assertRaisesRegex(
            InvalidInvestigationError,
            "investigation.catalog.snapshot-invalid",
        ):
            self.runtime.investigations.execute_prepared(
                RunInvestigationCommand(self.actor, modified)
            )

    def test_semantically_invalid_profile_fails_when_runtime_composes(self) -> None:
        catalog = local_catalog()
        selection = catalog["profiles"][0]["selections"]["changeSelections"][0]
        selection["limits"]["maxChanges"] = 0
        adapter = StaticInvestigationSignalCatalog.from_json(json.dumps(catalog))

        with self.assertRaisesRegex(
            InvestigationCatalogConfigurationError,
            "investigation.catalog.configuration.invalid",
        ):
            build_local_runtime(signal_catalog=adapter)


if __name__ == "__main__":
    unittest.main()
