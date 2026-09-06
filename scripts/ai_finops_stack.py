#!/usr/bin/env python3
"""One-command lifecycle for the local AI FinOps reference dashboard."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import ai_finops_fixture


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".iip"
ENV_PATH = STATE_DIR / "ai-finops.env"
COMPOSE_PATH = ROOT / "deploy" / "docker-compose.ai-finops.yml"
PROJECT = "iip-ai-finops"


def current_anchor() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def create_configuration(anchor: datetime | None = None) -> Path:
    selected = anchor or current_anchor()
    values = {
        "COMPOSE_PROJECT_NAME": PROJECT,
        "IIP_AI_FINOPS_ANCHOR": ai_finops_fixture.format_timestamp(selected),
        "IIP_AI_USAGE_CHANNEL_TOKEN": ai_finops_fixture.CHANNEL_TOKEN,
        "IIP_OPENAI_USAGE_CHANNEL_TOKEN": (
            ai_finops_fixture.OPENAI_CHANNEL_TOKEN
        ),
        "IIP_AI_USAGE_RECEIVER_CHANNELS_JSON": json.dumps(
            ai_finops_fixture.channel_configuration(),
            separators=(",", ":"),
            sort_keys=True,
        ),
        "IIP_AI_PRICE_CATALOGS_JSON": json.dumps(
            ai_finops_fixture.price_catalog_configuration(selected),
            separators=(",", ":"),
            sort_keys=True,
        ),
        "IIP_AI_ATTRIBUTION_POLICIES_JSON": json.dumps(
            ai_finops_fixture.attribution_policy_configuration(selected),
            separators=(",", ":"),
            sort_keys=True,
        ),
        "IIP_AI_SAVINGS_PROFILES_JSON": json.dumps(
            ai_finops_fixture.savings_profile_configuration(selected),
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    ENV_PATH.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    return ENV_PATH


def compose(arguments: Sequence[str], *, capture_output: bool = False) -> str:
    command = [
        os.environ.get("IIP_DOCKER_BIN", "docker"),
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(ENV_PATH),
        "--file",
        str(COMPOSE_PATH),
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=capture_output,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Docker CLI is unavailable; start Docker Desktop and retry"
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"Docker Compose failed with exit code {error.returncode}"
        ) from error
    return completed.stdout if capture_output else ""


def _wait_for_collector(timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:13134/",
                timeout=2,
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("AI FinOps Collector did not become ready")


def start() -> None:
    if ENV_PATH.is_file() and compose(
        ("ps", "--status", "running", "--quiet"),
        capture_output=True,
    ).strip():
        raise RuntimeError(
            "AI FinOps reference slice is already running; use make ai-finops-status"
        )
    anchor = current_anchor()
    create_configuration(anchor)
    compose(
        (
            "up",
            "--build",
            "--detach",
            "--remove-orphans",
            "--wait",
            "--wait-timeout",
            "180",
        )
    )
    _wait_for_collector()
    ai_finops_fixture.send_fixture(
        anchor,
        "http://127.0.0.1:14319",
        "http://127.0.0.1:14321",
        "http://127.0.0.1:14320",
    )
    ai_finops_fixture.verify_fixture(
        anchor=anchor,
        database_url="postgresql://iip@127.0.0.1:15435/iip",
        api_endpoint="http://127.0.0.1:18082",
        prometheus_endpoint="http://127.0.0.1:19091",
        grafana_endpoint="http://127.0.0.1:13000",
        loki_endpoint="http://127.0.0.1:13101",
        timeout_seconds=60,
    )
    print("IIP AI FinOps reference slice is ready in Docker Desktop.")
    print("Dashboard: http://127.0.0.1:13000/d/iip-ai-finops")
    print("Console: http://127.0.0.1:18082/console")
    print(f"Console token: {ai_finops_fixture.CONTROL_TOKEN}")
    print("Prometheus: http://127.0.0.1:19091")
    print(f"Protected fixture configuration: {ENV_PATH}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "status", "down"))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "up":
            start()
        elif arguments.command == "status":
            if not ENV_PATH.is_file():
                raise RuntimeError(
                    "AI FinOps configuration does not exist; run make ai-finops-up"
                )
            compose(("ps",))
        else:
            if not ENV_PATH.is_file():
                raise RuntimeError("AI FinOps reference slice is not configured")
            compose(("down", "--volumes"))
            print("Stopped the disposable AI FinOps reference slice.")
        return 0
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
