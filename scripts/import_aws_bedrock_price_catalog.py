#!/usr/bin/env python3
"""Import or reproduce an exact AWS Bedrock Price List catalog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from iip.adapters.aws_bedrock_price_catalog import (
    AwsBedrockPriceCatalogImportError,
    derive_aws_bedrock_price_catalog_import_policy_id,
    download_aws_bedrock_price_list,
    import_aws_bedrock_price_catalog,
    validate_aws_bedrock_price_catalog_import_policy,
    verify_aws_bedrock_price_catalog_import,
)


MAX_POLICY_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _read_json(path: str, *, maximum_bytes: int) -> object:
    source = Path(path)
    try:
        if not source.is_file() or not 2 <= source.stat().st_size <= maximum_bytes:
            raise ValueError
        with source.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ValueError
        return json.loads(payload.decode("utf-8"))
    except (OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.input.unreadable"
        ) from None


def _read_source(path: str, *, maximum_bytes: int) -> bytes:
    source = Path(path)
    try:
        if not source.is_file() or not 2 <= source.stat().st_size <= maximum_bytes:
            raise ValueError
        with source.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
        if not 2 <= len(payload) <= maximum_bytes:
            raise ValueError
        return payload
    except (OSError, TypeError, ValueError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.input.unreadable"
        ) from None


def _write_json(path: str, document: object) -> None:
    payload = (json.dumps(document, indent=2, sort_keys=False) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.output.unwritable"
        )
    _write_bytes(path, payload)


def _write_bytes(path: str, payload: bytes) -> None:
    destination = Path(path)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.output.unwritable"
        ) from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _outputs_are_distinct(*paths: str) -> None:
    normalized = tuple(Path(path).resolve() for path in paths)
    if len(set(normalized)) != len(normalized):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.output.invalid"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    identify = commands.add_parser(
        "policy-id",
        help="calculate the content-derived ID for an otherwise valid policy",
    )
    identify.add_argument("--policy", required=True)

    generate = commands.add_parser(
        "generate",
        help="import an already retained exact AWS Price List snapshot",
    )
    generate.add_argument("--source", required=True)
    generate.add_argument("--policy", required=True)
    generate.add_argument("--catalog-output", required=True)
    generate.add_argument("--report-output", required=True)
    generate.add_argument("--retrieved-at", required=True)

    fetch = commands.add_parser(
        "fetch",
        help="download the policy-selected official snapshot and retain all outputs",
    )
    fetch.add_argument("--policy", required=True)
    fetch.add_argument("--source-output", required=True)
    fetch.add_argument("--catalog-output", required=True)
    fetch.add_argument("--report-output", required=True)
    fetch.add_argument("--retrieved-at")
    fetch.add_argument("--timeout-seconds", type=int, default=30)

    verify = commands.add_parser(
        "verify",
        help="reimport the retained source and exact-compare both outputs",
    )
    verify.add_argument("--source", required=True)
    verify.add_argument("--policy", required=True)
    verify.add_argument("--catalog", required=True)
    verify.add_argument("--report", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        policy_document = _read_json(
            arguments.policy,
            maximum_bytes=MAX_POLICY_BYTES,
        )
        if arguments.command == "policy-id":
            print(derive_aws_bedrock_price_catalog_import_policy_id(policy_document))
            return 0
        policy = validate_aws_bedrock_price_catalog_import_policy(policy_document)
        if arguments.command == "generate":
            _outputs_are_distinct(
                arguments.source,
                arguments.policy,
                arguments.catalog_output,
                arguments.report_output,
            )
            source = _read_source(
                arguments.source,
                maximum_bytes=policy.maximum_bytes,
            )
            catalog, report = import_aws_bedrock_price_catalog(
                source,
                policy_document,
                retrieved_at=arguments.retrieved_at,
            )
            _write_json(arguments.catalog_output, catalog)
            _write_json(arguments.report_output, report)
            print(f"AWS Bedrock price catalog imported: {arguments.catalog_output}")
            return 0
        if arguments.command == "fetch":
            _outputs_are_distinct(
                arguments.policy,
                arguments.source_output,
                arguments.catalog_output,
                arguments.report_output,
            )
            source = download_aws_bedrock_price_list(
                policy_document,
                timeout_seconds=arguments.timeout_seconds,
            )
            retrieved_at = arguments.retrieved_at or _now()
            catalog, report = import_aws_bedrock_price_catalog(
                source,
                policy_document,
                retrieved_at=retrieved_at,
            )
            _write_bytes(arguments.source_output, source)
            _write_json(arguments.catalog_output, catalog)
            _write_json(arguments.report_output, report)
            print(f"AWS Bedrock price source retained: {arguments.source_output}")
            print(f"AWS Bedrock price catalog imported: {arguments.catalog_output}")
            return 0
        source = _read_source(
            arguments.source,
            maximum_bytes=policy.maximum_bytes,
        )
        catalog = _read_json(arguments.catalog, maximum_bytes=MAX_OUTPUT_BYTES)
        report = _read_json(arguments.report, maximum_bytes=MAX_OUTPUT_BYTES)
        verified = verify_aws_bedrock_price_catalog_import(
            source,
            policy_document,
            catalog,
            report,
        )
        print(f"AWS Bedrock price catalog import verified: {verified['metadata']['id']}")
        return 0
    except AwsBedrockPriceCatalogImportError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
