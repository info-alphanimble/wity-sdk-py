"""Request and response shapes for POST /v1/systemone and POST /v1/generate.

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


class NoulAnswer(_Answer):
    type: Literal["noul"]
    noul: float
    """Probability of yes, from 0 to 1. It is not calibrated."""
    direct_noul: float | None = None
    """Probability of yes before thinking. Only present when Wity thought."""
    reasoning: ReasoningInfo | None = None


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
