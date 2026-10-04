#!/usr/bin/env python3
"""Render the single-tenant community dashboard from a validated catalog."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT
    / "deploy"
    / "grafana"
    / "dashboards"
    / "iip-community-ai-finops.json"
)
OUTPUT_NAME = "dashboard.json"
_DASHBOARD_UID = "iip-community-ai-finops"
_CURRENCY = re.compile(r"[A-Z]{3}")
_CURRENCY_SCALES = frozenset({6, 9, 12})
_MARKER_PREFIX = "__IIP_"


class CommunityDashboardError(ValueError):
    """Stable configuration failure without catalog or filesystem disclosure."""


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CommunityDashboardError("community.dashboard.template.invalid")
        result[key] = value
    return result


def _load_template() -> dict[str, object]:
    try:
        raw = TEMPLATE.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > 1_048_576:
            raise CommunityDashboardError("community.dashboard.template.invalid")
        document = json.loads(raw, object_pairs_hook=_object)
    except CommunityDashboardError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CommunityDashboardError(
            "community.dashboard.template.invalid"
        ) from error
    if not isinstance(document, dict) or document.get("uid") != _DASHBOARD_UID:
        raise CommunityDashboardError("community.dashboard.template.invalid")
    return document


def _money_filter(currency: str, currency_scale: int, dimension: str) -> str:
    return (
        f'iip_ai_allocation_dimension="{dimension}",'
        'iip_ai_cost_basis="calculated-estimate",'
        f'iip_ai_currency="{currency}",'
        f'iip_ai_currency_scale="{currency_scale}",'
        'job="iip-ai-economics"'
    )


def _query_replacements(currency: str, currency_scale: int) -> Mapping[str, str]:
    divisor = 10**currency_scale
    application = _money_filter(currency, currency_scale, "application")
    team = _money_filter(currency, currency_scale, "team")
    fixed_saving = (
        'iip_ai_cost_basis="calculated-estimate",'
        f'iip_ai_currency="{currency}",'
        f'iip_ai_currency_scale="{currency_scale}",'
        'job="iip-ai-economics"'
    )
    return {
        "__IIP_COMMUNITY_ROLLING_COST_TOTAL__": (
            f"sum(iip_ai_allocation_cost_amount{{{application}}}) / {divisor}"
        ),
        "__IIP_COMMUNITY_APPLICATION_COST__": (
            "sum by (iip_ai_application_id,iip_ai_allocation_status) "
            f"(iip_ai_allocation_cost_amount{{{application}}}) / {divisor}"
        ),
        "__IIP_COMMUNITY_TEAM_COST__": (
            "sum by (iip_ai_team_id,iip_ai_allocation_status) "
            f"(iip_ai_allocation_cost_amount{{{team}}}) / {divisor}"
        ),
        "__IIP_COMMUNITY_FIXED_SAVING__": (
            f"iip_ai_savings_potential_amount{{{fixed_saving}}} / {divisor}"
        ),
    }


def _render(
    value: object,
    *,
    currency: str,
    currency_scale: int,
    queries: Mapping[str, str],
) -> object:
    if isinstance(value, dict):
        return {
            key: _render(
                item,
                currency=currency,
                currency_scale=currency_scale,
                queries=queries,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _render(
                item,
                currency=currency,
                currency_scale=currency_scale,
                queries=queries,
            )
            for item in value
        ]
    if isinstance(value, str):
        if value in queries:
            return queries[value]
        return value.replace("__IIP_CURRENCY__", currency).replace(
            "__IIP_CURRENCY_SCALE__", str(currency_scale)
        )
    return value


def _validate_state(state: Path) -> None:
    try:
        metadata = state.lstat()
    except OSError as error:
        raise CommunityDashboardError("community.dashboard.state.invalid") from error
    if (
        not state.is_absolute()
        or state.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise CommunityDashboardError("community.dashboard.state.invalid")


def write_dashboard(state: Path, currency: str, currency_scale: int) -> Path:
    """Create ``state/dashboard.json`` once with owner-only permissions.

    ``currency`` and ``currency_scale`` must come from the already validated,
    single-tenant price catalog. The generated PromQL always selects one
    allocation dimension for totals and an exact money representation.
    """

    if (
        not isinstance(state, Path)
        or not isinstance(currency, str)
        or _CURRENCY.fullmatch(currency) is None
        or isinstance(currency_scale, bool)
        or not isinstance(currency_scale, int)
        or currency_scale not in _CURRENCY_SCALES
    ):
        raise CommunityDashboardError("community.dashboard.configuration.invalid")
    _validate_state(state)
    template = _load_template()
    rendered = _render(
        template,
        currency=currency,
        currency_scale=currency_scale,
        queries=_query_replacements(currency, currency_scale),
    )
    if not isinstance(rendered, dict) or rendered.get("uid") != _DASHBOARD_UID:
        raise CommunityDashboardError("community.dashboard.template.invalid")
    payload = json.dumps(
        rendered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    if _MARKER_PREFIX in payload:
        raise CommunityDashboardError("community.dashboard.template.invalid")

    destination = state / OUTPUT_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload + "\n")
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o600)
    except FileExistsError as error:
        raise CommunityDashboardError("community.dashboard.exists") from error
    except OSError as error:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise CommunityDashboardError("community.dashboard.write-failed") from error
    return destination


if __name__ == "__main__":
    raise SystemExit(
        "community_dashboard is an installer helper, not a standalone command"
    )
