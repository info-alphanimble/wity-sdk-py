"""Shared test helpers: a fake Wity server, and one way to call both clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

import wity
import wity._retry
import wity.client

TEST_KEY = "wity_test_secret_1234567890"
"""A made-up key. Tests check that it never leaks into errors or logs."""

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    """A real API response: ``{"status": ..., "body": ...}``. Copied from wity-sdk-ts/test/fixtures/."""
    saved: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return saved


# The same questions the fixtures were captured with.
TICKET = "I was charged twice for my subscription this month. Please refund one of them today."
TEAM = wity.choice(
    "Which team should handle this ticket?",
    {"billing": "Payments, charges and refunds", "technical": "Bugs, errors and outages", "other": "Anything else"},
)
URGENT = wity.noul(
    "Does the customer ask for something to happen soon?", yes="They ask for fast action", no="No time pressure"
)
SEVERITY = wity.score("How much is this hurting the customer?", ["Not at all", "A little", "A lot"])


@dataclass
class Reply:
    """What the fake server answers."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    stall: float = 0.0
    """Send half the body, wait this many seconds, then send the rest."""
    break_body: bool = False
    """Send half the body, then break the connection."""


def reply(name: str, **extra: Any) -> Reply:
    """A reply made from a saved fixture."""
    saved = fixture(name)
    return Reply(status=saved["status"], body=saved["body"], **extra)


class _Body(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A response body that works for both clients, and can stall or break halfway."""

    def __init__(self, data: bytes, stall: float, break_body: bool) -> None:
        self.data, self.stall, self.break_body = data, stall, break_body

    def _halves(self) -> tuple[bytes, bytes]:
        middle = len(self.data) // 2
        return self.data[:middle], self.data[middle:]

    def __iter__(self) -> Iterator[bytes]:
        first, rest = self._halves()
        yield first
        if self.break_body:
            raise httpx.ReadError("connection reset")
        if self.stall:
            time.sleep(self.stall)
        yield rest

    async def __aiter__(self) -> AsyncIterator[bytes]:
        first, rest = self._halves()
        yield first
        if self.break_body:
            raise httpx.ReadError("connection reset")
        if self.stall:
            await asyncio.sleep(self.stall)
        yield rest


class FakeServer:
    """Gives the replies in order (the last one repeats) and records every request.

    A reply can also be an exception, which is raised instead of answering.
    """

    def __init__(self, *replies: Reply | Exception) -> None:
        if not replies:
            raise ValueError("FakeServer needs at least one reply")
        self.replies = replies
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        answer = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(answer, Exception):
            raise answer
        body = answer.body
        data = b"" if body is None else body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return httpx.Response(
            answer.status, headers=answer.headers, stream=_Body(data, answer.stall, answer.break_body)
        )

    def sent(self, index: int = 0) -> dict[str, Any]:
        """The JSON body of a recorded request."""
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


class Caller:
    """Calls WityClient or AsyncWityClient the same way, so each test runs against both."""

    def __init__(self, kind: str, server: FakeServer | None, **options: Any) -> None:
        self.kind = kind
        self.client: wity.WityClient | wity.AsyncWityClient
        if kind == "sync":
            http = httpx.Client(transport=httpx.MockTransport(server.handle)) if server else None
            self.client = wity.WityClient(http_client=http, **options)
        else:
            ahttp = httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) if server else None
            self.client = wity.AsyncWityClient(http_client=ahttp, **options)

    def system_one(self, **request: Any) -> wity.SystemOneResponse:
        if isinstance(self.client, wity.WityClient):
            return self.client.system_one(**request)
        return asyncio.run(self.client.system_one(**request))

    def generate(self, **request: Any) -> wity.GenerateResponse:
        if isinstance(self.client, wity.WityClient):
            return self.client.generate(**request)
        return asyncio.run(self.client.generate(**request))


@pytest.fixture(params=["sync", "async"])
def kind(request: pytest.FixtureRequest) -> str:
    """Runs a test once with WityClient and once with AsyncWityClient."""
    param: str = request.param
    return param


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the real environment and earlier tests' logging setup out of each test."""
    for name in ("WITY_API_KEY", "WITY_BASE_URL", "WITY_LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    log = logging.getLogger("wity")
    level, handlers = log.level, list(log.handlers)
    yield
    log.setLevel(level)
    log.handlers[:] = handlers


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """No waiting between retries, so tests stay fast."""
    monkeypatch.setattr(wity.client, "backoff", lambda attempt: 0.0)
    monkeypatch.setattr(wity._retry, "backoff", lambda attempt: 0.0)
