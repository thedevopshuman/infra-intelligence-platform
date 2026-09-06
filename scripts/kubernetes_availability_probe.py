#!/usr/bin/env python3
"""In-cluster aggregate-only API and OTLP availability probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)


PHASES = ("baseline", "disruption", "recovery")
PROBES = ("control-plane-api", "otlp-metrics")
MAX_RESPONSE_BYTES = 65_536


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        del request, file_pointer, code, message, headers, url
        return None


def _token(path: Path) -> str:
    value = path.read_text(encoding="ascii")
    if len(value) > 8194:
        raise ValueError("probe credential is invalid")
    token = value.rstrip("\r\n")
    if value not in (token, token + "\n", token + "\r\n") or len(token) < 32:
        raise ValueError("probe credential is invalid")
    return token


def _request(
    *, url: str, token: str, method: str, body: bytes | None, content_type: str
) -> tuple[int, str, bytes]:
    headers = {
        "authorization": f"Bearer {token}",
        "accept": content_type,
        "connection": "close",
    }
    if body is not None:
        headers["content-type"] = content_type
    response = build_opener(ProxyHandler({}), _DenyRedirects()).open(
        Request(url, headers=headers, data=body, method=method), timeout=2
    )
    try:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("probe response exceeded limit")
        return response.status, response.headers.get_content_type(), payload
    finally:
        response.close()


def _resource_uid(tenant: str, provider: str, resource_type: str, external_id: str) -> str:
    canonical = "\x1f".join((tenant, provider, resource_type, external_id))
    return "res_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _resource_document(tenant: str) -> tuple[str, dict[str, Any]]:
    provider = "qualification"
    resource_type = "synthetic/probe"
    external_id = "kubernetes-availability"
    uid = _resource_uid(tenant, provider, resource_type, external_id)
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return uid, {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "Resource",
        "metadata": {
            "uid": uid,
            "tenantId": tenant,
            "observedAt": observed_at,
            "observation": {
                "sourceId": "qualification-probe",
                "streamId": "obs_" + "a" * 32,
                "sequence": 1,
                "mode": "incremental",
                "resourceVersion": "1",
            },
        },
        "spec": {
            "provider": provider,
            "type": resource_type,
            "externalId": external_id,
            "displayName": "availability probe",
            "attributes": {},
            "relationships": [],
        },
        "status": {"health": "healthy", "lifecycle": "active"},
    }


def _metric_payload() -> bytes:
    request = ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.resource.attributes.add(
        key="service.name"
    ).value.string_value = "availability-probe"
    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = "iip.kubernetes-availability-qualification"
    metric = scope_metrics.metrics.add(
        name="iip.qualification.request.count", unit="{request}"
    )
    metric.sum.aggregation_temporality = 2
    metric.sum.is_monotonic = True
    point = metric.sum.data_points.add()
    point.time_unix_nano = time.time_ns()
    point.as_int = 1
    return request.SerializeToString()


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def seed(args: argparse.Namespace) -> None:
    control_token = _token(args.control_token_file)
    otlp_token = _token(args.otlp_token_file)
    _, resource = _resource_document(args.tenant)
    status, content_type, _ = _request(
        url=args.api_url + "/v1/resources",
        token=control_token,
        method="POST",
        body=json.dumps(resource, separators=(",", ":")).encode("utf-8"),
        content_type="application/json",
    )
    resource_accepted = status == 202 and content_type == "application/json"
    status, content_type, body = _request(
        url=args.otlp_url + "/v1/metrics",
        token=otlp_token,
        method="POST",
        body=_metric_payload(),
        content_type="application/x-protobuf",
    )
    metric_accepted = (
        status == 200 and content_type == "application/x-protobuf" and body == b""
    )
    if not resource_accepted or not metric_accepted:
        raise RuntimeError("availability seed was not accepted")
    state = {
        "seed": {"resourceAccepted": True, "metricAccepted": True},
        "phases": {
            phase: {
                probe: {"attempts": 0, "successes": 0, "failures": 0}
                for probe in PROBES
            }
            for phase in PHASES
        },
    }
    _write_state(args.state_file, state)


def _runtime_identity_valid(payload: bytes, args: argparse.Namespace) -> bool:
    try:
        document = json.loads(payload)
        spec = document["spec"]
        return (
            document["apiVersion"] == "iip.platform/v1alpha1"
            and document["kind"] == "RuntimeVersionReport"
            and spec["application"]["version"] == args.application_version
            and spec["contracts"]["apiVersion"] == "iip.platform/v1alpha1"
            and spec["storage"]["requiredMigration"] == args.required_migration
            and spec["build"] == {"mode": "release", "revision": args.source_revision}
            and spec["deployment"]
            == {
                "helmChartVersion": args.chart_version,
                "imageDigest": args.image_digest,
            }
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        return False


def _api_probe(args: argparse.Namespace, token: str) -> bool:
    try:
        status, content_type, body = _request(
            url=args.api_url + "/v1/system/version",
            token=token,
            method="GET",
            body=None,
            content_type="application/json",
        )
        return (
            status == 200
            and content_type == "application/json"
            and _runtime_identity_valid(body, args)
        )
    except Exception:
        return False


def _otlp_probe(args: argparse.Namespace, token: str) -> bool:
    try:
        status, content_type, body = _request(
            url=args.otlp_url + "/v1/metrics",
            token=token,
            method="POST",
            body=_metric_payload(),
            content_type="application/x-protobuf",
        )
        return (
            status == 200
            and content_type == "application/x-protobuf"
            and body == b""
        )
    except Exception:
        return False


def _instant(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def _investigation_request(args: argparse.Namespace, investigation_id: str) -> dict:
    now = time.time()
    resource_uid = _resource_uid(
        args.tenant,
        "qualification",
        "synthetic/probe",
        "kubernetes-availability",
    )
    question = "Can the surviving workflow worker complete bounded queued work?"
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "InvestigationRequest",
        "metadata": {
            "id": investigation_id,
            "tenantId": args.tenant,
            "actorId": args.actor_id,
            "requestedAt": _instant(now),
            "correlationId": f"kubernetes-availability-{args.phase}",
        },
        "spec": {
            "question": question,
            "trigger": {
                "type": "scheduled",
                "source": "urn:iip:qualification:kubernetes-availability",
                "summary": question,
            },
            "scope": {
                "resourceUids": [resource_uid],
                "timeRange": {
                    "start": _instant(now - 3600),
                    "end": _instant(now),
                },
            },
            "agentSelector": {
                "id": "incident-investigator",
                "version": "0.1.0",
            },
            "evidenceTypes": ["resource.change"],
            "allowedTools": ["resources/query", "evidence/fetch"],
            "budgets": {
                "maxToolCalls": 8,
                "maxWallTimeSeconds": 60,
                "maxModelTokens": 0,
                "maxCostUsd": 0,
                "maxEvidenceItems": 16,
                "maxIterations": 8,
            },
            "maxAuthority": "propose",
            "priority": "normal",
        },
    }


def workflow(args: argparse.Namespace) -> None:
    """Submit and observe one durable job after a declared topology state."""

    token = _token(args.control_token_file)
    investigation_id = "inv_" + secrets.token_hex(16)
    request = _investigation_request(args, investigation_id)
    started = time.monotonic()
    status, content_type, body = _request(
        url=args.api_url + "/v1/investigation-jobs",
        token=token,
        method="POST",
        body=json.dumps(request, separators=(",", ":")).encode("utf-8"),
        content_type="application/json",
    )
    try:
        submitted = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("workflow processing submission was invalid") from None
    submitted_metadata = (
        submitted.get("metadata") if isinstance(submitted, Mapping) else None
    )
    if (
        status != 202
        or content_type != "application/json"
        or not isinstance(submitted, Mapping)
        or submitted.get("apiVersion") != "iip.platform/v1alpha1"
        or submitted.get("kind") != "InvestigationJobStatus"
        or not isinstance(submitted_metadata, Mapping)
        or submitted_metadata.get("id") != investigation_id
        or submitted_metadata.get("tenantId") != args.tenant
    ):
        raise RuntimeError("workflow processing submission was not accepted")

    polls = 0
    deadline = started + args.timeout_seconds
    while time.monotonic() < deadline:
        polls += 1
        status, content_type, body = _request(
            url=args.api_url + f"/v1/investigation-jobs/{investigation_id}",
            token=token,
            method="GET",
            body=None,
            content_type="application/json",
        )
        try:
            job = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError("workflow processing status was invalid") from None
        job_metadata = job.get("metadata") if isinstance(job, Mapping) else None
        spec = job.get("spec") if isinstance(job, Mapping) else None
        state = spec.get("state") if isinstance(spec, Mapping) else None
        if (
            status != 200
            or content_type != "application/json"
            or not isinstance(job, Mapping)
            or job.get("apiVersion") != "iip.platform/v1alpha1"
            or job.get("kind") != "InvestigationJobStatus"
            or not isinstance(job_metadata, Mapping)
            or job_metadata.get("id") != investigation_id
            or job_metadata.get("tenantId") != args.tenant
            or not isinstance(spec, Mapping)
            or state not in {"queued", "running", "completed", "failed", "cancelled"}
        ):
            raise RuntimeError("workflow processing status was unavailable")
        if state in {"failed", "cancelled"}:
            raise RuntimeError("workflow processing did not complete")
        if state == "completed":
            status, content_type, body = _request(
                url=args.api_url + f"/v1/investigations/{investigation_id}",
                token=token,
                method="GET",
                body=None,
                content_type="application/json",
            )
            try:
                report = json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError("workflow processing report was invalid") from None
            report_spec = report.get("spec") if isinstance(report, Mapping) else None
            report_metadata = (
                report.get("metadata") if isinstance(report, Mapping) else None
            )
            if (
                status != 200
                or content_type != "application/json"
                or not isinstance(report, Mapping)
                or report.get("apiVersion") != "iip.platform/v1alpha1"
                or report.get("kind") != "InvestigationReport"
                or not isinstance(report_metadata, Mapping)
                or report_metadata.get("id") != investigation_id
                or report_metadata.get("tenantId") != args.tenant
                or not isinstance(report_spec, Mapping)
                or report_spec.get("outcome") not in {"conclusive", "inconclusive"}
            ):
                raise RuntimeError("workflow processing report was invalid")
            elapsed = max(0, int((time.monotonic() - started) * 1000))
            print(
                json.dumps(
                    {
                        "phase": args.phase,
                        "submitted": 1,
                        "completed": 1,
                        "failures": 0,
                        "pollAttempts": polls,
                        "completionMilliseconds": elapsed,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return
        time.sleep(0.2)
    raise RuntimeError("workflow processing completion timed out")


def run(args: argparse.Namespace) -> None:
    control_token = _token(args.control_token_file)
    otlp_token = _token(args.otlp_token_file)
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    while not args.stop_file.exists():
        phase = args.phase_file.read_text(encoding="ascii").strip()
        if phase not in PHASES:
            raise RuntimeError("availability probe phase is invalid")
        outcomes = {
            "control-plane-api": _api_probe(args, control_token),
            "otlp-metrics": _otlp_probe(args, otlp_token),
        }
        for probe, succeeded in outcomes.items():
            measurement = state["phases"][phase][probe]
            measurement["attempts"] += 1
            measurement["successes" if succeeded else "failures"] += 1
        _write_state(args.state_file, state)
        time.sleep(args.interval_milliseconds / 1000)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("seed", "run"):
        current = subparsers.add_parser(command)
        current.add_argument("--api-url", required=True)
        current.add_argument("--otlp-url", required=True)
        current.add_argument("--control-token-file", required=True, type=Path)
        current.add_argument("--otlp-token-file", required=True, type=Path)
        current.add_argument("--state-file", required=True, type=Path)
        current.add_argument("--tenant", default="availability")
        if command == "run":
            current.add_argument("--phase-file", required=True, type=Path)
            current.add_argument("--stop-file", required=True, type=Path)
            current.add_argument("--application-version", required=True)
            current.add_argument("--chart-version", required=True)
            current.add_argument("--required-migration", required=True)
            current.add_argument("--source-revision", required=True)
            current.add_argument("--image-digest", required=True)
            current.add_argument(
                "--interval-milliseconds", type=int, default=250, choices=range(100, 5001)
            )
    workflow_parser = subparsers.add_parser("workflow")
    workflow_parser.add_argument("--api-url", required=True)
    workflow_parser.add_argument("--control-token-file", required=True, type=Path)
    workflow_parser.add_argument("--tenant", default="availability")
    workflow_parser.add_argument("--actor-id", default="availability-probe")
    workflow_parser.add_argument("--phase", required=True, choices=PHASES)
    workflow_parser.add_argument(
        "--timeout-seconds", type=int, default=60, choices=range(10, 121)
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "seed":
            seed(args)
        elif args.command == "run":
            run(args)
        else:
            workflow(args)
    except Exception:
        print("availability probe failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
