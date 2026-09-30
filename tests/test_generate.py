from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import TEST_KEY, Caller, FakeServer, Reply, fixture, reply

from wity import DEFAULT_BASE_URL, AuthenticationError, BadRequestError

pytestmark = pytest.mark.usefixtures("fast_retries")

# The same inputs the fixtures were captured with.
FLIGHT = "Ticket scan: LISBOA (LIS) -> BERLIN BRANDENBURG (BER), 14 Oct, seat 23C."
CITY_SHAPE = {
    "type": "object",
    "properties": {"city": {"type": "string", "pattern": "^[A-Za-z ]{1,40}$"}},
    "required": ["city"],
}


def caller(kind: str, server: FakeServer, **options: Any) -> Caller:
    return Caller(kind, server, api_key=TEST_KEY, max_retries=0, **options)


class TestGenerateRequest:
    def test_sends_one_post_to_v1_generate_with_the_key(self, kind: str) -> None:
        server = FakeServer(reply("generate-shape"))
        caller(kind, server).generate(state=FLIGHT, instructions="Origin city", shape=CITY_SHAPE, max_tokens=40)

        assert len(server.requests) == 1
        request = server.requests[0]
        assert str(request.url) == f"{DEFAULT_BASE_URL}/v1/generate"
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        assert server.sent() == {
            "state": FLIGHT,
            "instructions": "Origin city",
            "shape": CITY_SHAPE,
            "max_tokens": 40,
        }

    def test_sends_only_instructions_when_thats_all_you_give(self, kind: str) -> None:
        server = FakeServer(reply("generate-text"))
        caller(kind, server).generate(instructions="Say hello")
        assert server.sent() == {"instructions": "Say hello"}

    def test_sends_image_when_given(self, kind: str) -> None:
        server = FakeServer(reply("generate-text"))
        image = "data:image/png;base64,iVBORw0KGgo="
        caller(kind, server).generate(instructions="Describe the photo", image=image)
        assert server.sent() == {"instructions": "Describe the photo", "image": image}

    def test_sends_extra_body_and_names_it_if_wity_rejects_the_request(self, kind: str) -> None:
        server = FakeServer(reply("error-generate-unknown-field"))
        with pytest.raises(BadRequestError, match=r"fields Wity may not know: user\."):
            caller(kind, server).generate(instructions="Say hello", extra_body={"user": "someone"})
        assert server.sent() == {"instructions": "Say hello", "user": "someone"}


class TestGenerateResponse:
    def test_returns_free_text_with_no_value(self, kind: str) -> None:
        res = caller(kind, FakeServer(reply("generate-text"))).generate(instructions="Reply")
        assert res.model_dump(mode="json", exclude_unset=True) == fixture("generate-text")["body"]
        assert res.finish_reason == "stop"
        assert res.value is None
        assert len(res.text) > 0

    def test_returns_the_parsed_value_when_a_shape_was_sent(self, kind: str) -> None:
        res = caller(kind, FakeServer(reply("generate-shape"))).generate(
            state=FLIGHT, instructions="Origin city", shape=CITY_SHAPE
        )
        assert res.model_dump(mode="json", exclude_unset=True) == fixture("generate-shape")["body"]
        assert res.value == {"city": "Lisbon"}
        assert json.loads(res.text) == res.value

    def test_returns_value_none_and_finish_reason_length_when_cut_off(self, kind: str) -> None:
        res = caller(kind, FakeServer(reply("generate-cut-off"))).generate(
            state=FLIGHT, instructions="x", shape=CITY_SHAPE
        )
        assert res.finish_reason == "length"
        assert res.value is None


class TestGenerateErrors:
    def test_400_becomes_bad_request_error_with_the_servers_message(self, kind: str) -> None:
        with pytest.raises(BadRequestError) as info:
            caller(kind, FakeServer(reply("error-generate-unknown-field"))).generate(instructions="x")
        # No unknown fields were sent this time, so the SDK adds nothing.
        assert str(info.value) == "400 unknown fields: ['user']"

    def test_401_becomes_authentication_error_without_the_key(self, kind: str) -> None:
        with pytest.raises(AuthenticationError) as info:
            caller(kind, FakeServer(reply("error-bad-key"))).generate(instructions="x")
        assert TEST_KEY not in repr(info.value)

    def test_retries_5xx_like_system_one(self, kind: str) -> None:
        server = FakeServer(Reply(502, {"error": "model error"}), reply("generate-text"))
        res = Caller(kind, server, api_key=TEST_KEY, max_retries=1).generate(instructions="Reply")
        assert res.finish_reason == "stop"
        assert len(server.requests) == 2
