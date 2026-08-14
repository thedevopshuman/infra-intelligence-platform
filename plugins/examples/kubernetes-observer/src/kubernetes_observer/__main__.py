"""Fixture-backed command-line entry point for the observer conformance example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .collector import collect


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Normalize a Kubernetes list fixture")
    result.add_argument("--request", required=True, type=Path)
    result.add_argument("--objects", required=True, type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        objects = json.loads(args.objects.read_text(encoding="utf-8"))
        result = collect(request, objects).to_dict()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        print(json.dumps({"error": {"code": "collector.request_invalid"}}))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["spec"]["completion"]["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
