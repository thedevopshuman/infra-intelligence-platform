#!/usr/bin/env python3
"""Generate or verify minimized AI price-catalog qualification evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from iip.application.qualify_ai_price_catalog import (
    AiPriceCatalogQualificationError,
    qualify_ai_price_catalog,
    verify_ai_price_catalog_qualification_report,
)


MAX_INPUT_BYTES = 16 * 1024 * 1024


def _read_document(path: str) -> object:
    source = Path(path)
    try:
        if not source.is_file() or source.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.input.unreadable"
        ) from None


def _write_document(path: str, document: object) -> None:
    destination = Path(path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except OSError:
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.output.unwritable"
        ) from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate")
    generate.add_argument("--catalog", required=True)
    generate.add_argument("--policy", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument(
        "--qualification-level",
        choices=("offline-static", "production-catalog"),
        required=True,
    )
    generate.add_argument("--generated-at")

    verify = commands.add_parser("verify")
    verify.add_argument("--report", required=True)
    verify.add_argument("--catalog", required=True)
    verify.add_argument("--policy", required=True)
    verify.add_argument("--evaluated-at")
    verify.add_argument("--allow-unqualified", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        catalog = _read_document(arguments.catalog)
        policy = _read_document(arguments.policy)
        if arguments.command == "generate":
            report = qualify_ai_price_catalog(
                catalog,
                policy,
                generated_at=arguments.generated_at or _now(),
                qualification_level=arguments.qualification_level,
            )
            _write_document(arguments.output, report)
            status = report["spec"]["status"]  # type: ignore[index]
            print(f"AI price catalog qualification {status}: {arguments.output}")
            return 0 if status == "qualified" else 1
        report = _read_document(arguments.report)
        verified = verify_ai_price_catalog_qualification_report(
            report,
            catalog,
            policy,
            evaluated_at=arguments.evaluated_at or _now(),
            require_qualified=not arguments.allow_unqualified,
        )
        status = verified["spec"]["status"]  # type: ignore[index]
        print(f"AI price catalog qualification verified: {status}")
        return 0
    except AiPriceCatalogQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
