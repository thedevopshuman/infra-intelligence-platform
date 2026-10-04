#!/usr/bin/env python3
"""Container-only composition of existing synthetic AI learning fixtures."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import ai_finops_fixture as fixture


def configuration(anchor: datetime | None = None) -> dict[str, str]:
    anchor = anchor or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    compact = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    return {
        "IIP_AI_FINOPS_IMAGE": "iip-learning:0.84.0",
        "IIP_AI_FINOPS_ANCHOR": fixture.format_timestamp(anchor),
        "IIP_AI_USAGE_CHANNEL_TOKEN": fixture.CHANNEL_TOKEN,
        "IIP_OPENAI_USAGE_CHANNEL_TOKEN": fixture.OPENAI_CHANNEL_TOKEN,
        "IIP_AI_USAGE_RECEIVER_CHANNELS_JSON": compact(fixture.channel_configuration()),
        "IIP_AI_PRICE_CATALOGS_JSON": compact(fixture.price_catalog_configuration(anchor)),
        "IIP_AI_ATTRIBUTION_POLICIES_JSON": compact(fixture.attribution_policy_configuration(anchor)),
        "IIP_AI_SAVINGS_PROFILES_JSON": compact(fixture.savings_profile_configuration(anchor)),
    }


def seed() -> dict[str, int]:
    anchor = fixture._parse_anchor(os.environ["IIP_AI_FINOPS_ANCHOR"])
    for attempt in range(60):
        try:
            with urllib.request.urlopen("http://otel-collector:13133/", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("learning.collector.not-ready")
    print("Sending synthetic usage; two intentional invalid-span rejections (HTTP 400) are expected.", flush=True)
    fixture.send_fixture(
        anchor, "http://otel-collector:4318", "http://otel-collector:4320",
        "http://ai-usage-receiver:4318",
    )
    return dict(fixture.verify_fixture(
        anchor=anchor,
        database_url="postgresql://iip@postgres:5432/iip",
        api_endpoint="http://api:8080",
        prometheus_endpoint="http://prometheus:9090",
        grafana_endpoint="http://grafana:3000",
        loki_endpoint="http://loki:3100",
        timeout_seconds=90,
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configuration", "seed"))
    args = parser.parse_args()
    if args.command == "configuration":
        for key, value in configuration().items():
            # Compose dotenv uses single quotes to preserve the JSON literally.
            if any(character in value for character in ("'", "\n", "\r")):
                raise ValueError("learning.fixture.configuration.invalid")
            print(f"{key}='{value}'")
    else:
        print(json.dumps(seed(), sort_keys=True))


if __name__ == "__main__":
    main()
