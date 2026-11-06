"""Structured engine errors. Nothing but these crosses the engine boundary."""

from __future__ import annotations


class EngineError(Exception):
    """A calculation cannot be performed (invalid or inconsistent inputs)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
