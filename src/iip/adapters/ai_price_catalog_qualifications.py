"""Protected JSON configuration adapter for AI price qualification evidence."""

from __future__ import annotations

import json
from typing import Mapping

from iip.application.calculate_ai_cost import AiCostConfigurationError


def ai_price_catalog_qualifications_from_json(
    raw: str,
) -> tuple[
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
]:
    """Parse the closed policy/report wrapper without interpreting authority."""

    try:
        if not isinstance(raw, str) or not 2 <= len(raw) <= 16 * 1024 * 1024:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"policies", "reports"}:
            raise ValueError
        policies = document["policies"]
        reports = document["reports"]
        if (
            not isinstance(policies, list)
            or not isinstance(reports, list)
            or not 1 <= len(policies) <= 1000
            or not 1 <= len(reports) <= 1000
            or any(not isinstance(item, dict) for item in policies)
            or any(not isinstance(item, dict) for item in reports)
        ):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AiCostConfigurationError("ai.cost.configuration.invalid") from None
    return tuple(policies), tuple(reports)


__all__ = ["ai_price_catalog_qualifications_from_json"]
