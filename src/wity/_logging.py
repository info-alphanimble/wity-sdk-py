"""Logging through Python's standard ``logging`` module, under the logger name ``wity``.

Warnings show by default. WITY_LOG_LEVEL turns it up or down.
Nothing here ever logs the API key or the ``state`` text.
"""

from __future__ import annotations

import logging
import os
import unicodedata

from .errors import WityError

logger = logging.getLogger("wity")

_OFF = logging.CRITICAL + 10
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "off": _OFF,
}


class _WityHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Prints to stderr with a ``[wity]`` prefix. Added only when WITY_LOG_LEVEL asks for more than warnings."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("[wity] %(message)s"))


def apply_env_level() -> None:
    """Set the ``wity`` logger's level from WITY_LOG_LEVEL, if it's set. Safe to call more than once."""
    raw = os.environ.get("WITY_LOG_LEVEL", "").strip().lower()
    if not raw:
        return
    if raw not in _LEVELS:
        raise WityError("WITY_LOG_LEVEL must be one of: debug, info, warn, error, off.")
    logger.setLevel(_LEVELS[raw])
    # Without any logging set up, Python only prints warnings and above.
    # So for debug and info, add a handler, unless the app has set up logging itself.
    wants_more = _LEVELS[raw] < logging.WARNING
    has_own = any(isinstance(h, _WityHandler) for h in logger.handlers)
    if wants_more and not has_own and not logging.getLogger().handlers:
        logger.addHandler(_WityHandler())


def safe_name(name: object) -> str:
    """Line breaks and other control characters become "?", so a field name can't fake a new log line."""
    return "".join("?" if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in str(name))
