"""
Exception categorization.

Maps raised exceptions to a stable category string so failures can be logged
and persisted with structured context instead of a generic message.
"""

from git_provider import ProviderError
from orchestrator.opencode import OpenCodeError


def classify_exception(e: BaseException) -> str:
    """Return a stable category string for an exception."""
    if isinstance(e, ProviderError):
        return e.category or "provider"
    if isinstance(e, OpenCodeError):
        return "opencode"
    return "internal"
