"""Provider-neutral readiness adapter for dependency-free local profiles."""

from __future__ import annotations


class AlwaysReadyProbe:
    """Mark an in-process profile ready after successful composition."""

    def check(self) -> None:
        return None
