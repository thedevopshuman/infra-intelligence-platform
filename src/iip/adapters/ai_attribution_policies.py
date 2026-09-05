"""Protected JSON configuration adapter for tenant AI attribution policies."""

from __future__ import annotations

import json
from typing import Mapping

from iip.application.attribute_ai_usage import AiAttributionConfigurationError


def ai_attribution_policies_from_json(
    raw: str,
) -> tuple[Mapping[str, object], ...]:
    try:
        if not isinstance(raw, str) or not 2 <= len(raw) <= 8 * 1024 * 1024:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"policies"}:
            raise ValueError
        policies = document["policies"]
        if not isinstance(policies, list) or not 1 <= len(policies) <= 1000:
            raise ValueError
        if any(not isinstance(item, dict) for item in policies):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.invalid"
        ) from None
    return tuple(policies)


__all__ = ["ai_attribution_policies_from_json"]
