"""Fixture and explicit live-cluster entry point for the observer example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .collector import canonical_digest, collect
from .live import LiveCollectionError, list_objects, watch_then_list_objects


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect and normalize Kubernetes objects")
    result.add_argument("--request", required=True, type=Path)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--objects", type=Path)
    source.add_argument("--live-context")
    result.add_argument("--kubeconfig", type=Path)
    result.add_argument("--timeout-seconds", type=int, default=20)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if args.objects is not None:
            if args.kubeconfig is not None:
                raise ValueError("kubeconfig is only valid with live collection")
            objects = json.loads(args.objects.read_text(encoding="utf-8"))
        else:
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
        result = collect(request, objects).to_dict()
    except LiveCollectionError as exc:
        print(json.dumps({"error": {"code": str(exc)}}))
        return 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        print(json.dumps({"error": {"code": "collector.request_invalid"}}))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["spec"]["completion"]["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
