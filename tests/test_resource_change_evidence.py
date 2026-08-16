from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.application.collect_evidence import InvalidEvidenceRequestError
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext
from iip.application.resource_change_evidence import (
    CollectResourceChangeEvidenceCommand,
    InvalidResourceChangeEvidenceRequestError,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import Client, Evidence, ResourceChangeEvidenceRequest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "change-evidence-reference-token-0123456789abcdef"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def observation(
    observed_at: datetime,
    sequence: int,
    *,
    image: str,
    replicas: int = 3,
    available_replicas: int = 3,
) -> dict:
    resource = copy.deepcopy(example("resource.json"))
    resource["metadata"]["observedAt"] = iso(observed_at)
    resource["metadata"]["observation"]["sequence"] = sequence
    resource["metadata"]["observation"]["resourceVersion"] = str(sequence)
    resource["metadata"]["observation"]["checkpoint"] = f"test:{sequence}"
    resource["spec"]["attributes"].update(
        {
            "image": image,
            "replicas": replicas,
            "availableReplicas": available_replicas,
        }
    )
    resource["status"] = {
        "health": "healthy" if available_replicas == replicas else "degraded",
        "lifecycle": "active",
    }
    return resource


def live_request(actor: ActorContext, now: datetime) -> dict:
    request = copy.deepcopy(example("resource-change-evidence-request.json"))
    request["metadata"].update(
        {
            "tenantId": actor.tenant_id,
            "actorId": actor.actor_id,
            "requestedAt": iso(now),
        }
    )
    request["spec"].update(
        {
            "timeRange": {
                "start": iso(now - timedelta(minutes=5)),
                "end": iso(now),
            },
            "deadline": iso(now + timedelta(minutes=1)),
            "query": {"changeKinds": ["image", "scale", "status"]},
        }
    )
    return request


class ResourceChangeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.runtime = build_local_runtime()
        self.now = datetime.now(timezone.utc)
        first = self.runtime.ingestion.execute(
            IngestResourceCommand(
                self.actor,
                observation(
                    self.now - timedelta(minutes=2),
                    1,
                    image="registry.example/api:1.4.0",
                ),
            )
        )
        self.resource_uid = first.identity.uid
        self.runtime.ingestion.execute(
            IngestResourceCommand(
                self.actor,
                observation(
                    self.now - timedelta(seconds=30),
                    2,
                    image="registry.example/api:1.5.0",
                    available_replicas=2,
                ),
            )
        )

    def request(self) -> dict:
        request = live_request(self.actor, self.now)
        request["spec"]["resourceRefs"] = [self.resource_uid]
        return request

    def test_history_changes_are_value_minimized_and_committed(self) -> None:
        evidence = self.runtime.resource_change_evidence.execute(
            CollectResourceChangeEvidenceCommand(self.actor, self.request())
        )

        artifact_bytes = self.runtime.evidence_store.read_artifact(
            self.actor, evidence["metadata"]["id"]
        )
        self.assertIsNotNone(artifact_bytes)
        artifact = json.loads(artifact_bytes)
        self.assertEqual(artifact["kind"], "ResourceChangeEvidenceResult")
        self.assertEqual(artifact["spec"]["status"], "complete")
        self.assertEqual(
            {item["kind"] for item in artifact["spec"]["changes"]},
            {"image", "status"},
        )
        self.assertEqual(artifact["spec"]["summary"]["changeCount"], 2)
        self.assertEqual(artifact["spec"]["summary"]["affectedResourceCount"], 1)
        self.assertNotIn(b"registry.example", artifact_bytes)
        self.assertNotIn(b"1.4.0", artifact_bytes)
        self.assertNotIn(b"1.5.0", artifact_bytes)
        self.assertEqual(evidence["spec"]["type"], "resource.change")
        self.assertEqual(evidence["spec"]["source"]["provider"], "resource-history")
        self.assertEqual(evidence["spec"]["handling"]["sensitivity"], "internal")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema("resource-change-evidence-result.schema.json"),
                artifact,
                label="resource change artifact",
            ),
            [],
        )

    def test_scan_limit_is_explicit_and_absence_is_not_reported_as_no_data(self) -> None:
        self.runtime.ingestion.execute(
            IngestResourceCommand(
                self.actor,
                observation(
                    self.now - timedelta(seconds=10),
                    3,
                    image="registry.example/api:1.6.0",
                    available_replicas=1,
                ),
            )
        )
        request = self.request()
        request["spec"]["limits"]["maxObservationsPerResource"] = 2

        evidence = self.runtime.resource_change_evidence.execute(
            CollectResourceChangeEvidenceCommand(self.actor, request)
        )
        artifact = json.loads(
            self.runtime.evidence_store.read_artifact(
                self.actor, evidence["metadata"]["id"]
            )
        )

        self.assertEqual(artifact["spec"]["status"], "partial")
        self.assertEqual(artifact["spec"]["warnings"], ["observation-limit"])

    def test_identity_time_vendor_values_and_unknown_fields_fail_closed(self) -> None:
        requests = []
        wrong_actor = self.request()
        wrong_actor["metadata"]["actorId"] = "another"
        requests.append(wrong_actor)
        reversed_time = self.request()
        reversed_time["spec"]["timeRange"]["start"] = reversed_time["spec"][
            "timeRange"
        ]["end"]
        requests.append(reversed_time)
        value_request = self.request()
        value_request["spec"]["query"]["afterImage"] = "customer/image:secret"
        requests.append(value_request)

        for request in requests:
            with self.subTest(request=request), self.assertRaisesRegex(
                InvalidResourceChangeEvidenceRequestError,
                "change.request.invalid",
            ):
                self.runtime.resource_change_evidence.execute(
                    CollectResourceChangeEvidenceCommand(self.actor, request)
                )

    def test_missing_or_cross_tenant_resource_never_reaches_history(self) -> None:
        request = self.request()
        request["spec"]["resourceRefs"] = ["res_" + "f" * 32]
        with self.assertRaisesRegex(
            InvalidEvidenceRequestError, "evidence.resource.unavailable"
        ):
            self.runtime.resource_change_evidence.execute(
                CollectResourceChangeEvidenceCommand(self.actor, request)
            )


class ResourceChangeEvidenceHttpAndSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(TOKEN),
                            "actorId": "developer",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        self.runtime = build_local_runtime(authenticator)
        self.actor = ActorContext("developer", "local")
        self.now = datetime.now(timezone.utc)
        stored = self.runtime.ingestion.execute(
            IngestResourceCommand(
                self.actor,
                observation(
                    self.now - timedelta(seconds=30),
                    1,
                    image="registry.example/api:1.5.0",
                ),
            )
        )
        self.resource_uid = stored.identity.uid

    def request(self) -> dict:
        request = live_request(self.actor, self.now)
        request["spec"]["resourceRefs"] = [self.resource_uid]
        request["spec"]["query"] = {"changeKinds": ["created"]}
        return request

    def test_authenticated_http_collection_and_stable_invalid_code(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/evidence/changes/queries"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler._read_json = self.request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        evidence = responses[0][1]
        artifact = json.loads(
            self.runtime.evidence_store.read_artifact(
                self.actor, evidence["metadata"]["id"]
            )
        )
        self.assertEqual(artifact["spec"]["status"], "complete")
        self.assertEqual(artifact["spec"]["changes"][0]["kind"], "created")

        invalid = self.request()
        invalid["metadata"]["tenantId"] = "another-tenant"
        handler._read_json = lambda: invalid
        responses.clear()
        handler.do_POST()
        self.assertEqual(
            responses,
            [(HTTPStatus.BAD_REQUEST, {"error": {"code": "change.request.invalid"}})],
        )

    def test_python_sdk_posts_public_request_and_parses_evidence(self) -> None:
        payload = example("evidence.json")

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode()

        request = ResourceChangeEvidenceRequest.from_dict(
            example("resource-change-evidence-request.json")
        )
        client = Client("https://control.example", TOKEN)
        with patch("infra_intelligence_sdk.client.urlopen", return_value=Response()) as send:
            evidence = client.collect_resource_change_evidence(request)

        self.assertIsInstance(evidence, Evidence)
        outgoing = send.call_args.args[0]
        self.assertEqual(
            outgoing.full_url, "https://control.example/v1/evidence/changes/queries"
        )
        self.assertEqual(json.loads(outgoing.data), request.to_dict())


if __name__ == "__main__":
    unittest.main()
