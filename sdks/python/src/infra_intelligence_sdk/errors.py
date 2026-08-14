"""Stable SDK error types."""


class ApiError(RuntimeError):
    """API failure with a stable platform error code."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"platform request failed ({status}, {code})")
        self.status = status
        self.code = code

