from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx
import pytest
from conftest import SEVERITY, TEAM, TEST_KEY, TICKET, URGENT, Caller, FakeServer, Reply, fixture, reply

import wity
from wity import (
    DEFAULT_BASE_URL,
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InsufficientBalanceError,
    InternalServerError,
    RateLimitError,
    WityError,
)

pytestmark = pytest.mark.usefixtures("fast_retries")

TEAM_JSON = {
    "type": "choice",
    "instructions": "Which team should handle this ticket?",
    "criteria": {
        "billing": "Payments, charges and refunds",
        "technical": "Bugs, errors and outages",
        "other": "Anything else",
    },
}


def caller(kind: str, server: FakeServer, **options: Any) -> Caller:
    return Caller(kind, server, api_key=TEST_KEY, **options)


def error_from(kind: str, server: FakeServer, max_retries: int = 0, **request: Any) -> Any:
    """The error a call raises. Retries are off unless asked for."""
    request = {"state": TICKET, "questions": {"team": TEAM}, **request}
    with pytest.raises(WityError) as info:
        caller(kind, server, max_retries=max_retries).system_one(**request)
    return info.value


class TestRequest:
    def test_sends_one_post_with_the_key_and_json(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, reasoning="off")

        assert len(server.requests) == 1
        request = server.requests[0]
        assert str(request.url) == f"{DEFAULT_BASE_URL}/v1/systemone"
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        assert request.headers["content-type"] == "application/json"
        assert server.sent() == {"state": TICKET, "questions": {"team": TEAM_JSON}, "reasoning": "off"}

    def test_sends_max_latency_ms_and_image_when_given(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        image = "data:image/png;base64,iVBORw0KGgo="
        caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, max_latency_ms=3000, image=image)
        assert server.sent() == {
            "state": TICKET,
            "questions": {"team": TEAM_JSON},
            "max_latency_ms": 3000,
            "image": image,
        }

    def test_leaves_out_fields_that_are_none(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, reasoning=None)
        assert list(server.sent()) == ["state", "questions"]

    def test_sends_state_dicts_as_json(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        caller(kind, server).system_one(state={"ticket": TICKET, "plan": "pro"}, questions={"team": TEAM})
        assert server.sent()["state"] == {"ticket": TICKET, "plan": "pro"}

    def test_sends_all_three_question_types_as_the_api_expects(self, kind: str) -> None:
        server = FakeServer(reply("multi-auto"))
        caller(kind, server).system_one(state=TICKET, questions={"team": TEAM, "urgent": URGENT, "severity": SEVERITY})
        assert server.sent()["questions"] == {
            "team": TEAM_JSON,
            "urgent": {
                "type": "noul",
                "instructions": "Does the customer ask for something to happen soon?",
                "criteria": {"true": "They ask for fast action", "false": "No time pressure"},
            },
            "severity": {
                "type": "score",
                "instructions": "How much is this hurting the customer?",
                "criteria": ["Not at all", "A little", "A lot"],
            },
        }

    def test_sends_plain_dict_questions_as_they_are(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        question = {**TEAM_JSON, "new_field": True}
        caller(kind, server).system_one(state=TICKET, questions={"team": question})
        assert server.sent()["questions"] == {"team": question}

    def test_sends_extra_body_and_warns_about_unknown_fields_by_name(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        server = FakeServer(reply("choice"))
        extra = {"max_latency": 3000, "model": "ignored-by-wity"}
        caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, extra_body=extra)
        assert server.sent() == {"state": TICKET, "questions": {"team": TEAM_JSON}, **extra}
        warnings = [r for r in caplog.records if r.name == "wity"]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING
        # `model` is accepted by Wity, so only the typo is named.
        assert "fields Wity may not know: max_latency." in warnings[0].getMessage()
        assert "model" not in warnings[0].getMessage()

    def test_a_typo_in_an_argument_fails_before_sending(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        with pytest.raises(TypeError, match="max_latency"):
            caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, max_latency=3000)
        assert server.requests == []


class TestBadInput:
    @pytest.mark.parametrize(
        ("state", "message"),
        [
            ({"ids": {1, 2}}, "turned into JSON"),
            ({"score": math.nan}, "turned into JSON"),
        ],
    )
    def test_state_that_isnt_json_raises_and_sends_nothing(self, kind: str, state: Any, message: str) -> None:
        server = FakeServer(reply("choice"))
        with pytest.raises(WityError, match=message):
            caller(kind, server).system_one(state=state, questions={"team": TEAM})
        with pytest.raises(WityError, match=message):
            caller(kind, server).generate(state=state, instructions="x")
        assert server.requests == []

    def test_state_with_a_loop_raises(self, kind: str) -> None:
        loop: dict[str, Any] = {}
        loop["self"] = loop
        server = FakeServer(reply("choice"))
        with pytest.raises(WityError, match="turned into JSON"):
            caller(kind, server).system_one(state=loop, questions={"team": TEAM})
        assert server.requests == []

    def test_questions_must_be_a_dict(self, kind: str) -> None:
        with pytest.raises(WityError, match="questions must be a dict"):
            caller(kind, FakeServer(reply("choice"))).system_one(state=TICKET, questions=[TEAM])

    def test_each_question_must_be_built_or_a_dict(self, kind: str) -> None:
        with pytest.raises(WityError, match="'team' must be built with choice"):
            caller(kind, FakeServer(reply("choice"))).system_one(state=TICKET, questions={"team": "billing?"})


class TestResponse:
    @pytest.mark.parametrize(
        ("name", "questions"),
        [
            ("choice", {"team": TEAM}),
            ("noul", {"urgent": URGENT}),
            ("noul-always", {"urgent": URGENT}),
            ("score", {"severity": SEVERITY}),
            ("score-always", {"severity": SEVERITY}),
            ("reasoning-always", {"team": TEAM}),
            ("multi-auto", {"team": TEAM, "urgent": URGENT, "severity": SEVERITY}),
        ],
    )
    def test_keeps_everything_the_api_sent(self, kind: str, name: str, questions: dict[str, Any]) -> None:
        res = caller(kind, FakeServer(reply(name))).system_one(state=TICKET, questions=questions)
        assert res.model_dump(mode="json", exclude_unset=True) == fixture(name)["body"]

    def test_gives_typed_access_to_answers(self, kind: str) -> None:
        res = caller(kind, FakeServer(reply("multi-auto"))).system_one(
            state=TICKET, questions={"team": TEAM, "urgent": URGENT, "severity": SEVERITY}
        )
        assert res.choices["team"].choice == "billing"
        assert res.choices["team"].reasoning is not None
        assert res.choices["team"].reasoning.thought is False
        assert res.nouls["urgent"].noul > 0.9
        assert res.scores["severity"].legend["2"] == "A lot"
        assert res.usage.input_tokens == 287
        # Each accessor holds only its own type.
        assert list(res.choices) == ["team"]
        assert list(res.nouls) == ["urgent"]
        assert list(res.scores) == ["severity"]
        assert isinstance(res.answers["urgent"], wity.NoulAnswer)

    def test_thinking_answers_carry_the_direct_answer(self, kind: str) -> None:
        res = caller(kind, FakeServer(reply("noul-always"))).system_one(state=TICKET, questions={"urgent": URGENT})
        answer = res.nouls["urgent"]
        assert answer.direct_noul is not None
        assert answer.reasoning is not None
        assert answer.reasoning.reason == "requested"

    def test_keeps_answer_types_and_fields_this_sdk_doesnt_know(self, kind: str) -> None:
        body = fixture("choice")["body"]
        body["answers"]["when"] = {"type": "date", "date": "2026-10-01"}
        body["answers"]["team"]["calibrated"] = False
        body["cost"] = 0.00001
        res = caller(kind, FakeServer(Reply(body=body))).system_one(state=TICKET, questions={"team": TEAM})
        when = res.answers["when"]
        assert isinstance(when, wity.OtherAnswer)
        assert when.type == "date"
        assert when.model_extra == {"date": "2026-10-01"}
        assert res.choices["team"].model_extra == {"calibrated": False}
        assert res.model_extra == {"cost": 0.00001}

    def test_raises_if_a_success_isnt_a_json_object(self, kind: str) -> None:
        with pytest.raises(WityError, match="isn't a JSON object"):
            caller(kind, FakeServer(Reply(body="not json"))).system_one(state=TICKET, questions={"team": TEAM})

    def test_raises_if_a_success_has_the_wrong_shape(self, kind: str) -> None:
        with pytest.raises(WityError, match="doesn't have the shape"):
            caller(kind, FakeServer(Reply(body={"answers": 1}))).system_one(state=TICKET, questions={"team": TEAM})


class TestErrors:
    def test_401_becomes_authentication_error_with_a_hint(self, kind: str) -> None:
        err = error_from(kind, FakeServer(reply("error-bad-key")))
        assert isinstance(err, AuthenticationError)
        assert err.status == 401
        assert str(err) == "401 invalid API key. Check WITY_API_KEY."

    @pytest.mark.parametrize("name", ["error-unknown-field", "error-object-instructions", "error-one-score-level"])
    def test_400_becomes_bad_request_error_with_the_servers_message(self, kind: str, name: str) -> None:
        saved = fixture(name)
        err = error_from(kind, FakeServer(reply(name)))
        assert isinstance(err, BadRequestError)
        assert str(err) == f"400 {saved['body']['error']}"
        assert err.body == saved["body"]

    def test_402_becomes_insufficient_balance_error(self, kind: str) -> None:
        err = error_from(kind, FakeServer(Reply(402, {"detail": "insufficient balance"})))
        assert isinstance(err, InsufficientBalanceError)
        assert "no balance" in str(err)

    def test_reads_nested_error_message_bodies(self, kind: str) -> None:
        err = error_from(kind, FakeServer(Reply(418, {"error": {"message": "nested"}})))
        assert type(err) is APIError
        assert str(err) == "418 nested"

    def test_404_says_the_address_may_have_changed(self, kind: str) -> None:
        err = error_from(kind, FakeServer(Reply(404, "Not Found")))
        assert "WITY_BASE_URL" in str(err)

    def test_a_400_names_the_unknown_fields_that_were_sent(self, kind: str) -> None:
        err = error_from(kind, FakeServer(reply("error-unknown-field")), extra_body={"user": "someone"})
        assert isinstance(err, BadRequestError)
        assert str(err) == (
            "400 A System One request requires state and questions. "
            "The request included fields Wity may not know: user."
        )

    def test_an_html_error_page_becomes_a_readable_message(self, kind: str) -> None:
        html = "<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1></body></html>"
        err = error_from(kind, FakeServer(Reply(502, html)))
        assert isinstance(err, InternalServerError)
        assert str(err) == "502 the server sent an HTML error page"
        assert err.body == html

    def test_a_list_of_validation_errors_becomes_field_problem_pairs(self, kind: str) -> None:
        body = {
            "detail": [
                {"loc": ["body", "questions", "team"], "msg": "field required"},
                {"loc": ["body", "state"], "msg": "too long"},
            ]
        }
        err = error_from(kind, FakeServer(Reply(422, body)))
        assert str(err) == "422 questions.team: field required; state: too long"

    def test_long_text_bodies_are_cut_to_200_characters(self, kind: str) -> None:
        err = error_from(kind, FakeServer(Reply(500, "x" * 500)))
        assert str(err) == f"500 {'x' * 200}…"

    def test_errors_carry_the_request_id_when_the_server_sends_one(self, kind: str) -> None:
        err = error_from(kind, FakeServer(reply("error-bad-key", headers={"x-railway-request-id": "req_abc123"})))
        assert err.request_id == "req_abc123"
        assert error_from(kind, FakeServer(reply("error-bad-key"))).request_id is None

    def test_never_follows_a_redirect(self, kind: str) -> None:
        server = FakeServer(Reply(301, headers={"Location": "https://somewhere-else.example/v1/systemone"}))
        err = error_from(kind, server)
        assert type(err) is APIError
        assert err.status == 301
        assert "never follows" in str(err)
        assert "WITY_BASE_URL" in str(err)
        assert len(server.requests) == 1

    def test_never_follows_a_redirect_even_with_a_client_that_would(self, kind: str) -> None:
        server = FakeServer(
            Reply(302, headers={"Location": "https://somewhere-else.example/v1/systemone"}), reply("choice")
        )
        transport = httpx.MockTransport(server.handle)
        client: Any = (
            wity.WityClient(api_key=TEST_KEY, http_client=httpx.Client(transport=transport, follow_redirects=True))
            if kind == "sync"
            else wity.AsyncWityClient(
                api_key=TEST_KEY, http_client=httpx.AsyncClient(transport=transport, follow_redirects=True)
            )
        )
        with pytest.raises(APIError, match="never follows"):
            if kind == "sync":
                client.system_one(state=TICKET, questions={"team": TEAM})
            else:
                asyncio.run(client.system_one(state=TICKET, questions={"team": TEAM}))
        assert len(server.requests) == 1


class TestRetries:
    def test_retries_a_429_waiting_as_long_as_retry_after_says(self, kind: str) -> None:
        server = FakeServer(Reply(429, {"detail": "slow down"}, {"Retry-After": "0"}), reply("choice"))
        res = caller(kind, server).system_one(state=TICKET, questions={"team": TEAM})
        assert res.choices["team"].choice == "billing"
        assert len(server.requests) == 2

    def test_gives_up_after_max_retries_and_raises_the_last_error(self, kind: str) -> None:
        server = FakeServer(Reply(429, {"detail": "slow down"}, {"Retry-After": "0"}))
        err = error_from(kind, server, max_retries=2)
        assert isinstance(err, RateLimitError)
        assert len(server.requests) == 3

    def test_retries_a_5xx_with_backoff_when_theres_no_retry_after(self, kind: str) -> None:
        server = FakeServer(Reply(502, {"error": "model error"}), reply("choice"))
        res = caller(kind, server, max_retries=1).system_one(state=TICKET, questions={"team": TEAM})
        assert res.model == "wity-1"
        assert len(server.requests) == 2

    def test_doesnt_retry_when_retry_after_is_longer_than_60_s(self, kind: str) -> None:
        server = FakeServer(Reply(503, {"error": "busy"}, {"Retry-After": "90"}))
        err = error_from(kind, server, max_retries=2)
        assert isinstance(err, InternalServerError)
        assert err.headers["retry-after"] == "90"
        assert len(server.requests) == 1

    def test_doesnt_retry_a_400(self, kind: str) -> None:
        server = FakeServer(reply("error-one-score-level"))
        error_from(kind, server, max_retries=2)
        assert len(server.requests) == 1

    def test_retries_network_failures_then_raises_api_connection_error(self, kind: str) -> None:
        server = FakeServer(httpx.ConnectError("connection refused"))
        err = error_from(kind, server, max_retries=1)
        assert isinstance(err, APIConnectionError)
        assert httpx.URL(DEFAULT_BASE_URL).host in str(err)
        assert len(server.requests) == 2

    def test_raises_api_timeout_error_without_retrying(self, kind: str) -> None:
        server = FakeServer(httpx.ReadTimeout("timed out"))
        with pytest.raises(APITimeoutError, match="didn't answer within 60 s"):
            caller(kind, server).system_one(state=TICKET, questions={"team": TEAM})
        assert len(server.requests) == 1


class TestReadingTheAnswer:
    def test_a_connection_that_breaks_mid_answer_is_retried(self, kind: str) -> None:
        server = FakeServer(Reply(body=fixture("choice")["body"], break_body=True), reply("choice"))
        res = caller(kind, server, max_retries=1).system_one(state=TICKET, questions={"team": TEAM})
        assert res.choices["team"].choice == "billing"
        assert len(server.requests) == 2

        err = error_from(kind, FakeServer(Reply(body={"model": "wity-1"}, break_body=True)))
        assert isinstance(err, APIConnectionError)
        assert "connection broke while reading" in str(err)

    def test_an_answer_that_stalls_halfway_becomes_api_timeout_error(self, kind: str) -> None:
        server = FakeServer(reply("choice", stall=0.2))
        with pytest.raises(APITimeoutError):
            caller(kind, server, timeout=0.05).system_one(state=TICKET, questions={"team": TEAM})
        assert len(server.requests) == 1


class TestMaxLatencyAndTheTimeout:
    def test_waits_for_max_latency_ms_plus_10_s_even_when_timeout_is_shorter(self, kind: str) -> None:
        # The answer takes 0.2 s. The timeout alone (0.05 s) would give up first.
        server = FakeServer(reply("choice", stall=0.2))
        res = caller(kind, server, timeout=0.05).system_one(state=TICKET, questions={"team": TEAM}, max_latency_ms=300)
        assert res.choices["team"].choice == "billing"
        assert server.requests[0].extensions["timeout"]["read"] == pytest.approx(10.3)

    def test_keeps_the_timeout_when_its_already_longer(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        caller(kind, server, timeout=30).system_one(state=TICKET, questions={"team": TEAM}, max_latency_ms=300)
        assert server.requests[0].extensions["timeout"]["read"] == 30

    def test_a_huge_max_latency_ms_doesnt_break_the_call(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        res = caller(kind, server).system_one(state=TICKET, questions={"team": TEAM}, max_latency_ms=3e9)
        assert res.choices["team"].choice == "billing"


class TestAsyncCancelling:
    def test_asyncio_timeout_stops_a_request_in_flight(self) -> None:
        requests: list[httpx.Request] = []

        async def hang(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            await asyncio.sleep(10)
            raise AssertionError("never reached")

        client = wity.AsyncWityClient(
            api_key=TEST_KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(hang))
        )
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(asyncio.wait_for(client.system_one(state=TICKET, questions={"team": TEAM}), 0.05))
        assert len(requests) == 1

    def test_asyncio_timeout_stops_the_wait_between_retries(self) -> None:
        server = FakeServer(Reply(429, {"detail": "slow down"}, {"Retry-After": "30"}))
        client = Caller("async", server, api_key=TEST_KEY).client
        assert isinstance(client, wity.AsyncWityClient)
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(asyncio.wait_for(client.system_one(state=TICKET, questions={"team": TEAM}), 0.1))
        assert len(server.requests) == 1


class TestClosing:
    def test_with_closes_the_clients_own_connection_pool(self) -> None:
        with wity.WityClient(api_key=TEST_KEY) as client:
            pass
        assert client._http.is_closed

    def test_async_with_closes_the_clients_own_connection_pool(self) -> None:
        async def run() -> wity.AsyncWityClient:
            async with wity.AsyncWityClient(api_key=TEST_KEY) as client:
                pass
            return client

        assert asyncio.run(run())._http.is_closed

    def test_doesnt_close_an_http_client_you_passed_in(self) -> None:
        http = httpx.Client()
        with wity.WityClient(api_key=TEST_KEY, http_client=http):
            pass
        assert not http.is_closed
        http.close()
