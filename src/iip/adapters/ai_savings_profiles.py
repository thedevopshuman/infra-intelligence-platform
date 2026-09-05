"""Protected JSON configuration adapter for deterministic AI savings scopes."""

from __future__ import annotations

import json
from typing import Mapping

from iip.application.evaluate_ai_savings import AiSavingsConfigurationError


def ai_savings_profiles_from_json(raw: str) -> tuple[Mapping[str, object], ...]:
    try:
        if not isinstance(raw, str) or not 2 <= len(raw) <= 4 * 1024 * 1024:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"profiles"}:
            raise ValueError
        profiles = document["profiles"]
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 1000:
            raise ValueError
        if any(not isinstance(item, dict) for item in profiles):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AiSavingsConfigurationError(
            "ai.savings.configuration.invalid"
        ) from None
    return tuple(profiles)


__all__ = ["ai_savings_profiles_from_json"]
