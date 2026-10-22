"""``client.sessions``: keep a state on Wity's side, add events, and decide on it (/v1/sessions).

Session decisions are billed like ``system_one``. Repeats, events and reads are free.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ._request import latency_timeout, parse, path_part, question_json
from .errors import WityError
from .types import (
    AddedEvents,
    Deleted,
    EventList,
    Question,
    Reasoning,
    Session,
    SessionDecision,
    SessionList,
    SkillQuestion,
)

if TYPE_CHECKING:
    from .client import AsyncWityClient, WityClient, _BaseClient, _Prepared


def _session_path(session_id: str) -> str:
    return f"/v1/sessions/{path_part(session_id, 'session_id')}"


class _SessionsBase:
    """Builds each request. ``Sessions`` and ``AsyncSessions`` only send it."""

    def __init__(self, client: _BaseClient) -> None:
        self._client = client

    def _create(
        self,
        state: str | Mapping[str, Any] | None,
        skills: Sequence[str] | None,
        metadata: Mapping[str, Any] | None,
        expires_in: int | None,
    ) -> _Prepared:
        body = {
            "state": state,
            "skills": list(skills) if skills is not None else None,
            "metadata": metadata,
            "expires_in": expires_in,
        }
        n = len(skills) if skills is not None else 0
        return self._client._prepare("/v1/sessions", body, None, f"skills={n}", 0.0, retry="busy")

    def _list(self, limit: int | None, after: str | None) -> _Prepared:
        query = {"limit": limit, "after": after}
        return self._client._prepare("/v1/sessions", None, None, "", 0.0, method="GET", query=query)

    def _get(self, session_id: str) -> _Prepared:
        return self._client._prepare(
            "/v1/sessions/{id}", None, None, "", 0.0, method="GET", path=_session_path(session_id)
        )

    def _update(
        self,
        session_id: str,
        skills: Sequence[str] | None,
        metadata: Mapping[str, Any] | None,
        expires_in: int | None,
    ) -> _Prepared:
        body = {"skills": list(skills) if skills is not None else None, "metadata": metadata, "expires_in": expires_in}
        changed = [key for key, value in body.items() if value is not None]
        return self._client._prepare(
            "/v1/sessions/{id}",
            body,
            None,
            f"fields={changed!r}",
            0.0,
            method="PATCH",
            path=_session_path(session_id),
        )

    def _delete(self, session_id: str) -> _Prepared:
        return self._client._prepare(
            "/v1/sessions/{id}", None, None, "", 0.0, method="DELETE", path=_session_path(session_id)
        )

    def _add_events(self, session_id: str, events: Sequence[Mapping[str, Any]]) -> _Prepared:
        types = [event.get("type") if isinstance(event, Mapping) else None for event in events]
        return self._client._prepare(
            "/v1/sessions/{id}/events",
            {"events": [dict(event) if isinstance(event, Mapping) else event for event in events]},
            None,
            f"events={types!r}",
            0.0,
            path=f"{_session_path(session_id)}/events",
            retry="busy",
        )

    def _events(self, session_id: str, limit: int | None, after: str | None) -> _Prepared:
        return self._client._prepare(
            "/v1/sessions/{id}/events",
            None,
            None,
            "",
            0.0,
            method="GET",
            path=f"{_session_path(session_id)}/events",
            query={"limit": limit, "after": after},
        )

    def _decide(
        self,
        session_id: str,
        questions: Mapping[str, Question | SkillQuestion | Mapping[str, Any]] | None,
        reasoning: Reasoning | None,
        max_latency_ms: float | None,
        remember: bool | None,
        extra_body: Mapping[str, Any] | None,
    ) -> _Prepared:
        if questions is not None and not isinstance(questions, Mapping):
            raise WityError(
                "questions must be a dict of question name to question, for example {'urgent': skill(...)}."
            )
        body = {
            "questions": {name: question_json(name, q) for name, q in questions.items()} if questions else None,
            "reasoning": reasoning,
            "max_latency_ms": max_latency_ms,
            "remember": remember,
        }
        asked = list(questions) if questions else "session skills"
        return self._client._prepare(
            "/v1/sessions/{id}/decide",
            body,
            extra_body,
            f"questions={asked!r} reasoning={reasoning or 'default'!r}",
            latency_timeout(max_latency_ms),
            path=f"{_session_path(session_id)}/decide",
            retry="busy",
        )


class Sessions(_SessionsBase):
    """Sessions belong to the key's account. They expire after 7 days unless ``expires_in`` says otherwise."""

    _client: WityClient

    def create(
        self,
        *,
        state: str | Mapping[str, Any] | None = None,
        skills: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        expires_in: int | None = None,
    ) -> Session:
        """Start a session. Not retried after an unclear failure, so no second session is made.

        Args:
            state: What Wity reads first. May be empty.
            skills: Skill ids or names, up to 64. ``decide`` runs them when it names no questions.
            metadata: Yours to keep, such as your own ticket id. Up to 8,000 characters as JSON.
            expires_in: Seconds until the session and its log are deleted: 60 to 7,776,000 (90 days).
                The API default is 7 days.
        """
        return parse(Session, self._client._request(self._create(state, skills, metadata, expires_in)))

    def list(self, *, limit: int | None = None, after: str | None = None) -> SessionList:
        """Newest first. ``limit`` is 1 to 100. ``after`` is the last id of the page before."""
        return parse(SessionList, self._client._request(self._list(limit, after)))

    def get(self, session_id: str) -> Session:
        return parse(Session, self._client._request(self._get(session_id)))

    def update(
        self,
        session_id: str,
        *,
        skills: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        expires_in: int | None = None,
    ) -> Session:
        """Change the skills, metadata or ``expires_in`` (counted from now). The state changes only through events."""
        return parse(Session, self._client._request(self._update(session_id, skills, metadata, expires_in)))

    def delete(self, session_id: str) -> Deleted:
        """Delete the session and its log. Examples made from its feedback stay with their skills."""
        return parse(Deleted, self._client._request(self._delete(session_id)))

    def add_events(self, session_id: str, events: Sequence[Mapping[str, Any]]) -> AddedEvents:
        """Add 1 to 100 events, kept in order. Not retried after an unclear failure, so nothing is added twice.

        - ``{"type": "update", "content": ...}`` adds to what Wity reads.
        - ``{"type": "state", "content": ...}`` replaces what Wity reads from here on.
        - ``{"type": "note", "content": ...}`` is kept in the log, never read.
        - ``{"type": "feedback", "decision_id": ..., "answer": ...}`` is the right answer to an earlier decision.
          It becomes an example of that skill. Add ``"question"`` when the decision had more than one skill question.
        """
        return parse(AddedEvents, self._client._request(self._add_events(session_id, events)))

    def events(self, session_id: str, *, limit: int | None = None, after: str | None = None) -> EventList:
        """The log, oldest first. ``limit`` is 1 to 100 (default 50). ``after`` is an event id."""
        return parse(EventList, self._client._request(self._events(session_id, limit, after)))

    def decide(
        self,
        session_id: str,
        *,
        questions: Mapping[str, Question | SkillQuestion | Mapping[str, Any]] | None = None,
        reasoning: Reasoning | None = None,
        max_latency_ms: float | None = None,
        remember: bool | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> SessionDecision:
        """Decide on what the session holds now.

        The decision is added to the log. Not retried after an unclear failure, so it isn't logged twice.

        Args:
            questions: Leave out to run the session's skills, each keyed by its name.
            reasoning: The default here is ``"off"``. Skill questions are always answered in fast mode.
            max_latency_ms: Time limit per request, 200 to 120000 ms.
            remember: Add this decision's answers to what later decisions read. Default ``False``.
            extra_body: Extra top-level fields to send, for API fields newer than this SDK.
        """
        p = self._decide(session_id, questions, reasoning, max_latency_ms, remember, extra_body)
        return parse(SessionDecision, self._client._request(p))


class AsyncSessions(_SessionsBase):
    """Async version of ``Sessions``. Same methods and arguments."""

    _client: AsyncWityClient

    async def create(
        self,
        *,
        state: str | Mapping[str, Any] | None = None,
        skills: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        expires_in: int | None = None,
    ) -> Session:
        return parse(Session, await self._client._request(self._create(state, skills, metadata, expires_in)))

    async def list(self, *, limit: int | None = None, after: str | None = None) -> SessionList:
        return parse(SessionList, await self._client._request(self._list(limit, after)))

    async def get(self, session_id: str) -> Session:
        return parse(Session, await self._client._request(self._get(session_id)))

    async def update(
        self,
        session_id: str,
        *,
        skills: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        expires_in: int | None = None,
    ) -> Session:
        return parse(Session, await self._client._request(self._update(session_id, skills, metadata, expires_in)))

    async def delete(self, session_id: str) -> Deleted:
        return parse(Deleted, await self._client._request(self._delete(session_id)))

    async def add_events(self, session_id: str, events: Sequence[Mapping[str, Any]]) -> AddedEvents:
        return parse(AddedEvents, await self._client._request(self._add_events(session_id, events)))

    async def events(self, session_id: str, *, limit: int | None = None, after: str | None = None) -> EventList:
        return parse(EventList, await self._client._request(self._events(session_id, limit, after)))

    async def decide(
        self,
        session_id: str,
        *,
        questions: Mapping[str, Question | SkillQuestion | Mapping[str, Any]] | None = None,
        reasoning: Reasoning | None = None,
        max_latency_ms: float | None = None,
        remember: bool | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> SessionDecision:
        p = self._decide(session_id, questions, reasoning, max_latency_ms, remember, extra_body)
        return parse(SessionDecision, await self._client._request(p))
