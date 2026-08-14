from __future__ import annotations

import copy
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import BearerIdentity, HashedBearerAuthenticator
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ingest_resource import (
    IngestResourceCommand,
    ObservationConflictError,
    ResourceIngestionService,
    StaleObservationError,
)
from iip.application.ports import ActorContext, AuthenticationError
from iip.application.query_resources import (
    InvalidCursorError,
    InvalidQueryError,
    QueryAuthorizationError,
    ResourceNotFoundError,
    ResourceQueryService,
)
from iip.bootstrap import build_local_runtime
from iip.domain.models import Resource
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import Client


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOKEN = "local-reference-token-0000000000000001"
OTHER_TOKEN = "other-reference-token-0000000000000001"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def resource_payload(
    name: str,
    sequence: int,
    *,
    resource_type: str = "apps/deployment",
    relationships: list[dict] | None = None,
    tenant_id: str = "local",
) -> dict:
    payload = json.loads(
        (ROOT / "contracts" / "examples" / "resource.json").read_text(encoding="utf-8")
    )
    payload["metadata"]["tenantId"] = tenant_id
    payload["metadata"]["observation"]["sequence"] = sequence
    payload["metadata"]["observation"]["resourceVersion"] = str(400000 + sequence)
    payload["metadata"]["observation"].pop("checkpoint", None)
    payload["spec"]["type"] = resource_type
    payload["spec"]["externalId"] = f"cluster-local/default/{name}"
    payload["spec"]["displayName"] = name
    payload["spec"]["relationships"] = relationships or []
    return payload


class ResourceQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        policy = AllowTenantPolicy()
        self.ingestion = ResourceIngestionService(self.store, policy)
        self.queries = ResourceQueryService(self.store, policy)
        self.actor = ActorContext("developer", "local")

        self.config = Resource.from_dict(
            resource_payload("settings", 1, resource_type="core/configmap")
        )
        self.service = Resource.from_dict(
            resource_payload("service", 2, resource_type="core/service")
        )
        self.root_without_edges = Resource.from_dict(resource_payload("api", 3))
        self.owner = Resource.from_dict(resource_payload("namespace", 4, resource_type="core/namespace"))
        self.inbound_assertion = Resource.from_dict(
            resource_payload("pod", 5, resource_type="core/pod")
        )

        root = resource_payload(
            "api",
            3,
            relationships=[
                {"type": "depends_on", "target": self.config.identity.uid},
                {"type": "routes_to", "target": self.service.identity.uid},
                {"type": "runs_on", "target": "cluster-local/node-a"},
            ],
        )
        owner = resource_payload(
            "namespace",
            4,
            resource_type="core/namespace",
            relationships=[{"type": "contains", "target": self.root_without_edges.identity.uid}],
        )
        inbound = resource_payload(
            "pod",
            5,
            resource_type="core/pod",
            relationships=[
                {
                    "type": "receives_from",
                    "target": self.root_without_edges.identity.uid,
                    "direction": "inbound",
                }
            ],
        )
        for payload in (
            self.config.to_dict(),
            self.service.to_dict(),
            root,
            owner,
            inbound,
        ):
            self.ingestion.execute(IngestResourceCommand(self.actor, payload))
        self.root_uid = self.root_without_edges.identity.uid

    def test_neighborhood_resolves_current_nodes_and_canonical_edge_direction(self) -> None:
        result = self.queries.neighborhood(self.actor, self.root_uid)

        self.assertEqual(len(result.edges), 5)
        self.assertEqual(
            {edge.relationship_type for edge in result.edges},
            {"contains", "depends_on", "receives_from", "routes_to", "runs_on"},
        )
        self.assertEqual(
            {node.identity.uid for node in result.nodes},
            {
                self.root_uid,
                self.config.identity.uid,
                self.service.identity.uid,
                self.owner.identity.uid,
                self.inbound_assertion.identity.uid,
            },
        )
        receives = next(edge for edge in result.edges if edge.relationship_type == "receives_from")
        self.assertEqual(receives.source_ref, self.root_uid)
        self.assertEqual(receives.target_ref, self.inbound_assertion.identity.uid)
        self.assertEqual(receives.observed_resource_uid, self.inbound_assertion.identity.uid)

    def test_direction_and_relationship_type_filters_are_relative_to_root(self) -> None:
        incoming = self.queries.neighborhood(
            self.actor, self.root_uid, direction="incoming"
        )
        outgoing = self.queries.neighborhood(
            self.actor,
            self.root_uid,
            direction="outgoing",
            relationship_types=("depends_on", "routes_to"),
        )

        self.assertEqual([edge.relationship_type for edge in incoming.edges], ["contains"])
        self.assertEqual(
            {edge.relationship_type for edge in outgoing.edges},
            {"depends_on", "routes_to"},
        )

    def test_neighborhood_cursor_pages_without_duplicates(self) -> None:
        seen = []
        cursor = None
        while True:
            page = self.queries.neighborhood(
                self.actor,
                self.root_uid,
                limit=1,
                cursor=cursor,
            )
            seen.extend(edge.edge_id for edge in page.edges)
            if not page.page.has_more:
                self.assertIsNone(page.page.next_cursor)
                break
            self.assertRegex(page.page.next_cursor or "", r"^p1\.")
            cursor = page.page.next_cursor

        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(seen, sorted(seen))

    def test_cursor_is_bound_to_exact_query_and_tenant_scope(self) -> None:
        first = self.queries.neighborhood(self.actor, self.root_uid, limit=1)
        assert first.page.next_cursor is not None

        with self.assertRaisesRegex(InvalidCursorError, "pagination.cursor_invalid"):
            self.queries.neighborhood(
                self.actor,
                self.root_uid,
                direction="outgoing",
                limit=1,
                cursor=first.page.next_cursor,
            )
        with self.assertRaisesRegex(InvalidCursorError, "pagination.cursor_invalid"):
            self.queries.neighborhood(
                ActorContext("developer", "another-tenant"),
                self.root_uid,
                limit=1,
                cursor=first.page.next_cursor,
            )

    def test_timeline_pages_accepted_stale_and_conflicting_observations(self) -> None:
        newer = resource_payload("api", 6)
        newer["spec"]["attributes"]["availableReplicas"] = 3
        self.ingestion.execute(IngestResourceCommand(self.actor, newer))
        stale = resource_payload("api", 2)
        stale["spec"]["attributes"]["availableReplicas"] = 0
        with self.assertRaises(StaleObservationError):
            self.ingestion.execute(IngestResourceCommand(self.actor, stale))
        conflict = resource_payload("api", 6)
        conflict["spec"]["attributes"]["availableReplicas"] = 1
        with self.assertRaises(ObservationConflictError):
            self.ingestion.execute(IngestResourceCommand(self.actor, conflict))

        first = self.queries.timeline(self.actor, self.root_uid, limit=2)
        assert first.page.next_cursor is not None
        second = self.queries.timeline(
            self.actor,
            self.root_uid,
            limit=2,
            cursor=first.page.next_cursor,
        )

        items = first.items + second.items
        self.assertEqual(
            [item.disposition.value for item in items],
            ["accepted", "accepted", "stale", "conflict"],
        )
        self.assertEqual([item.offset for item in items], sorted(item.offset for item in items))
        self.assertFalse(second.page.has_more)

    def test_projection_replacement_removes_old_current_edges_but_not_history(self) -> None:
        replacement = resource_payload("api", 6)
        self.ingestion.execute(IngestResourceCommand(self.actor, replacement))

        neighborhood = self.queries.neighborhood(
            self.actor, self.root_uid, direction="outgoing"
        )
        timeline = self.queries.timeline(self.actor, self.root_uid)

        self.assertEqual(
            {edge.observed_resource_uid for edge in neighborhood.edges},
            {self.inbound_assertion.identity.uid},
        )
        self.assertNotIn(
            self.root_uid,
            {edge.observed_resource_uid for edge in neighborhood.edges},
        )
        self.assertEqual(len(timeline.items), 2)

    def test_query_authority_validation_and_not_found_fail_closed(self) -> None:
        with self.assertRaises(QueryAuthorizationError):
            self.queries.neighborhood(
                ActorContext("anonymous", "local"), self.root_uid
            )
        with self.assertRaises(ResourceNotFoundError):
            self.queries.timeline(
                ActorContext("developer", "another-tenant"), self.root_uid
            )
        with self.assertRaises(InvalidQueryError):
            self.queries.timeline(self.actor, "not-a-resource")
        with self.assertRaises(InvalidQueryError):
            self.queries.neighborhood(self.actor, self.root_uid, limit=101)
        with self.assertRaises(InvalidQueryError):
            self.queries.neighborhood(
                self.actor,
                self.root_uid,
                relationship_types=("depends_on",) * 33,
            )


class ResourceQueryHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        authenticator = HashedBearerAuthenticator(
            (
                BearerIdentity(
                    HashedBearerAuthenticator.token_sha256(LOCAL_TOKEN),
                    ActorContext("developer", "local", ("developer",)),
                ),
                BearerIdentity(
                    HashedBearerAuthenticator.token_sha256(OTHER_TOKEN),
                    ActorContext("other-developer", "another-tenant", ("developer",)),
                ),
            )
        )
        self.runtime = build_local_runtime(authenticator)
        self.actor = ActorContext("developer", "local")
        target = Resource.from_dict(
            resource_payload("settings", 1, resource_type="core/configmap")
        )
        root_payload = resource_payload(
            "api",
            2,
            relationships=[{"type": "depends_on", "target": target.identity.uid}],
        )
        for payload in (target.to_dict(), root_payload):
            self.runtime.ingestion.execute(IngestResourceCommand(self.actor, payload))
        self.root_uid = Resource.from_dict(root_payload).identity.uid
        self.handler = object.__new__(ApiHandler)
        self.handler.runtime = self.runtime
        self.handler.headers = {
            "authorization": f"Bearer {LOCAL_TOKEN}",
        }
        self.responses: list[tuple[HTTPStatus, dict]] = []
        self.handler._json = lambda status, payload: self.responses.append((status, payload))

    def query(self, kind: str, query: str = "") -> tuple[HTTPStatus, dict]:
        self.responses.clear()
        try:
            actor = self.handler._actor()
        except AuthenticationError as exc:
            self.handler._authentication_failed(exc)
        else:
            self.handler._query_resource(actor, self.root_uid, kind, query)
        self.assertEqual(len(self.responses), 1)
        return self.responses[0]

    def test_http_surface_serializes_executable_neighborhood_and_timeline(self) -> None:
        neighborhood_status, neighborhood = self.query(
            "neighborhood",
            "depth=1&direction=outgoing&relationshipType=depends_on&limit=1",
        )
        timeline_status, timeline = self.query("timeline", "limit=1")

        self.assertEqual(neighborhood_status, HTTPStatus.OK)
        self.assertEqual(timeline_status, HTTPStatus.OK)
        self.assertEqual(neighborhood["kind"], "ResourceNeighborhood")
        self.assertEqual(neighborhood["spec"]["edges"][0]["type"], "depends_on")
        self.assertEqual(timeline["kind"], "ResourceTimeline")
        self.assertEqual(timeline["spec"]["items"][0]["disposition"], "accepted")
        for schema_name, payload in (
            ("resource-neighborhood.schema.json", neighborhood),
            ("resource-timeline.schema.json", timeline),
        ):
            schema = json.loads(
                (ROOT / "contracts" / "schemas" / schema_name).read_text(encoding="utf-8")
            )
            self.assertEqual(
                validate_schemas.instance_validation_errors(
                    schema,
                    payload,
                    label="HTTP query response",
                ),
                [],
            )

    def test_http_returns_stable_cursor_tenant_and_query_errors(self) -> None:
        status, payload = self.query("timeline", "cursor=p1.modified")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["error"]["code"], "pagination.cursor_invalid")

        self.handler.headers = {
            "authorization": f"Bearer {OTHER_TOKEN}",
            "x-iip-tenant-id": "local",
            "x-iip-actor-id": "developer",
        }
        status, payload = self.query("timeline")
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(payload["error"]["code"], "resource.not_found")

        self.handler.headers = {
            "x-iip-tenant-id": "local",
            "x-iip-actor-id": "developer",
            "authorization": "Bearer invalid-reference-token-00000000000001",
        }
        status, payload = self.query("neighborhood")
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(payload["error"]["code"], "authentication.invalid")

        self.handler.headers = {}
        status, payload = self.query("timeline")
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(payload["error"]["code"], "authentication.required")

    def test_python_sdk_builds_query_urls_and_parses_public_envelopes(self) -> None:
        neighborhood = json.loads(
            (ROOT / "contracts" / "examples" / "resource-neighborhood.json").read_text(
                encoding="utf-8"
            )
        )
        timeline = json.loads(
            (ROOT / "contracts" / "examples" / "resource-timeline.json").read_text(
                encoding="utf-8"
            )
        )

        class Response:
            def __init__(self, payload: dict) -> None:
                self.payload = payload

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        client = Client("https://control.example", LOCAL_TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen",
            side_effect=(Response(neighborhood), Response(timeline)),
        ) as send:
            parsed_neighborhood = client.get_resource_neighborhood(
                self.root_uid,
                direction="outgoing",
                relationship_types=("depends_on",),
                limit=1,
            )
            parsed_timeline = client.get_resource_timeline(self.root_uid, limit=1)

        self.assertEqual(parsed_neighborhood.to_dict(), neighborhood)
        self.assertEqual(parsed_timeline.to_dict(), timeline)
        first_url = send.call_args_list[0].args[0].full_url
        second_url = send.call_args_list[1].args[0].full_url
        first_request = send.call_args_list[0].args[0]
        self.assertIn("/neighborhood?", first_url)
        self.assertIn("relationshipType=depends_on", first_url)
        self.assertIn("/timeline?limit=1", second_url)
        self.assertEqual(
            first_request.get_header("Authorization"),
            f"Bearer {LOCAL_TOKEN}",
        )
        self.assertIsNone(first_request.get_header("X-iip-tenant-id"))
        self.assertIsNone(first_request.get_header("X-iip-actor-id"))


if __name__ == "__main__":
    unittest.main()
