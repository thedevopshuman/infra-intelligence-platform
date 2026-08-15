"""Dependency-free HTTP surface for the reference vertical slice."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from iip.application.actions import (
    ActionWorkflowError,
    DecideActionCommand,
    ExecuteActionCommand,
    ProposeActionCommand,
)
from iip.application.ingest_collection import (
    CollectionConflictError,
    IngestCollectionCommand,
    InvalidCollectionError,
)
from iip.application.ingest_resource import (
    AuthorizationError,
    IngestResourceCommand,
    InvalidInputError,
    ObservationConflictError,
    StaleObservationError,
)
from iip.application.investigate import (
    InvestigationConflictError,
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.observe_ingestion import (
    GetIngestionFreshnessCommand,
    IngestionSourceNotFoundError,
    IngestionTelemetryAuthorizationError,
    IngestionTelemetryInputError,
    IngestionTelemetryStateError,
)
from iip.application.plugin_sessions import (
    OpenPluginSessionCommand,
    PluginHandshakeError,
)
from iip.application.ports import ActorContext, AuthenticationError, PersistenceError
from iip.application.query_resources import (
    InvalidCursorError,
    InvalidQueryError,
    PageInfo,
    QueryAuthorizationError,
    ResourceNeighborhoodResult,
    ResourceNotFoundError,
    ResourceTimelineResult,
)
from iip.bootstrap import Runtime, build_runtime_from_env


class ApiHandler(BaseHTTPRequestHandler):
    """Small HTTP adapter with credential-derived request identity."""

    runtime: Runtime
    server_version = "IIPReference/0.4.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/healthz", "/readyz"):
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        segments = path.strip("/").split("/")
        is_resource_query = (
            len(segments) == 4
            and segments[:2] == ["v1", "resources"]
            and segments[3] in ("neighborhood", "timeline")
        )
        if path.startswith("/v1/"):
            try:
                actor = self._actor()
            except AuthenticationError as exc:
                self._authentication_failed(exc)
                return
        if path == "/v1/resources":
            try:
                if parsed.query:
                    raise InvalidQueryError("request.invalid")
                items = [
                    resource.to_dict()
                    for resource in self.runtime.queries.list_resources(actor)
                ]
                self._json(HTTPStatus.OK, {"items": items})
            except QueryAuthorizationError:
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
            except InvalidQueryError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        if path == "/v1/telemetry/ingestion":
            self._query_ingestion_telemetry(actor, parsed.query)
            return
        if is_resource_query:
            self._query_resource(actor, segments[2], segments[3], parsed.query)
            return
        if len(segments) == 3 and segments[:2] == ["v1", "evidence"]:
            self._stored_document(
                lambda: self.runtime.evidence_store.get(actor, segments[2]),
                "evidence.not_found",
            )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "investigations"]:
            self._stored_document(
                lambda: self.runtime.operational_store.get_investigation(
                    actor, segments[2]
                ),
                "investigation.not_found",
            )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "actions"]:
            self._stored_document(
                lambda: self._action_document(actor, segments[2]),
                "action.not_found",
            )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "plugin-sessions"]:
            self._stored_document(
                lambda: self.runtime.operational_store.get_plugin_session(
                    actor, segments[2]
                ),
                "plugin.session.not_found",
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})

    def _query_ingestion_telemetry(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            parameters = parse_qs(query, keep_blank_values=True)
            if set(parameters) != {"sourceId"}:
                raise IngestionTelemetryInputError("ingestion.source_id.invalid")
            source_id = self._single(parameters, "sourceId")
            if source_id is None:
                raise IngestionTelemetryInputError("ingestion.source_id.invalid")
            report = self.runtime.ingestion_telemetry.get(
                GetIngestionFreshnessCommand(actor, source_id)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except (IngestionTelemetryInputError, InvalidQueryError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except IngestionTelemetryAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except IngestionSourceNotFoundError:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "ingestion.source_not_found"}},
            )
        except (IngestionTelemetryStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        segments = path.strip("/").split("/")
        known = (
            path
            in (
                "/v1/resources",
                "/v1/collections/ingest",
                "/v1/investigations",
                "/v1/actions/proposals",
                "/v1/plugin-sessions",
            )
            or (len(segments) == 4 and segments[:2] == ["v1", "actions"] and segments[3] in ("decision", "execute"))
        )
        if not known:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})
            return
        try:
            actor = self._actor()
        except AuthenticationError as exc:
            self._authentication_failed(exc)
            return
        try:
            payload = self._read_json()
            if path == "/v1/resources":
                document: Mapping[str, object] = self.runtime.ingestion.execute(
                    IngestResourceCommand(
                        actor=actor,
                        payload=payload,
                        correlation_id=self.headers.get("x-correlation-id"),
                    )
                ).to_dict()
                status = HTTPStatus.ACCEPTED
            elif path == "/v1/collections/ingest":
                resources = self.runtime.collection_ingestion.execute(
                    IngestCollectionCommand(
                        actor=actor,
                        request=payload["request"],
                        result=payload["result"],
                        correlation_id=self.headers.get("x-correlation-id"),
                    )
                )
                document = {"items": [resource.to_dict() for resource in resources]}
                status = HTTPStatus.ACCEPTED
            elif path == "/v1/investigations":
                document = self.runtime.investigations.execute(
                    RunInvestigationCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/actions/proposals":
                document = self.runtime.actions.propose(
                    ProposeActionCommand(
                        actor=actor,
                        investigation_id=payload["investigationId"],
                        action_type=payload["actionType"],
                        target_resource_uid=payload["targetResourceUid"],
                        parameters=payload.get("parameters", {}),
                        idempotency_key=payload["idempotencyKey"],
                        expires_at=payload["expiresAt"],
                        dry_run=payload.get("dryRun", True),
                    )
                )
                status = HTTPStatus.CREATED
            elif len(segments) == 4 and segments[3] == "decision":
                document = self.runtime.actions.decide(
                    DecideActionCommand(
                        actor=actor,
                        proposal_id=segments[2],
                        decision=payload["decision"],
                        rationale=payload["rationale"],
                    )
                )
                status = HTTPStatus.CREATED
            elif len(segments) == 4 and segments[3] == "execute":
                document = self.runtime.actions.execute(
                    ExecuteActionCommand(actor=actor, proposal_id=segments[2])
                )
                status = HTTPStatus.OK
            else:
                limits = payload.get("limits", {})
                document = self.runtime.plugin_sessions.open(
                    OpenPluginSessionCommand(
                        actor=actor,
                        manifest=payload["manifest"],
                        requested_capabilities=tuple(payload["requestedCapabilities"]),
                        capability_token=payload["capabilityToken"],
                        max_requests=limits.get("maxRequests", 1),
                        max_wall_time_seconds=limits.get("maxWallTimeSeconds", 60),
                        max_output_bytes=limits.get("maxOutputBytes", 16_777_216),
                    )
                )
                status = HTTPStatus.CREATED
            self._json(status, dict(document))
        except InvalidInputError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "contract.invalid"}})
        except AuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except StaleObservationError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.stale"}},
            )
        except ObservationConflictError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.conflict"}},
            )
        except (InvalidCollectionError, InvalidInvestigationError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
        except CollectionConflictError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": {"code": str(exc)}})
        except InvestigationConflictError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": {"code": str(exc)}})
        except ActionWorkflowError as exc:
            code = str(exc)
            status = (
                HTTPStatus.FORBIDDEN
                if any(word in code for word in ("denied", "role-required", "not-approved"))
                else HTTPStatus.CONFLICT
                if any(word in code for word in ("conflict", "expired"))
                else HTTPStatus.BAD_REQUEST
            )
            self._json(status, {"error": {"code": code}})
        except PluginHandshakeError as exc:
            code = str(exc)
            status = HTTPStatus.FORBIDDEN if code.endswith("policy-denied") else HTTPStatus.BAD_REQUEST
            self._json(status, {"error": {"code": code}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid_json"}})

    def _query_resource(
        self,
        actor: ActorContext,
        resource_uid: str,
        query_kind: str,
        query: str,
    ) -> None:
        try:
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=40,
                )
            except ValueError:
                raise InvalidQueryError("request.invalid") from None
            if query_kind == "neighborhood":
                allowed = {"cursor", "depth", "direction", "limit", "relationshipType"}
                if set(parameters).difference(allowed):
                    raise InvalidQueryError("request.invalid")
                if "depth" in parameters and self._single(parameters, "depth") != "1":
                    raise InvalidQueryError("request.invalid")
                result = self.runtime.queries.neighborhood(
                    actor,
                    resource_uid,
                    direction=self._single(parameters, "direction", "both"),
                    relationship_types=parameters.get("relationshipType", []),
                    limit=self._limit(parameters),
                    cursor=self._single(parameters, "cursor", None),
                )
                self._json(HTTPStatus.OK, self._neighborhood_payload(result))
            else:
                allowed = {"cursor", "limit"}
                if set(parameters).difference(allowed):
                    raise InvalidQueryError("request.invalid")
                result = self.runtime.queries.timeline(
                    actor,
                    resource_uid,
                    limit=self._limit(parameters),
                    cursor=self._single(parameters, "cursor", None),
                )
                self._json(HTTPStatus.OK, self._timeline_payload(result))
        except InvalidCursorError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "pagination.cursor_invalid"}},
            )
        except InvalidQueryError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid"}})
        except QueryAuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except ResourceNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "resource.not_found"}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def _actor(self) -> ActorContext:
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            values = get_all("authorization") or []
        else:
            value = self.headers.get("authorization")
            values = [value] if value is not None else []
        if not values:
            raise AuthenticationError("authentication.required")
        if len(values) != 1 or not isinstance(values[0], str):
            raise AuthenticationError("authentication.invalid")
        scheme, separator, token = values[0].partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not token
            or any(character.isspace() for character in token)
        ):
            raise AuthenticationError("authentication.invalid")
        return self.runtime.authenticator.authenticate_bearer(token)

    def _authentication_failed(self, error: AuthenticationError) -> None:
        code = str(error)
        if code not in ("authentication.required", "authentication.invalid"):
            code = "authentication.invalid"
        self._json(HTTPStatus.UNAUTHORIZED, {"error": {"code": code}})

    def _stored_document(
        self,
        loader: Callable[[], Optional[Mapping[str, object]]],
        not_found_code: str,
    ) -> None:
        try:
            document = loader()
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )
            return
        self._json(
            HTTPStatus.OK if document is not None else HTTPStatus.NOT_FOUND,
            dict(document)
            if document is not None
            else {"error": {"code": not_found_code}},
        )

    def _action_document(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        document = self.runtime.operational_store.get_action_result(actor, proposal_id)
        if document is not None:
            return document
        return self.runtime.operational_store.get_proposal(actor, proposal_id)

    @staticmethod
    def _single(
        parameters: Mapping[str, list[str]],
        name: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        values = parameters.get(name)
        if values is None:
            return default
        if len(values) != 1 or not values[0]:
            raise InvalidQueryError("request.invalid")
        return values[0]

    @classmethod
    def _limit(cls, parameters: Mapping[str, list[str]]) -> int:
        value = cls._single(parameters, "limit", "50")
        try:
            return int(value) if value is not None else 50
        except ValueError:
            raise InvalidQueryError("request.invalid") from None

    @staticmethod
    def _page_payload(page: PageInfo) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"limit": page.limit, "hasMore": page.has_more}
        if page.next_cursor is not None:
            payload["nextCursor"] = page.next_cursor
        return payload

    @classmethod
    def _neighborhood_payload(
        cls, result: ResourceNeighborhoodResult
    ) -> Dict[str, Any]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceNeighborhood",
            "metadata": {
                "tenantId": result.tenant_id,
                "rootResourceUid": result.root_resource_uid,
            },
            "spec": {
                "depth": 1,
                "direction": result.direction,
                "nodes": [resource.to_dict() for resource in result.nodes],
                "edges": [
                    {
                        "id": edge.edge_id,
                        "observedResourceUid": edge.observed_resource_uid,
                        "type": edge.relationship_type,
                        "source": edge.source_ref,
                        "target": edge.target_ref,
                        "attributes": dict(edge.attributes),
                    }
                    for edge in result.edges
                ],
                "page": cls._page_payload(result.page),
            },
        }

    @classmethod
    def _timeline_payload(cls, result: ResourceTimelineResult) -> Dict[str, Any]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceTimeline",
            "metadata": {
                "tenantId": result.tenant_id,
                "resourceUid": result.resource_uid,
            },
            "spec": {
                "items": [
                    {
                        "offset": item.offset,
                        "recordedAt": item.recorded_at,
                        "disposition": item.disposition.value,
                        "observationHash": item.observation_hash,
                        "resource": item.resource.to_dict(),
                    }
                    for item in result.items
                ],
                "page": cls._page_payload(result.page),
            },
        }

    def log_message(self, format: str, *args: object) -> None:
        """Keep the reference surface quiet; production uses structured telemetry."""

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0 or length > 1_048_576:
            raise ValueError("invalid content length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("content-type", "application/json")
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Start the local reference API."""

    host = os.environ.get("IIP_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("IIP_HTTP_PORT", "8080"))
    ApiHandler.runtime = build_runtime_from_env()
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"IIP reference API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("IIP reference API stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
