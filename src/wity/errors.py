"""Every error the SDK raises extends WityError, so one ``except`` can handle them all.

None of them ever contain the API key.
"""

from __future__ import annotations

from typing import Any

import httpx


class WityError(Exception):
    """Base class for all SDK errors. Also raised for setup problems, like a missing key."""


_MOVED_HINT = "The API address may have changed. Set WITY_BASE_URL to the new address, or update the wity package."


def _hint_for(status: int) -> str | None:
    """Extra advice added to the message for some statuses."""
    if 300 <= status < 400:
        return f"The API answered with a redirect, which this SDK never follows. {_MOVED_HINT}"
    if status == 401:
        return "Check WITY_API_KEY."
    if status == 402:
        return "The account has no balance left."
    if status == 404:
        return _MOVED_HINT
    return None


_MAX_TEXT_IN_MESSAGE = 200


def _describe_list(items: list[Any]) -> str | None:
    """Turn a list of validation errors (``[{loc, msg}, ...]``) into "field: problem; field: problem"."""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("msg"), str):
            continue
        loc = item.get("loc")
        where = ".".join(str(part) for part in loc if part != "body") if isinstance(loc, list) else ""
        parts.append(f"{where}: {item['msg']}" if where else item["msg"])
    return "; ".join(parts) if parts else None


def _message_from(body: Any) -> str | None:
    """Read the message out of an error body.

    Wity uses ``{"error": "..."}`` on some routes and ``{"detail": "..."}`` on others.
    """
    if isinstance(body, str):
        text = body.strip()
        if text.startswith("<"):
            return "the server sent an HTML error page"
        if len(text) > _MAX_TEXT_IN_MESSAGE:
            return f"{text[:_MAX_TEXT_IN_MESSAGE]}…"
        return text or None
    if not isinstance(body, dict):
        return None
    for key in ("error", "detail", "message"):
        value = body.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return _describe_list(value)
        if isinstance(value, dict) and isinstance(value.get("message"), str):
            return str(value["message"])
    return None


def request_id_from(headers: httpx.Headers) -> str | None:
    """The id the server gave this request. Quote it when reporting a problem."""
    request_id: str | None = headers.get("x-request-id") or headers.get("x-railway-request-id")
    return request_id


class APIError(WityError):
    """The API answered with an error status."""

    status: int
    """HTTP status code, for example 400."""
    body: Any
    """The response body: parsed JSON, text, or ``None`` if empty."""
    headers: httpx.Headers
    """The response headers, for example ``Retry-After``."""
    request_id: str | None
    """The server's id for this request, if it sent one."""

    def __init__(self, status: int, body: Any, headers: httpx.Headers, note: str | None = None) -> None:
        # For example: "401 invalid API key. Check WITY_API_KEY."
        detail = _message_from(body)
        detail = detail.rstrip(".") if detail else None
        head = f"{status} {detail}" if detail else str(status)
        extra = " ".join(part for part in (_hint_for(status), note) if part)
        super().__init__(f"{head}. {extra}" if extra else head)
        self.status = status
        self.body = body
        self.headers = headers
        self.request_id = request_id_from(headers)

    @staticmethod
    def from_response(status: int, body: Any, headers: httpx.Headers, note: str | None = None) -> APIError:
        """Pick the error class that matches the status."""
        if status == 400:
            return BadRequestError(status, body, headers, note)
        if status == 401:
            return AuthenticationError(status, body, headers, note)
        if status == 402:
            return InsufficientBalanceError(status, body, headers, note)
        if status == 429:
            return RateLimitError(status, body, headers, note)
        if status >= 500:
            return InternalServerError(status, body, headers, note)
        return APIError(status, body, headers, note)


class BadRequestError(APIError):
    """400: the request is wrong. The message says which part."""


class AuthenticationError(APIError):
    """401: the API key is missing or wrong."""


class InsufficientBalanceError(APIError):
    """402: the account has no balance left."""


class RateLimitError(APIError):
    """429: too many requests. Raised after the SDK's retries run out."""


class InternalServerError(APIError):
    """5xx: a problem on Wity's side. Raised after the SDK's retries run out."""


class APIConnectionError(WityError):
    """The API couldn't be reached, or the connection broke while reading the answer."""


class APITimeoutError(WityError):
    """The API didn't answer within the timeout."""
