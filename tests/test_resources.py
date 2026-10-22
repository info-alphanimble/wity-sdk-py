"""Split, extract, skill questions, skills and sessions, against both clients.

No fixtures were captured for these routes yet. The reply bodies below follow the server code
(wity-server engine/src/open_decision/skills_api.py and api_server.py, 20 Oct 2026).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

import httpx
import pytest
from conftest import TEST_KEY, Caller, FakeServer, Reply

import wity
from wity import (
    DEFAULT_BASE_URL,
    APIConnectionError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    WityError,
)

pytestmark = pytest.mark.usefixtures("fast_retries")

EMAIL = "From: dana@example.com\nSeal test failed.\n\nFrom: marcus@example.com\nRerun tomorrow."
SPLIT_REPLY = {
    "items": [
        {"start": 0, "end": 40, "text": EMAIL[0:40]},
        {"start": 42, "end": len(EMAIL), "text": EMAIL[42:]},
    ],
    "how": "model",
    "timing_ms": {"total": 84},
    "usage": {"input_tokens": 412, "output_tokens": 0},
}
EXTRACT_REPLY = {
    "fields": {"party": ["Kestrel Foods Ltd."], "effective date": ["2025-03-14"]},
    "entities": [
        {"field": "party", "text": "Kestrel Foods Ltd.", "start": 10, "end": 28, "confidence": 0.98},
        {"field": "effective date", "text": "2025-03-14", "start": 60, "end": 74, "confidence": 0.91},
    ],
    "timing_ms": 503,
    "usage": {"input_tokens": 301, "output_tokens": 0},
}
SKILL = {
    "id": "sk_4f1c9e",
    "object": "skill",
    "kind": "decision",
    "name": "urgent-ticket",
    "description": "",
    "question": {"type": "noul", "instructions": "Does this ticket need a reply within the hour?"},
    "parts": None,
    "combine": None,
    "metadata": {},
    "examples": 0,
    "status": "needs_examples",
    "answered_by": "question",
    "active": False,
    "version": 1,
    "quality": None,
    "created_at": 1792400000,
    "updated_at": 1792400000,
}
SPLIT_SKILL = {
    **{key: value for key, value in SKILL.items() if key not in ("question", "parts", "combine")},
    "kind": "split",
    "name": "tickets",
    "split": "one item per ticket",
}
SESSION = {
    "id": "ses_9a41c0",
    "object": "session",
    "state": "Ticket #5120 from Northwind Foods.",
    "skills": ["sk_4f1c9e"],
    "metadata": {},
    "events": 0,
    "created_at": 1792400000,
    "updated_at": 1792400000,
    "expires_at": 1793004800,
}
LIST_OF_NOTHING = {"object": "list", "data": [], "has_more": False}


def ok(body: Any) -> Reply:
    return Reply(status=200, body=body)


def client(kind: str, server: FakeServer, **options: Any) -> wity.WityClient | wity.AsyncWityClient:
    return Caller(kind, server, api_key=TEST_KEY, **options).client


def run(result: Any) -> Any:
    """The value of a call, from either client."""
    return asyncio.run(result) if inspect.iscoroutine(result) else result


def sent(server: FakeServer, index: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(server.requests[index].content)
    return body


def where(server: FakeServer, index: int) -> str:
    request = server.requests[index]
    return f"{request.method} {str(request.url).removeprefix(DEFAULT_BASE_URL)}"


class TestSplit:
    def test_sends_text_and_split(self, kind: str) -> None:
        server = FakeServer(ok(SPLIT_REPLY))
        res = run(client(kind, server).split(text=EMAIL, split="one item per email"))

        assert where(server, 0) == "POST /v1/split"
        assert sent(server) == {"text": EMAIL, "split": "one item per email"}
        for item in res.items:
            assert EMAIL[item.start : item.end] == item.text

    def test_sends_a_spec(self, kind: str) -> None:
        server = FakeServer(ok(SPLIT_REPLY))
        run(client(kind, server).split(text=EMAIL, split=wity.SplitSpec(unit="email", starts_at=["a From: line"])))
        assert sent(server) == {"text": EMAIL, "split": {"unit": "email", "starts_at": ["a From: line"]}}

    def test_splits_with_a_skill(self, kind: str) -> None:
        info = {"id": "sk_1", "name": "tickets", "version": 2, "answered_by": "examples", "active": True}
        server = FakeServer(ok({**SPLIT_REPLY, "skill": info}))
        res = run(client(kind, server).split(text=EMAIL, skill="tickets"))
        assert sent(server) == {"text": EMAIL, "skill": "tickets"}
        assert res.skill is not None and res.skill.answered_by == "examples"

    def test_refuses_split_and_skill_together(self, kind: str) -> None:
        server = FakeServer(ok(SPLIT_REPLY))
        with pytest.raises(WityError, match="Give split or skill, not both"):
            run(client(kind, server).split(text=EMAIL, split="one item per email", skill="tickets"))
        assert server.requests == []

    def test_waits_at_least_two_minutes(self, kind: str) -> None:
        server = FakeServer(ok(SPLIT_REPLY))
        run(client(kind, server, timeout=5).split(text=EMAIL))
        assert server.requests[0].extensions["timeout"]["read"] == 120.0


class TestExtract:
    def test_sends_text_fields_and_types(self, kind: str) -> None:
        server = FakeServer(ok(EXTRACT_REPLY))
        fields = {"party": "a company that signs the agreement", "effective date": "when it takes effect"}
        res = run(client(kind, server).extract(text="…", fields=fields, types={"effective date": "date"}))

        assert where(server, 0) == "POST /v1/extract"
        assert sent(server) == {"text": "…", "fields": fields, "types": {"effective date": "date"}}
        assert res.fields["effective date"] == ["2025-03-14"]
        assert res.entities[0].confidence == 0.98


class TestSkillQuestions:
    def test_sends_skill_as_a_question(self, kind: str) -> None:
        answer = {
            "type": "noul",
            "noul": 0.97,
            "skill": {
                "id": "sk_4f1c9e",
                "name": "urgent-ticket",
                "version": 3,
                "answered_by": "examples",
                "active": True,
            },
        }
        body = {"model": "wity-1", "answers": {"urgent": answer}, "metadata": {"reasoning": "auto", "elapsed_ms": 40.0}}
        server = FakeServer(ok({**body, "usage": {"input_tokens": 9, "output_tokens": 0}}))
        res = run(client(kind, server).system_one(state="…", questions={"urgent": wity.skill("urgent-ticket")}))

        assert sent(server) == {"state": "…", "questions": {"urgent": {"skill": "urgent-ticket"}}}
        urgent = res.answers["urgent"]
        assert isinstance(urgent, wity.NoulAnswer)
        assert urgent.skill is not None and urgent.skill.version == 3


class TestSkills:
    def test_create(self, kind: str) -> None:
        server = FakeServer(ok({**SKILL, "usage": {"input_tokens": 0, "output_tokens": 0}}))
        question = wity.noul("Does this ticket need a reply within the hour?")
        created = run(client(kind, server).skills.create(name="urgent-ticket", question=question))

        assert where(server, 0) == "POST /v1/skills"
        assert sent(server) == {
            "name": "urgent-ticket",
            "question": {"type": "noul", "instructions": "Does this ticket need a reply within the hour?"},
        }
        assert isinstance(created, wity.DecisionSkill)
        assert created.usage is not None

    def test_create_a_split_skill(self, kind: str) -> None:
        server = FakeServer(ok(SPLIT_SKILL))
        examples = [{"text": "A\n\nB", "items": ["A", "B"]}]
        created = run(
            client(kind, server).skills.create(name="tickets", split="one item per ticket", examples=examples)
        )
        assert sent(server) == {"name": "tickets", "split": "one item per ticket", "examples": examples}
        assert isinstance(created, wity.SplitSkill)

    def test_list_sends_get_with_query_and_no_body(self, kind: str) -> None:
        server = FakeServer(ok({"object": "list", "data": [SKILL, SPLIT_SKILL], "has_more": False}))
        page = run(client(kind, server).skills.list(limit=5, after="sk_1"))

        assert where(server, 0) == "GET /v1/skills?limit=5&after=sk_1"
        assert server.requests[0].content == b""
        assert "content-type" not in server.requests[0].headers
        assert [type(skill) for skill in page.data] == [wity.DecisionSkill, wity.SplitSkill]

    def test_list_leaves_out_an_empty_query(self, kind: str) -> None:
        server = FakeServer(ok(LIST_OF_NOTHING))
        run(client(kind, server).skills.list())
        assert where(server, 0) == "GET /v1/skills"

    def test_encodes_names_in_the_path(self, kind: str) -> None:
        server = FakeServer(ok(SKILL))
        run(client(kind, server).skills.get("a b/c?d"))
        assert server.requests[0].url.raw_path == b"/v1/skills/a%20b%2Fc%3Fd"

    @pytest.mark.parametrize("name", [".", "..", ""])
    def test_refuses_names_that_break_the_path(self, kind: str, name: str) -> None:
        server = FakeServer(ok(SKILL))
        with pytest.raises(WityError):
            run(client(kind, server).skills.get(name))
        assert server.requests == []

    def test_update_sends_only_what_changed(self, kind: str) -> None:
        server = FakeServer(ok(SKILL))
        run(client(kind, server).skills.update("urgent-ticket", description="Within the hour"))
        assert where(server, 0) == "PATCH /v1/skills/urgent-ticket"
        assert sent(server) == {"description": "Within the hour"}

    def test_update_can_remove_parts(self, kind: str) -> None:
        server = FakeServer(ok(SKILL))
        run(client(kind, server).skills.update("urgent-ticket", parts=None))
        assert sent(server) == {"parts": None}

    def test_delete(self, kind: str) -> None:
        server = FakeServer(ok({"id": "sk_4f1c9e", "object": "skill", "deleted": True}))
        deleted = run(client(kind, server).skills.delete("urgent-ticket"))
        assert where(server, 0) == "DELETE /v1/skills/urgent-ticket"
        assert deleted.deleted is True

    def test_examples(self, kind: str) -> None:
        added = {"object": "list", "data": [], "skill": SKILL, "usage": {"input_tokens": 9, "output_tokens": 0}}
        server = FakeServer(
            ok(added), ok(LIST_OF_NOTHING), ok({"id": "ex_1", "object": "skill.example", "deleted": True})
        )
        c = client(kind, server)
        run(c.skills.examples.add("urgent-ticket", [{"state": "Site down", "answer": "yes"}]))
        run(c.skills.examples.list("urgent-ticket", limit=10))
        run(c.skills.examples.delete("urgent-ticket", "ex_1"))

        assert where(server, 0) == "POST /v1/skills/urgent-ticket/examples"
        assert sent(server) == {"examples": [{"state": "Site down", "answer": "yes"}]}
        assert where(server, 1) == "GET /v1/skills/urgent-ticket/examples?limit=10"
        assert where(server, 2) == "DELETE /v1/skills/urgent-ticket/examples/ex_1"

    def test_example_kinds_are_told_apart(self, kind: str) -> None:
        decision = {
            "id": "ex_1",
            "object": "skill.example",
            "state": "x",
            "answer": "yes",
            "source": "api",
            "created_at": 1,
        }
        split = {
            "id": "ex_2",
            "object": "skill.example",
            "text": "A\n\nB",
            "starts": [0, 3],
            "items": ["A", "B"],
            "source": "api",
            "created_at": 1,
        }
        server = FakeServer(ok({"object": "list", "data": [decision, split], "has_more": False}))
        page = run(client(kind, server).skills.examples.list("x"))
        assert [type(example) for example in page.data] == [wity.DecisionExample, wity.SplitExample]


class TestSessions:
    def test_create_get_update_delete(self, kind: str) -> None:
        server = FakeServer(
            ok(SESSION), ok(SESSION), ok(SESSION), ok({"id": SESSION["id"], "object": "session", "deleted": True})
        )
        c = client(kind, server)
        run(c.sessions.create(state="Ticket #5120 from Northwind Foods.", skills=["urgent-ticket"]))
        run(c.sessions.get("ses_9a41c0"))
        run(c.sessions.update("ses_9a41c0", expires_in=3600))
        run(c.sessions.delete("ses_9a41c0"))

        assert [where(server, i) for i in range(4)] == [
            "POST /v1/sessions",
            "GET /v1/sessions/ses_9a41c0",
            "PATCH /v1/sessions/ses_9a41c0",
            "DELETE /v1/sessions/ses_9a41c0",
        ]
        assert sent(server, 0) == {"state": "Ticket #5120 from Northwind Foods.", "skills": ["urgent-ticket"]}
        assert sent(server, 2) == {"expires_in": 3600}

    def test_events(self, kind: str) -> None:
        server = FakeServer(ok({"object": "list", "data": [], "session": SESSION}), ok(LIST_OF_NOTHING))
        c = client(kind, server)
        events = [
            {"type": "update", "content": "Customer: still broken"},
            {"type": "feedback", "decision_id": "ev_2", "answer": "no"},
        ]
        run(c.sessions.add_events("ses_9a41c0", events))
        run(c.sessions.events("ses_9a41c0", after="ev_1"))

        assert sent(server) == {"events": events}
        assert where(server, 1) == "GET /v1/sessions/ses_9a41c0/events?after=ev_1"

    def test_decide_with_an_empty_body_runs_the_session_skills(self, kind: str) -> None:
        decision = {
            "model": "wity-1",
            "answers": {"urgent-ticket": {"type": "noul", "noul": 0.999}},
            "metadata": {"reasoning": "off", "elapsed_ms": 39.5},
            "decision_id": "ev_2",
            "session_id": "ses_9a41c0",
            "context": {"updates_read": 2, "updates_left_out": 0},
            "cached": False,
            "usage": {"input_tokens": 202, "output_tokens": 0},
        }
        server = FakeServer(ok(decision))
        res = run(client(kind, server).sessions.decide("ses_9a41c0"))

        assert where(server, 0) == "POST /v1/sessions/ses_9a41c0/decide"
        assert sent(server) == {}
        assert res.decision_id == "ev_2"
        assert res.context.updates_read == 2


class TestRetries:
    def test_retries_a_read_after_a_500(self, kind: str) -> None:
        server = FakeServer(Reply(500, {"error": "oops"}), ok(SKILL))
        run(client(kind, server).skills.get("urgent-ticket"))
        assert len(server.requests) == 2

    def test_doesnt_retry_an_add_after_a_500(self, kind: str) -> None:
        server = FakeServer(Reply(500, {"error": "oops"}), ok(SESSION))
        with pytest.raises(InternalServerError):
            run(client(kind, server).sessions.create(state="x"))
        assert len(server.requests) == 1

    def test_doesnt_retry_an_add_after_a_broken_connection(self, kind: str) -> None:
        server = FakeServer(httpx.ConnectError("refused"), ok(SESSION))
        with pytest.raises(APIConnectionError):
            run(client(kind, server).skills.examples.add("urgent-ticket", [{"state": "x", "answer": "yes"}]))
        assert len(server.requests) == 1

    @pytest.mark.parametrize("status", [429, 503])
    def test_retries_an_add_when_wity_did_no_work(self, kind: str, status: int) -> None:
        server = FakeServer(Reply(status, headers={"retry-after": "0"}), ok(SESSION))
        run(client(kind, server).sessions.create(state="x"))
        assert len(server.requests) == 2

    def test_logs_why_it_didnt_retry(self, kind: str, caplog: pytest.LogCaptureFixture) -> None:
        server = FakeServer(Reply(502, {"error": "oops"}))
        with caplog.at_level(logging.INFO, logger="wity"), pytest.raises(WityError):
            run(client(kind, server).sessions.add_events("ses_1", [{"type": "note", "content": "x"}]))
        assert "/v1/sessions/{id}/events: got 502. Not retrying: this call adds something" in caplog.text


class TestErrors:
    def test_not_found_without_the_moved_address_hint(self, kind: str) -> None:
        server = FakeServer(Reply(404, {"error": "no skill 'nope'", "code": "not_found"}))
        with pytest.raises(NotFoundError) as info:
            run(client(kind, server).skills.get("nope"))
        assert str(info.value) == "404 no skill 'nope'"

    def test_keeps_the_hint_for_a_404_that_isnt_wity_s(self, kind: str) -> None:
        server = FakeServer(Reply(404, {"detail": "Not Found"}))
        with pytest.raises(NotFoundError, match="The API address may have changed"):
            run(client(kind, server).skills.list())

    def test_conflict_when_the_name_is_taken(self, kind: str) -> None:
        server = FakeServer(Reply(409, {"error": "a skill named 'x' already exists", "code": "conflict"}))
        with pytest.raises(ConflictError):
            run(client(kind, server).skills.create(name="x", question=wity.noul("Is it urgent?")))
