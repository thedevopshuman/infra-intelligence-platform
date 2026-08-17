"""Fixture and explicit live-cluster entry point for the observer example."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from infra_intelligence_sdk import (
    PluginActionMediationRequest,
    PluginMediationClient,
    PluginMediationRequest,
)

from .collector import canonical_digest, collect
from .live import LiveCollectionError, list_objects, watch_then_list_objects


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect and normalize Kubernetes objects")
    result.add_argument("--request", type=Path)
    result.add_argument("--stdio", action="store_true")
    source = result.add_mutually_exclusive_group()
    source.add_argument("--objects", type=Path)
    source.add_argument("--live-context")
    result.add_argument("--kubeconfig", type=Path)
    result.add_argument("--timeout-seconds", type=int, default=20)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    mediation_grants: list[object] = []
    try:
        if args.stdio:
            if args.request is not None or args.live_context is not None:
                raise ValueError("stdio accepts one invocation and a fixed object source")
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("invocation exceeds the input limit")
            invocation = json.loads(raw)
            if (
                not isinstance(invocation, dict)
                or invocation.get("apiVersion") != "iip.platform/v1alpha1"
                or invocation.get("kind") != "PluginInvocation"
                or not isinstance(invocation.get("metadata"), dict)
                or not isinstance(invocation.get("spec"), dict)
                or not isinstance(invocation["spec"].get("input"), dict)
            ):
                raise ValueError("invalid invocation")
            if (
                invocation["spec"].get("capability") == "action-provider"
                and invocation["spec"].get("method") == "propose-restart"
            ):
                action_grants = invocation["spec"].get("actionMediationGrants")
                if not isinstance(action_grants, list) or len(action_grants) != 1:
                    raise ValueError("action mediation grant is required")
                grant = action_grants[0]
                grant_metadata = grant.get("metadata") if isinstance(grant, dict) else None
                action_request = PluginActionMediationRequest.from_dict(
                    invocation["spec"]["input"]
                )
                request_metadata = action_request.payload.get("metadata")
                if (
                    not isinstance(grant_metadata, dict)
                    or not isinstance(request_metadata, dict)
                    or request_metadata.get("invocationId")
                    != invocation["metadata"].get("id")
                    or request_metadata.get("grantId") != grant_metadata.get("id")
                ):
                    raise ValueError("action mediation identity is invalid")
                response = PluginMediationClient().propose_action(action_request)
                print(
                    json.dumps(
                        response.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return 0 if response.proposed else 1
            if (
                invocation["spec"].get("capability") != "resource-observer"
                or invocation["spec"].get("method") != "collect"
            ):
                raise ValueError("unsupported invocation interface")
            request = invocation["spec"]["input"]
            candidate_grants = invocation["spec"].get("mediationGrants", [])
            if not isinstance(candidate_grants, list):
                raise ValueError("invalid mediation grants")
            mediation_grants = candidate_grants
        else:
            if args.request is None:
                raise ValueError("fixture and live modes require --request")
            request = json.loads(args.request.read_text(encoding="utf-8"))
        if mediation_grants:
            grant = mediation_grants[0]
            grant_metadata = grant.get("metadata") if isinstance(grant, dict) else None
            request_spec = request.get("spec") if isinstance(request, dict) else None
            scope = request_spec.get("scope") if isinstance(request_spec, dict) else None
            parameters = scope.get("parameters") if isinstance(scope, dict) else None
            namespaces = parameters.get("namespaces") if isinstance(parameters, dict) else None
            if (
                not isinstance(grant_metadata, dict)
                or not isinstance(grant_metadata.get("id"), str)
                or not isinstance(namespaces, list)
                or len(namespaces) != 1
                or not isinstance(namespaces[0], str)
            ):
                raise ValueError("mediated collection scope is invalid")
            identity = f"{invocation['metadata']['id']}\x1f{grant_metadata['id']}".encode()
            mediated_request = PluginMediationRequest.from_dict(
                {
                    "apiVersion": "iip.plugin-runtime/v1alpha1",
                    "kind": "PluginMediationRequest",
                    "metadata": {
                        "id": "pmr_" + hashlib.sha256(identity).hexdigest()[:32],
                        "invocationId": invocation["metadata"]["id"],
                        "grantId": grant_metadata["id"],
                    },
                    "spec": {
                        "method": "GET",
                        "path": f"/api/v1/namespaces/{namespaces[0]}/pods",
                        "query": {"limit": "500"},
                    },
                }
            )
            mediated_response = PluginMediationClient().request(mediated_request)
            if not mediated_response.succeeded or not isinstance(
                mediated_response.body, dict
            ):
                raise ValueError("mediated collection failed")
            objects = mediated_response.body
        elif args.objects is not None:
            if args.kubeconfig is not None:
                raise ValueError("kubeconfig is only valid with live collection")
            objects = json.loads(args.objects.read_text(encoding="utf-8"))
        elif args.live_context is not None:
            if args.kubeconfig is None:
                raise ValueError("live collection requires an explicit kubeconfig")
            spec = request.get("spec")
            scope = spec.get("scope") if isinstance(spec, dict) else None
            parameters = scope.get("parameters") if isinstance(scope, dict) else None
            namespaces = parameters.get("namespaces") if isinstance(parameters, dict) else None
            cluster_id = scope.get("rootExternalId") if isinstance(scope, dict) else None
            if not isinstance(namespaces, list) or not isinstance(cluster_id, str):
                raise ValueError("live collection requires a supported scope")
            options = {
                "context": args.live_context,
                "kubeconfig": args.kubeconfig,
                "namespaces": namespaces,
                "cluster_id": cluster_id,
                "scope_digest": canonical_digest(scope),
                "timeout_seconds": args.timeout_seconds,
            }
            resume = spec.get("resume")
            objects = (
                watch_then_list_objects(resume=resume, **options)
                if isinstance(resume, dict)
                else list_objects(**options)
            )
        else:
            raise ValueError("an explicit object source is required")
        result = collect(request, objects).to_dict()
    except LiveCollectionError as exc:
        print(json.dumps({"error": {"code": str(exc)}}))
        return 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        print(json.dumps({"error": {"code": "collector.request_invalid"}}))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if args.stdio:
        return 0
    return 0 if result["spec"]["completion"]["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
