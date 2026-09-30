"""Real calls through both clients to the real API.

Skipped unless you run:

    WITY_LIVE=1 uv run --env-file ../.env pytest tests/test_live.py

It reads WITY_API_KEY and WITY_BASE_URL from ../.env.
Two calls are billed (a tiny amount). The two error calls are free.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from wity import AsyncWityClient, AuthenticationError, BadRequestError, WityClient, choice, noul, score

pytestmark = pytest.mark.skipif(os.environ.get("WITY_LIVE") != "1", reason="set WITY_LIVE=1 to call the real API")

TICKET = "I was charged twice for my subscription this month. Please refund one of them today."


# Read when pytest loads this file, before the shared clean_env fixture removes them for each test.
_real_env = {name: os.environ.get(name, "") for name in ("WITY_API_KEY", "WITY_BASE_URL")}


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the real key and address back."""
    for name, value in _real_env.items():
        if value:
            monkeypatch.setenv(name, value)


@pytest.mark.usefixtures("live_env")
def test_system_one_answers_all_three_question_types() -> None:
    with WityClient() as client:
        res = client.system_one(
            state=TICKET,
            questions={
                "team": choice("Which team should handle this ticket?", {"billing": "Payments", "technical": "Bugs"}),
                "urgent": noul("Does the customer ask for something to happen soon?"),
                "upset": score("How upset is the customer?", ["Calm", "Annoyed", "Angry"]),
            },
            reasoning="off",
        )
    assert res.model == "wity-1"
    team = res.choices["team"]
    assert team.choice in ("billing", "technical")
    assert sum(team.probabilities.values()) == pytest.approx(1, abs=1e-5)
    assert 0 <= res.nouls["urgent"].noul <= 1
    assert 0 <= res.scores["upset"].score <= 2
    assert res.usage.input_tokens > 0


@pytest.mark.usefixtures("live_env")
def test_generate_returns_json_in_the_shape_through_the_async_client() -> None:
    async def run() -> None:
        async with AsyncWityClient() as client:
            res = await client.generate(
                state="Ticket scan: LISBOA (LIS) -> BERLIN BRANDENBURG (BER), 14 Oct, seat 23C.",
                instructions="Origin city name, in English",
                shape={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                max_tokens=40,
            )
        assert res.finish_reason == "stop"
        assert res.value is not None
        assert isinstance(res.value["city"], str)

    asyncio.run(run())


@pytest.mark.usefixtures("live_env")
def test_a_wrong_key_gives_authentication_error_with_a_request_id() -> None:
    with pytest.raises(AuthenticationError) as info, WityClient(api_key="wity_not_a_real_key") as client:
        client.system_one(state="x", questions={"q": noul("x?")})
    assert isinstance(info.value.request_id, str)


@pytest.mark.usefixtures("live_env")
def test_an_unknown_field_is_sent_rejected_and_named_in_the_error() -> None:
    with pytest.raises(BadRequestError, match="fields Wity may not know: not_a_field"), WityClient() as client:
        client.system_one(state="x", questions={"q": noul("x?")}, extra_body={"not_a_field": True})
