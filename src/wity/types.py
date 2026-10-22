"""Request and response shapes for every Wity route.

Field names match the real API responses saved in tests/fixtures/.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Reasoning = Literal["off", "auto", "always"]
"""How much Wity thinks before answering. The API default is ``"auto"``."""

State = str | dict[str, Any] | list[Any]
"""What a request is about. Dicts and lists are read as JSON."""

# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


class _Question(BaseModel):
    # A misspelled field is an error, not something quietly dropped.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Choice(_Question):
    """Pick one option. ``criteria`` maps each option id to a description."""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]


class NoulCriteria(_Question):
    """What yes (``true``) and no (``false``) mean."""

    true: str
    false: str


class Noul(_Question):
    """Yes or no. ``criteria`` optionally describes what yes and no mean."""

    type: Literal["noul"] = "noul"
    instructions: str
    criteria: NoulCriteria | None = None


class Score(_Question):
    """Pick a level on a scale. ``criteria`` lists 2 to 10 levels, lowest first. Wity checks the count."""

    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str]


Question = Choice | Noul | Score


class SkillQuestion(_Question):
    """Ask a saved skill's question. The answer is a noul or choice answer, with ``skill`` added."""

    skill: str
    """The skill's id (``sk_...``) or name. A split skill can't be a question: use it with ``split``."""


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


class _Answer(BaseModel):
    # Fields the API adds later are kept, so older SDK versions don't break.
    model_config = ConfigDict(extra="allow", frozen=True)


class ReasoningInfo(_Answer):
    """How Wity reasoned about one answer. Missing when reasoning is ``"off"``."""

    mode: str
    """``"auto"`` or ``"always"``."""
    thought: bool
    """Whether Wity thought before answering."""
    reason: str | None
    """Why it thought, for example ``"close_call"`` or ``"requested"``. ``None`` if it didn't."""
    forecast: bool
    """Whether the question asks for a likelihood."""
    thought_tokens: int | None
    """Tokens spent thinking. ``None`` if it didn't think."""
    budget_limited: bool
    """``True`` when ``max_latency_ms`` cut the thinking short."""
    samples: int


class SkillInfo(_Answer):
    """Which skill answered, and how."""

    id: str
    name: str
    version: int
    """The skill version that answered."""
    answered_by: str
    """``"question"``, ``"rule"``, ``"parts"`` or ``"examples"``. ``"examples"`` once the skill has learned
    from its examples and beats the plain question."""
    active: bool
    """``True`` when the answer used what the skill learned from its examples."""


class ChoiceAnswer(_Answer):
    type: Literal["choice"]
    choice: str
    """The option with the highest probability."""
    probabilities: dict[str, float]
    """Probability per option. They sum to 1. They are not calibrated."""
    confidence: float
    direct_probabilities: dict[str, float] | None = None
    """Probabilities before thinking. Only present when Wity thought."""
    reasoning: ReasoningInfo | None = None
    skill: SkillInfo | None = None
    """Only on answers to skill questions."""
    parts: dict[str, ChoiceAnswer | NoulAnswer] | None = None
    """The answers to a skill's parts, by part name. Only for skills with parts."""


class NoulAnswer(_Answer):
    type: Literal["noul"]
    noul: float
    """Probability of yes, from 0 to 1. It is not calibrated."""
    direct_noul: float | None = None
    """Probability of yes before thinking. Only present when Wity thought."""
    reasoning: ReasoningInfo | None = None
    skill: SkillInfo | None = None
    """Only on answers to skill questions."""
    parts: dict[str, ChoiceAnswer | NoulAnswer] | None = None
    """The answers to a skill's parts, by part name. Only for skills with parts."""


class ScoreAnswer(_Answer):
    type: Literal["score"]
    score: float
    """Expected level. It can fall between levels, for example 1.97."""
    legend: dict[str, str]
    """Level descriptions keyed by level number ("0", "1", ...)."""
    probabilities: dict[str, float]
    """Probability per level number. They sum to 1. They are not calibrated."""
    confidence: float
    direct_probabilities: dict[str, float] | None = None
    """Probabilities before thinking. Only present when Wity thought."""
    reasoning: ReasoningInfo | None = None


class OtherAnswer(_Answer):
    """An answer type this SDK version doesn't know yet. All its fields are kept."""

    type: str


# Tried in order, so a known type always wins over OtherAnswer.
Answer = Annotated[ChoiceAnswer | NoulAnswer | ScoreAnswer | OtherAnswer, Field(union_mode="left_to_right")]

# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class Usage(_Answer):
    input_tokens: int
    output_tokens: int
    """Reported, not billed. Always 0 for system_one."""


class SystemOneMetadata(_Answer):
    reasoning: str
    elapsed_ms: float


class SystemOneResponse(_Answer):
    model: str
    """Always ``"wity-1"`` today."""
    answers: dict[str, Answer]
    """Every answer, under the name you gave its question."""
    metadata: SystemOneMetadata
    usage: Usage

    @property
    def choices(self) -> dict[str, ChoiceAnswer]:
        """The choice answers, by question name."""
        return {name: a for name, a in self.answers.items() if isinstance(a, ChoiceAnswer)}

    @property
    def nouls(self) -> dict[str, NoulAnswer]:
        """The yes/no answers, by question name."""
        return {name: a for name, a in self.answers.items() if isinstance(a, NoulAnswer)}

    @property
    def scores(self) -> dict[str, ScoreAnswer]:
        """The score answers, by question name."""
        return {name: a for name, a in self.answers.items() if isinstance(a, ScoreAnswer)}


class GenerateMetadata(_Answer):
    elapsed_ms: float


class GenerateResponse(_Answer):
    model: str
    """Always ``"wity-1"`` today."""
    text: str
    """What Wity wrote. With a shape, this is the JSON as text. If cut off, it's incomplete."""
    value: dict[str, Any] | None = None
    """The parsed JSON when you sent a shape and Wity finished. ``None`` without a shape, or when cut off."""
    finish_reason: str
    """``"stop"`` when finished. ``"length"`` means Wity hit ``max_tokens``."""
    usage: Usage
    metadata: GenerateMetadata


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


class SplitSpec(_Question):
    """A precise description of one item."""

    unit: str
    """What one item is, 2 to 80 characters: ``"email"``, ``"clause"``."""
    starts_at: list[str] | None = None
    """What a new item starts with. Up to 8 entries of up to 300 characters."""
    keep_together: list[str] | None = None
    """What stays inside one item. Up to 8."""
    never_split: list[str] | None = None
    """Where an item never starts. Up to 8."""


class SplitItem(_Answer):
    start: int
    """Character offset where the item starts."""
    end: int
    """Character offset where it ends (exclusive)."""
    text: str
    """Exactly ``text[start:end]``."""


class SplitTiming(_Answer):
    total: float


class SplitResponse(_Answer):
    items: list[SplitItem]
    """Every item, in document order. Together they cover the document but the whitespace between them."""
    how: str
    """``"model"``, or ``"rule:<kind>"`` when the unit was cut by rule, like ``"rule:sentence"``."""
    skill: SkillInfo | None = None
    """Only when you split with a skill."""
    timing_ms: SplitTiming
    usage: Usage


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

ExtractType = Literal["date", "duration", "number"]
"""Typed fields come back normalised: dates as YYYY-MM-DD."""


class ExtractEntity(_Answer):
    field: str
    text: str
    """The value as written. For a typed field, the normalised value."""
    start: int
    """Character offset where the value was read."""
    end: int
    """Where it ends (exclusive)."""
    confidence: float
    """From 0 to 1. It is not calibrated."""


class ExtractResponse(_Answer):
    fields: dict[str, list[str]]
    """Every field you asked for, in your order, with its values. ``[]`` when the document has none."""
    entities: list[ExtractEntity]
    """The same values with positions and confidence, by field and then by position."""
    timing_ms: float
    """Time spent on the server."""
    usage: Usage


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class Deleted(_Answer):
    id: str
    object: str
    deleted: bool


class SkillQuality(_Answer):
    """Accuracy on the skill's own held-back examples, with and without what it learned."""

    accuracy: float
    accuracy_without_skill: float
    checked_on: int


class _SkillBase(_Answer):
    id: str
    """``sk_...``. Methods take the id or the name."""
    object: Literal["skill"] = "skill"
    name: str
    description: str
    metadata: dict[str, Any]
    examples: int
    """How many examples it has."""
    status: str
    """``"needs_examples"``, ``"learning"`` or ``"ready"``."""
    answered_by: str
    """How it answers now: ``"question"``, ``"rule"``, ``"parts"`` or ``"examples"``."""
    active: bool
    """``True`` when answers use what it learned from its examples."""
    version: int
    """Goes up each time it learns or changes."""
    quality: SkillQuality | None = None
    """``None`` until it has learned."""
    created_at: int
    """Unix seconds."""
    updated_at: int
    usage: Usage | None = None
    """Only from ``create``: what adding its examples cost."""


class DecisionSkill(_SkillBase):
    """A question you save once and teach with labelled examples. Ask it with ``SkillQuestion``."""

    kind: Literal["decision"]
    question: dict[str, Any]
    """A noul or choice question."""
    parts: dict[str, dict[str, Any]] | None = None
    """Simpler questions the main one is split into, by name."""
    combine: str | None = None
    """The rule over the part names, like ``"not same_state and amount"``."""


class SplitSkill(_SkillBase):
    """A way to split, taught with example splits. Use it with ``split(text=..., skill=name)``."""

    kind: Literal["split"]
    split: str | dict[str, Any]


Skill = Annotated[DecisionSkill | SplitSkill, Field(discriminator="kind")]


class DecisionExample(_Answer):
    id: str
    object: str
    state: str
    answer: str
    source: str
    """``"api"`` for examples you sent, ``"session"`` for session feedback."""
    session_id: str | None = None
    """Only for examples made from session feedback."""
    created_at: int


class SplitExample(_Answer):
    id: str
    object: str
    text: str
    starts: list[int]
    items: list[str]
    source: str
    created_at: int


# A split example has ``starts``; a decision example has ``answer``.
SkillExample = Annotated[SplitExample | DecisionExample, Field(union_mode="left_to_right")]


class SkillList(_Answer):
    data: list[Skill]
    has_more: bool
    """Pass the last skill's id as ``after`` to get the next page."""


class ExampleList(_Answer):
    data: list[SkillExample]
    has_more: bool


class AddedExamples(_Answer):
    data: list[SkillExample]
    skill: Skill
    """The skill after the change. It starts learning a moment later."""
    usage: Usage


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class Session(_Answer):
    id: str
    """``ses_...``."""
    object: Literal["session"] = "session"
    state: str
    """What Wity reads first. Changes only through events."""
    skills: list[str]
    """Skill ids the session decides with when ``decide`` names no questions."""
    metadata: dict[str, Any]
    events: int
    """How many events the log holds."""
    created_at: int
    """Unix seconds."""
    updated_at: int
    expires_at: int


class SessionEvent(_Answer):
    id: str
    object: str
    type: str
    """``"update"``, ``"state"``, ``"note"``, ``"feedback"`` or ``"decision"``."""
    content: dict[str, Any]
    created_at: int


class SessionList(_Answer):
    data: list[Session]
    has_more: bool


class EventList(_Answer):
    data: list[SessionEvent]
    has_more: bool


class AddedEvents(_Answer):
    data: list[SessionEvent]
    session: Session
    """The session after the change."""


class DecisionContext(_Answer):
    updates_read: int
    updates_left_out: int
    """How many of the oldest updates were left out to fit."""


class SessionDecision(SystemOneResponse):
    decision_id: str
    """Give this to a feedback event to correct the answer."""
    session_id: str
    context: DecisionContext
    cached: bool
    """``True`` when the same contents and questions were answered before. Same answer, 0 tokens."""
