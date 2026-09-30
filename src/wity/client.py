"""Clients for the Wity API. ``WityClient`` blocks. ``AsyncWityClient`` is for ``await``."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
import pydantic

from ._logging import apply_env_level, logger, safe_name
from ._retry import backoff, delay_before_retry, should_retry_status
from .errors import APIConnectionError, APIError, APITimeoutError, WityError, request_id_from
from .types import Choice, GenerateResponse, Noul, Question, Reasoning, Score, State, SystemOneResponse

DEFAULT_BASE_URL = "https://wity-proxy-production-2c33.up.railway.app"
"""The production API address.

If it ever moves: change this line, bump the version and publish.
Until users update, they can set WITY_BASE_URL or the ``base_url`` option.
"""

DEFAULT_TIMEOUT = 60.0
"""Seconds. Thinking can take a few seconds. Longer ``max_latency_ms`` values raise the timeout automatically."""
DEFAULT_MAX_RETRIES = 2

_LATENCY_MARGIN = 10.0
"""Extra seconds on top of ``max_latency_ms`` for the network and Wity's own work."""

_MAX_BODY_BYTES = 10 * 1024 * 1024
"""Wity's answers are a few kB. A body far bigger than that isn't a Wity answer, so the SDK stops reading."""

_KNOWN_FIELDS = {
    # Top-level fields each route accepts, from the API docs. `model` is accepted and ignored by Wity.
    "/v1/systemone": frozenset({"state", "questions", "reasoning", "max_latency_ms", "image", "model"}),
    "/v1/generate": frozenset({"state", "instructions", "shape", "max_tokens", "image", "model"}),
}

_KEY_PATTERN = re.compile(r"[\x21-\x7e]+")
"""Printable ASCII only, no spaces."""

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class _Secret:
    """Holds the API key. Printing it, or the client that holds it, shows ``***``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "'***'"


def _read_env(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _check_base_url(raw: str) -> str:
    """Check the base URL and return it without a trailing slash.

    Error messages never repeat the URL, in case a key was pasted into it by mistake.
    """
    invalid = WityError("The Wity base URL is not a valid URL. Check WITY_BASE_URL or the base_url option.")
    try:
        url = urlsplit(raw.strip())
        host = url.hostname
        url.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        raise invalid from None
    if not url.scheme or not host or " " in raw.strip():
        raise invalid
    is_local_http = url.scheme == "http" and host in _LOCAL_HOSTS
    if url.scheme != "https" and not is_local_http:
        raise WityError(
            "The Wity base URL must start with https:// so the API key is encrypted on the way. "
            "Plain http:// is only allowed for localhost."
        )
    if url.username is not None or url.password is not None:
        raise WityError("The Wity base URL must not contain a username or password.")
    if url.query or url.fragment or "?" in raw or "#" in raw:
        raise WityError("The Wity base URL must not contain ? or #.")
    return f"{url.scheme}://{url.netloc}{url.path.rstrip('/')}"


def _check_number(value: object, name: str) -> float:
    """A positive, finite number of seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise WityError(f"{name} must be a positive number of seconds.")
    return float(value)


def _question_json(name: str, question: object) -> Any:
    if isinstance(question, (Choice, Noul, Score)):
        return question.model_dump(mode="json", exclude_none=True)
    if isinstance(question, Mapping):
        # Plain dicts are sent as they are, so question fields newer than this SDK still work.
        return dict(question)
    raise WityError(f"Question {safe_name(name)!r} must be built with choice(), noul() or score(), or be a dict.")


def _parse_body(raw: bytes) -> Any:
    """Parse a body as JSON, or keep it as text. ``None`` when empty."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _format_seconds(seconds: float) -> str:
    return f"{seconds:g} s"


@dataclass(frozen=True)
class _Prepared:
    """One request, ready to send. The same bytes go out on every retry."""

    route: str
    url: str
    payload: bytes
    timeout: float
    summary: str
    """What debug logs about the request. Never the key, never the ``state`` text."""
    unknown_note: str | None


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: httpx.Headers
    data: Any


class _BaseClient:
    """Setup and retry decisions shared by both clients. The clients only send and wait."""

    base_url: str
    """The address requests go to. Useful to check which API you're calling."""
    timeout: float
    """Seconds per attempt."""
    max_retries: int

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        max_retries: int,
    ) -> None:
        if sys.platform == "emscripten":
            raise WityError(
                "The Wity client can't run in a browser. Your API key would be visible to anyone using the page. "
                "Call Wity from your server instead."
            )

        # An api_key passed in code always wins, even an empty one. Quietly falling back
        # to WITY_API_KEY could bill a different account.
        if api_key is not None:
            key = api_key.strip()
            if not key:
                raise WityError(
                    "The api_key option is empty. Pass a real key, or leave api_key out to use WITY_API_KEY."
                )
        else:
            key = _read_env("WITY_API_KEY") or ""
            if not key:
                raise WityError(
                    "No API key found. Set the WITY_API_KEY environment variable, or pass api_key to the client."
                )
        # A bad character would make httpx raise an error that quotes the whole header, key included.
        if not _KEY_PATTERN.fullmatch(key):
            raise WityError("The API key contains spaces or unusual characters. Check WITY_API_KEY.")
        self._api_key = _Secret(key)

        self.base_url = _check_base_url(base_url or _read_env("WITY_BASE_URL") or DEFAULT_BASE_URL)
        self.timeout = _check_number(timeout, "timeout")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise WityError("max_retries must be a whole number, 0 or more.")
        self.max_retries = max_retries
        apply_env_level()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.get()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- building requests ---------------------------------------------------

    def _prepare_system_one(
        self,
        state: State,
        questions: Mapping[str, Question | Mapping[str, Any]],
        reasoning: Reasoning | None,
        max_latency_ms: float | None,
        image: str | None,
        extra_body: Mapping[str, Any] | None,
    ) -> _Prepared:
        if not isinstance(questions, Mapping):
            raise WityError("questions must be a dict of question name to question, for example {'team': choice(...)}.")
        body = {
            "state": state,
            "questions": {name: _question_json(name, q) for name, q in questions.items()},
            "reasoning": reasoning,
            "max_latency_ms": max_latency_ms,
            "image": image,
        }
        summary = f"questions={list(questions)!r} reasoning={reasoning or 'default'!r}"
        # Give Wity the full time it was allowed, plus room for the network.
        min_timeout = 0.0
        latency = max_latency_ms
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency):
            min_timeout = latency / 1000 + _LATENCY_MARGIN
        return self._prepare("/v1/systemone", body, extra_body, summary, min_timeout)

    def _prepare_generate(
        self,
        instructions: str,
        state: State | None,
        shape: Mapping[str, Any] | None,
        max_tokens: int | None,
        image: str | None,
        extra_body: Mapping[str, Any] | None,
    ) -> _Prepared:
        body = {
            "state": state,
            "instructions": instructions,
            "shape": shape,
            "max_tokens": max_tokens,
            "image": image,
        }
        summary = f"shape={shape is not None} max_tokens={max_tokens or 'default'!r}"
        return self._prepare("/v1/generate", body, extra_body, summary, 0.0)

    def _prepare(
        self,
        route: str,
        fields: dict[str, Any],
        extra_body: Mapping[str, Any] | None,
        summary: str,
        min_timeout: float,
    ) -> _Prepared:
        body = {name: value for name, value in fields.items() if value is not None}
        if extra_body is not None:
            if not isinstance(extra_body, Mapping):
                raise WityError("extra_body must be a dict.")
            body.update(extra_body)

        # Fields in extra_body are sent, so new API fields work without an SDK update.
        # They're never sent silently: a warning names them, and so does a 400 error.
        unknown = [safe_name(name) for name in body if name not in _KNOWN_FIELDS[route]]
        unknown_note = f"The request included fields Wity may not know: {', '.join(unknown)}." if unknown else None
        if unknown_note:
            logger.warning("%s: %s Check for typos.", route, unknown_note)

        try:
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError) as err:
            raise WityError(
                "The request can't be turned into JSON. Look for NaN, sets, dates or loops in state."
            ) from err

        return _Prepared(
            route=route,
            url=f"{self.base_url}{route}",
            payload=payload,
            timeout=max(self.timeout, min_timeout),
            summary=summary,
            unknown_note=unknown_note,
        )

    # -- deciding what happens after each attempt ------------------------------

    def _log_sending(self, p: _Prepared, attempt: int) -> None:
        logger.debug(
            "%s: sending (attempt %d of %d) url=%s bytes=%d %s",
            p.route,
            attempt + 1,
            self.max_retries + 1,
            p.url,
            len(p.payload),
            p.summary,
        )

    def _retry_label(self, attempt: int) -> str:
        return f"retry {attempt + 1} of {self.max_retries}"

    def _delay_after_failed_send(self, p: _Prepared, attempt: int, err: WityError, started: float) -> float:
        """Seconds to wait before retrying a broken connection. Raises ``err`` if it shouldn't be retried."""
        ms = round((time.monotonic() - started) * 1000)
        if isinstance(err, APIConnectionError) and attempt < self.max_retries:
            delay = backoff(attempt)
            logger.info(
                "%s: connection failed after %d ms. Retrying in %d ms (%s). Cause: %s",
                p.route,
                ms,
                round(delay * 1000),
                self._retry_label(attempt),
                err.__cause__,
            )
            return delay
        logger.info("%s: %s (after %d ms)", p.route, err, ms)
        raise err

    def _after_reply(self, p: _Prepared, attempt: int, reply: _Reply, started: float) -> dict[str, Any] | float:
        """The answer's data on success, or seconds to wait before a retry. Raises on errors not worth retrying."""
        request_id = request_id_from(reply.headers)
        ms = round((time.monotonic() - started) * 1000)
        logger.debug("%s: got %d in %d ms request_id=%s", p.route, reply.status, ms, request_id)

        if 200 <= reply.status < 300:
            if not isinstance(reply.data, dict):
                raise WityError("Wity answered with something that isn't a JSON object.")
            data: dict[str, Any] = reply.data
            return data

        if attempt < self.max_retries and should_retry_status(reply.status):
            delay = delay_before_retry(attempt, reply.headers)
            if delay is not None:
                logger.info(
                    "%s: got %d. Retrying in %d ms (%s). request_id=%s",
                    p.route,
                    reply.status,
                    round(delay * 1000),
                    self._retry_label(attempt),
                    request_id,
                )
                return delay
            logger.warning(
                "%s: got %d, and Retry-After asks for more than 60 s. Not retrying. request_id=%s",
                p.route,
                reply.status,
                request_id,
            )
        note = p.unknown_note if reply.status == 400 else None
        raise APIError.from_response(reply.status, reply.data, reply.headers, note)

    def _timeout_error(self, p: _Prepared) -> APITimeoutError:
        # Timeouts aren't retried: the request may have reached Wity, and the caller already waited.
        return APITimeoutError(f"Wity didn't answer within {_format_seconds(p.timeout)}.")

    def _connection_error(self) -> APIConnectionError:
        return APIConnectionError(
            f"Couldn't reach the Wity API at {self.base_url}, or the connection broke while reading its answer."
        )

    def _too_large_error(self) -> WityError:
        return WityError(
            f"The answer is larger than {_MAX_BODY_BYTES // 1024 // 1024} MB, so it can't be from Wity. "
            "Check WITY_BASE_URL or the base_url option."
        )


_Model = TypeVar("_Model", bound=pydantic.BaseModel)


def _parse(model: type[_Model], data: dict[str, Any]) -> _Model:
    try:
        return model.model_validate(data)
    except pydantic.ValidationError as err:
        raise WityError(
            "Wity's answer doesn't have the shape this SDK expects. Update the wity package, or check WITY_BASE_URL."
        ) from err


class WityClient(_BaseClient):
    """Client for the Wity API. Use it on a server only: it holds your API key.

    Use it with ``with``, or call ``close()`` when you're done.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        """
        Args:
            api_key: Defaults to the WITY_API_KEY environment variable.
            base_url: Defaults to WITY_BASE_URL, then ``DEFAULT_BASE_URL``.
                Must be https. Plain http is allowed only for localhost.
            timeout: Seconds per attempt. Default 60.
            max_retries: How many times to retry 408, 429, 5xx and network failures. Default 2.
            http_client: Your own ``httpx.Client``, for example for proxies or HTTP/2.
                The SDK doesn't close a client you pass in.
        """
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client()

    def __enter__(self) -> WityClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection pool. Does nothing to an ``http_client`` you passed in."""
        if self._owns_http:
            self._http.close()

    def system_one(
        self,
        *,
        state: State,
        questions: Mapping[str, Question | Mapping[str, Any]],
        reasoning: Reasoning | None = None,
        max_latency_ms: float | None = None,
        image: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> SystemOneResponse:
        """Ask one or more typed questions about ``state``.

        Answers come back under the same names as the questions.

        Args:
            state: What the decision is about. Dicts and lists are read as JSON.
            questions: Questions by a name you choose, built with ``choice``, ``noul`` or ``score``.
            reasoning: ``"auto"`` (the API default), ``"off"`` or ``"always"``.
            max_latency_ms: Time limit per request, 200 to 120000 ms. Thinking stops early to meet it.
                The SDK waits at least this long plus 10 s before timing out.
            image: One image as a data URL: ``data:image/png;base64,...``.
            extra_body: Extra top-level fields to send, for API fields newer than this SDK.
        """
        p = self._prepare_system_one(state, questions, reasoning, max_latency_ms, image, extra_body)
        return _parse(SystemOneResponse, self._post(p))

    def generate(
        self,
        *,
        instructions: str,
        state: State | None = None,
        shape: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        image: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> GenerateResponse:
        """Write short text from ``state``, or JSON that matches ``shape``.

        Args:
            instructions: What to write.
            state: What to write from. Dicts and lists are read as JSON.
            shape: A JSON Schema with ``"type": "object"`` at the top. Leave out for free text.
            max_tokens: 1 to 512. The API default is 128.
            image: One image as a data URL: ``data:image/png;base64,...``.
            extra_body: Extra top-level fields to send, for API fields newer than this SDK.
        """
        p = self._prepare_generate(instructions, state, shape, max_tokens, image, extra_body)
        return _parse(GenerateResponse, self._post(p))

    def _post(self, p: _Prepared) -> dict[str, Any]:
        attempt = 0
        while True:
            self._log_sending(p, attempt)
            started = time.monotonic()
            try:
                reply = self._send(p)
            except WityError as err:
                time.sleep(self._delay_after_failed_send(p, attempt, err, started))
                attempt += 1
                continue
            outcome = self._after_reply(p, attempt, reply, started)
            if isinstance(outcome, dict):
                return outcome
            time.sleep(outcome)
            attempt += 1

    def _send(self, p: _Prepared) -> _Reply:
        """One attempt: send the request and read the whole answer."""
        deadline = time.monotonic() + p.timeout
        request = self._http.build_request(
            "POST", p.url, content=p.payload, headers=self._headers(), timeout=httpx.Timeout(p.timeout)
        )
        try:
            # Never follow a redirect: it could carry the key to an address nobody chose.
            response = self._http.send(request, stream=True, follow_redirects=False)
            try:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_BODY_BYTES:
                        raise self._too_large_error()
                    # A server that trickles bytes slowly still ends within about one timeout.
                    if time.monotonic() > deadline:
                        raise self._timeout_error(p)
                    chunks.append(chunk)
            finally:
                response.close()
        except httpx.TimeoutException as err:
            raise self._timeout_error(p) from err
        except httpx.RequestError as err:
            raise self._connection_error() from err
        return _Reply(response.status_code, response.headers, _parse_body(b"".join(chunks)))


class AsyncWityClient(_BaseClient):
    """Async client for the Wity API. Use it on a server only: it holds your API key.

    Use it with ``async with``, or call ``await close()`` when you're done.
    Cancel a call the usual asyncio way, for example with ``asyncio.timeout()``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Takes the same options as ``WityClient``, with an ``httpx.AsyncClient`` for ``http_client``."""
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient()

    async def __aenter__(self) -> AsyncWityClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the connection pool. Does nothing to an ``http_client`` you passed in."""
        if self._owns_http:
            await self._http.aclose()

    async def system_one(
        self,
        *,
        state: State,
        questions: Mapping[str, Question | Mapping[str, Any]],
        reasoning: Reasoning | None = None,
        max_latency_ms: float | None = None,
        image: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> SystemOneResponse:
        """Ask one or more typed questions about ``state``. Same arguments as ``WityClient.system_one``."""
        p = self._prepare_system_one(state, questions, reasoning, max_latency_ms, image, extra_body)
        return _parse(SystemOneResponse, await self._post(p))

    async def generate(
        self,
        *,
        instructions: str,
        state: State | None = None,
        shape: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        image: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> GenerateResponse:
        """Write short text, or JSON that matches ``shape``. Same arguments as ``WityClient.generate``."""
        p = self._prepare_generate(instructions, state, shape, max_tokens, image, extra_body)
        return _parse(GenerateResponse, await self._post(p))

    async def _post(self, p: _Prepared) -> dict[str, Any]:
        attempt = 0
        while True:
            self._log_sending(p, attempt)
            started = time.monotonic()
            try:
                reply = await self._send(p)
            except WityError as err:
                await asyncio.sleep(self._delay_after_failed_send(p, attempt, err, started))
                attempt += 1
                continue
            outcome = self._after_reply(p, attempt, reply, started)
            if isinstance(outcome, dict):
                return outcome
            await asyncio.sleep(outcome)
            attempt += 1

    async def _send(self, p: _Prepared) -> _Reply:
        """One attempt: send the request and read the whole answer."""
        deadline = time.monotonic() + p.timeout
        request = self._http.build_request(
            "POST", p.url, content=p.payload, headers=self._headers(), timeout=httpx.Timeout(p.timeout)
        )
        try:
            # Never follow a redirect: it could carry the key to an address nobody chose.
            response = await self._http.send(request, stream=True, follow_redirects=False)
            try:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_BODY_BYTES:
                        raise self._too_large_error()
                    # A server that trickles bytes slowly still ends within about one timeout.
                    if time.monotonic() > deadline:
                        raise self._timeout_error(p)
                    chunks.append(chunk)
            finally:
                await response.aclose()
        except httpx.TimeoutException as err:
            raise self._timeout_error(p) from err
        except httpx.RequestError as err:
            raise self._connection_error() from err
        return _Reply(response.status_code, response.headers, _parse_body(b"".join(chunks)))
