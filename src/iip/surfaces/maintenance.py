"""Explicit local operator surface for destructive-capable maintenance tasks."""

from __future__ import annotations

import argparse
import json
import os
from typing import Sequence

from iip.application.ports import ActorContext
from iip.application.rebuild_projections import RebuildProjectionsCommand
from iip.bootstrap import build_projection_maintenance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iip-maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    rebuild = commands.add_parser(
        "rebuild-projections",
        help="verify or rebuild one tenant from accepted observations",
    )
    rebuild.add_argument("--tenant", required=True)
    rebuild.add_argument("--actor", required=True)
    rebuild.add_argument("--max-resources", type=int, default=100_000)
    rebuild.add_argument(
        "--apply",
        action="store_true",
        help="apply an atomic rebuild; without this flag the command is read-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url = os.environ.get("IIP_DATABASE_URL")
    if not database_url:
        raise SystemExit("IIP_DATABASE_URL is required")

    service = build_projection_maintenance(database_url)
    result = service.execute(
        RebuildProjectionsCommand(
            actor=ActorContext(
                actor_id=args.actor,
                tenant_id=args.tenant,
                roles=("platform-admin",),
            ),
            dry_run=not args.apply,
            max_resources=args.max_resources,
        )
    )
    payload = {
        "tenantId": result.tenant_id,
        "dryRun": result.dry_run,
        "driftDetected": result.drift_detected,
        "rebuildPerformed": result.rebuild_performed,
        "resourceCount": result.resource_count,
        "relationshipCount": result.relationship_count,
        "latestObservationOffset": result.latest_observation_offset,
        "beforeDigest": result.before_digest,
        "expectedDigest": result.expected_digest,
        "afterDigest": result.after_digest,
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
