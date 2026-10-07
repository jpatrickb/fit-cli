"""Structured errors (A26): a code an agent can branch on, a message, and suggestions. Never guess."""

import difflib


class FitError(Exception):
    def __init__(self, code: str, message: str, suggestions: list[str] | None = None, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestions = suggestions or []
        self.details = details

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "suggestions": self.suggestions, **self.details}


def closest(text: str, options: list[str], n: int = 5) -> list[str]:
    """Close spellings first, then options containing the text."""
    lowered = {o.lower(): o for o in options}
    found = [lowered[m] for m in difflib.get_close_matches(text.lower(), list(lowered), n=n, cutoff=0.5)]
    found += [o for o in options if text.lower() in o.lower() and o not in found]
    return found[:n]
