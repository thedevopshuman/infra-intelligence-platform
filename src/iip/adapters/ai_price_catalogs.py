"""Protected JSON configuration adapter for tenant AI price catalogs."""

from __future__ import annotations

import json
from typing import Mapping

from iip.application.calculate_ai_cost import AiCostConfigurationError


def ai_price_catalogs_from_json(raw: str) -> tuple[Mapping[str, object], ...]:
    try:
        if not isinstance(raw, str) or not 2 <= len(raw) <= 16 * 1024 * 1024:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"catalogs"}:
            raise ValueError
        catalogs = document["catalogs"]
        if not isinstance(catalogs, list) or not 1 <= len(catalogs) <= 1000:
            raise ValueError
        if any(not isinstance(item, dict) for item in catalogs):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AiCostConfigurationError("ai.cost.configuration.invalid") from None
    return tuple(catalogs)


__all__ = ["ai_price_catalogs_from_json"]
