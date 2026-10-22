"""Helpers shared by the clients and the skills and sessions resources."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypeVar, overload
from urllib.parse import quote

import pydantic

from ._logging import safe_name
from .errors import WityError
from .types import Choice, Noul, Score, SkillQuestion

_Model = TypeVar("_Model", bound=pydantic.BaseModel)
_T = TypeVar("_T")

_LATENCY_MARGIN = 10.0
"""Extra seconds on top of ``max_latency_ms`` for the network and Wity's own work."""


@overload
def parse(model: type[_Model], data: dict[str, Any]) -> _Model: ...
@overload
def parse(model: pydantic.TypeAdapter[_T], data: dict[str, Any]) -> _T: ...
def parse(model: Any, data: dict[str, Any]) -> Any:
    """A model class, or a ``TypeAdapter`` for a union like ``Skill``."""
    try:
        if isinstance(model, pydantic.TypeAdapter):
            return model.validate_python(data)
        return model.model_validate(data)
    except pydantic.ValidationError as err:
        raise WityError(
            "Wity's answer doesn't have the shape this SDK expects. Update the wity package, or check WITY_BASE_URL."
        ) from err


def path_part(value: object, what: str) -> str:
    """An id or name, ready to go in a path. ``.`` and ``..`` are refused: in a URL they point somewhere else."""
    if not isinstance(value, str) or not value:
        raise WityError(f"{what} must be a non-empty string.")
    if value in (".", ".."):
        raise WityError(f'{what} can\'t be "{value}" in a URL. Use the id instead.')
    return quote(value, safe="")


def latency_timeout(max_latency_ms: float | None) -> float:
    """Give Wity the full time ``max_latency_ms`` allows, plus room for the network. Seconds."""
    latency = max_latency_ms
    if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency):
        return latency / 1000 + _LATENCY_MARGIN
    return 0.0


def question_json(name: str, question: object) -> Any:
    if isinstance(question, (Choice, Noul, Score, SkillQuestion)):
        return question.model_dump(mode="json", exclude_none=True)
    if isinstance(question, Mapping):
        # Plain dicts are sent as they are, so question fields newer than this SDK still work.
        return dict(question)
    raise WityError(
        f"Question {safe_name(name)!r} must be built with choice(), noul(), score() or skill(), or be a dict."
    )
