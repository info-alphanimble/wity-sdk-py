"""``client.skills``: create, teach and manage skills (/v1/skills).

Reading and managing skills is free. Adding examples is billed once, on their tokens.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

import pydantic

from ._request import parse, path_part
from .types import AddedExamples, Choice, Deleted, ExampleList, Noul, Skill, SkillList, SplitSpec

if TYPE_CHECKING:
    from .client import AsyncWityClient, WityClient, _BaseClient, _Prepared

_SKILL: pydantic.TypeAdapter[Skill] = pydantic.TypeAdapter(Skill)

QuestionInput = Noul | Choice | Mapping[str, Any]
"""A noul or choice question: built with ``noul()`` or ``choice()``, or a dict."""

PartInput = str | Noul | Choice | Mapping[str, Any]
"""A part: a yes/no question as plain words, or a full question."""


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()
"""Leave a field as it is. Used where ``None`` means "remove"."""


def _json(value: Any) -> Any:
    """A question or spec model as JSON. Anything else as it is."""
    if isinstance(value, pydantic.BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _parts_json(parts: Mapping[str, PartInput] | Literal["auto"] | None) -> Any:
    if isinstance(parts, Mapping):
        return {name: _json(part) for name, part in parts.items()}
    return parts


def _skill_path(skill: str) -> str:
    return f"/v1/skills/{path_part(skill, 'skill')}"


class _SkillsBase:
    """Builds each request. ``Skills`` and ``AsyncSkills`` only send it."""

    def __init__(self, client: _BaseClient) -> None:
        self._client = client

    def _create(
        self,
        name: str,
        question: QuestionInput | None,
        split: str | SplitSpec | Mapping[str, Any] | None,
        description: str | None,
        examples: Sequence[Mapping[str, Any]] | None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None,
        combine: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> _Prepared:
        body = {
            "name": name,
            "question": _json(question),
            "split": _json(split),
            "description": description,
            "examples": list(examples) if examples is not None else None,
            "parts": _parts_json(parts),
            "combine": combine,
            "metadata": metadata,
        }
        n = len(examples) if examples is not None else 0
        kind = "split" if split is not None else "decision"
        return self._client._prepare("/v1/skills", body, None, f"kind={kind} examples={n}", 0.0, retry="busy")

    def _list(self, limit: int | None, after: str | None) -> _Prepared:
        query = {"limit": limit, "after": after}
        return self._client._prepare("/v1/skills", None, None, "", 0.0, method="GET", query=query)

    def _get(self, skill: str) -> _Prepared:
        return self._client._prepare("/v1/skills/{skill}", None, None, "", 0.0, method="GET", path=_skill_path(skill))

    def _update(
        self,
        skill: str,
        name: str | None,
        description: str | None,
        question: QuestionInput | None,
        split: str | SplitSpec | Mapping[str, Any] | None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None,
        combine: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> _Prepared:
        body = {
            "name": name,
            "description": description,
            "question": _json(question),
            "split": _json(split),
            "metadata": metadata,
        }
        # None is sent for parts and combine (it removes them), so they go in after the None filter.
        removable = {}
        if parts is not UNSET:
            removable["parts"] = _parts_json(parts)
        if combine is not UNSET:
            removable["combine"] = combine
        changed = [key for key, value in body.items() if value is not None] + list(removable)
        return self._client._prepare(
            "/v1/skills/{skill}",
            body,
            removable,
            f"fields={changed!r}",
            0.0,
            method="PATCH",
            path=_skill_path(skill),
        )

    def _delete(self, skill: str) -> _Prepared:
        return self._client._prepare(
            "/v1/skills/{skill}", None, None, "", 0.0, method="DELETE", path=_skill_path(skill)
        )

    def _add_examples(self, skill: str, examples: Sequence[Mapping[str, Any]]) -> _Prepared:
        return self._client._prepare(
            "/v1/skills/{skill}/examples",
            {"examples": list(examples)},
            None,
            f"examples={len(examples)}",
            0.0,
            path=f"{_skill_path(skill)}/examples",
            retry="busy",
        )

    def _list_examples(self, skill: str, limit: int | None, after: str | None) -> _Prepared:
        return self._client._prepare(
            "/v1/skills/{skill}/examples",
            None,
            None,
            "",
            0.0,
            method="GET",
            path=f"{_skill_path(skill)}/examples",
            query={"limit": limit, "after": after},
        )

    def _delete_example(self, skill: str, example_id: str) -> _Prepared:
        return self._client._prepare(
            "/v1/skills/{skill}/examples/{example_id}",
            None,
            None,
            "",
            0.0,
            method="DELETE",
            path=f"{_skill_path(skill)}/examples/{path_part(example_id, 'example_id')}",
        )


class Skills(_SkillsBase):
    """Skills belong to the key's account. Wherever a method takes ``skill``, pass the id (``sk_...``) or the name."""

    _client: WityClient

    def __init__(self, client: WityClient) -> None:
        super().__init__(client)
        self.examples = SkillExamples(client)
        """Teach a skill more examples, list them, or remove one."""

    def create(
        self,
        *,
        name: str,
        question: QuestionInput | None = None,
        split: str | SplitSpec | Mapping[str, Any] | None = None,
        description: str | None = None,
        examples: Sequence[Mapping[str, Any]] | None = None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None = None,
        combine: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Skill:
        """Make a skill.

        With ``question``, a decision skill: ask it with ``skill(name)`` in ``questions``.
        With ``split``, a split skill: use it with ``client.split(text=..., skill=name)``.
        Not retried after an unclear failure. A retry after the skill was made would fail with ``ConflictError``.

        Args:
            name: Up to 64 characters: letters, digits, ``_``, ``-`` and ``.``. Unique in your account.
            question: A noul or choice question.
            split: What one item is: plain words or a ``SplitSpec``.
            description: For you and your team. Up to 2,000 characters.
            examples: Decision skills: up to 1,000 ``{"state", "answer"}``. Learning starts at 8, with at least 2
                for each of two answers. Split skills: up to 50 ``{"text", "items"}`` or ``{"text", "starts"}``.
            parts: Up to 32 simpler questions by name, or ``"auto"`` for Wity to write them.
            combine: A rule over the part names with ``and``, ``or``, ``not`` and brackets. Noul skills only.
            metadata: Yours to keep. Up to 8,000 characters as JSON.
        """
        p = self._create(name, question, split, description, examples, parts, combine, metadata)
        return parse(_SKILL, self._client._request(p))

    def list(self, *, limit: int | None = None, after: str | None = None) -> SkillList:
        """Newest first. ``limit`` is 1 to 100. ``after`` is the last id of the page before."""
        return parse(SkillList, self._client._request(self._list(limit, after)))

    def get(self, skill: str) -> Skill:
        return parse(_SKILL, self._client._request(self._get(skill)))

    def update(
        self,
        skill: str,
        *,
        name: str | None = None,
        description: str | None = None,
        question: QuestionInput | None = None,
        split: str | SplitSpec | Mapping[str, Any] | None = None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None = UNSET,
        combine: str | None = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> Skill:
        """Change a skill. A new question, parts or rule makes it learn again.

        A decision skill takes name, description, question, parts, combine and metadata.
        A split skill takes name, description, split and metadata.
        ``parts=None`` removes the parts and their rule. ``combine=None`` removes the rule.
        """
        p = self._update(skill, name, description, question, split, parts, combine, metadata)
        return parse(_SKILL, self._client._request(p))

    def delete(self, skill: str) -> Deleted:
        """Delete the skill and its examples."""
        return parse(Deleted, self._client._request(self._delete(skill)))


class SkillExamples(_SkillsBase):
    _client: WityClient

    def add(self, skill: str, examples: Sequence[Mapping[str, Any]]) -> AddedExamples:
        """Add 1 to 1,000 examples. The skill starts learning a moment later.

        Decision skills take ``{"state", "answer"}``, split skills ``{"text", "items"}`` or ``{"text", "starts"}``.
        Billed once, on the examples' tokens. Not retried after an unclear failure, so nothing is added twice.
        """
        return parse(AddedExamples, self._client._request(self._add_examples(skill, examples)))

    def list(self, skill: str, *, limit: int | None = None, after: str | None = None) -> ExampleList:
        """Newest first."""
        return parse(ExampleList, self._client._request(self._list_examples(skill, limit, after)))

    def delete(self, skill: str, example_id: str) -> Deleted:
        return parse(Deleted, self._client._request(self._delete_example(skill, example_id)))


class AsyncSkills(_SkillsBase):
    """Async version of ``Skills``. Same methods and arguments."""

    _client: AsyncWityClient

    def __init__(self, client: AsyncWityClient) -> None:
        super().__init__(client)
        self.examples = AsyncSkillExamples(client)

    async def create(
        self,
        *,
        name: str,
        question: QuestionInput | None = None,
        split: str | SplitSpec | Mapping[str, Any] | None = None,
        description: str | None = None,
        examples: Sequence[Mapping[str, Any]] | None = None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None = None,
        combine: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Skill:
        p = self._create(name, question, split, description, examples, parts, combine, metadata)
        return parse(_SKILL, await self._client._request(p))

    async def list(self, *, limit: int | None = None, after: str | None = None) -> SkillList:
        return parse(SkillList, await self._client._request(self._list(limit, after)))

    async def get(self, skill: str) -> Skill:
        return parse(_SKILL, await self._client._request(self._get(skill)))

    async def update(
        self,
        skill: str,
        *,
        name: str | None = None,
        description: str | None = None,
        question: QuestionInput | None = None,
        split: str | SplitSpec | Mapping[str, Any] | None = None,
        parts: Mapping[str, PartInput] | Literal["auto"] | None = UNSET,
        combine: str | None = UNSET,
        metadata: Mapping[str, Any] | None = None,
    ) -> Skill:
        p = self._update(skill, name, description, question, split, parts, combine, metadata)
        return parse(_SKILL, await self._client._request(p))

    async def delete(self, skill: str) -> Deleted:
        return parse(Deleted, await self._client._request(self._delete(skill)))


class AsyncSkillExamples(_SkillsBase):
    _client: AsyncWityClient

    async def add(self, skill: str, examples: Sequence[Mapping[str, Any]]) -> AddedExamples:
        return parse(AddedExamples, await self._client._request(self._add_examples(skill, examples)))

    async def list(self, skill: str, *, limit: int | None = None, after: str | None = None) -> ExampleList:
        return parse(ExampleList, await self._client._request(self._list_examples(skill, limit, after)))

    async def delete(self, skill: str, example_id: str) -> Deleted:
        return parse(Deleted, await self._client._request(self._delete_example(skill, example_id)))
