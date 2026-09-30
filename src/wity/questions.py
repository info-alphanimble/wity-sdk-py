"""Small helpers that build question objects.

The API checks the limits (option counts, lengths) and explains any problem in its error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import WityError
from .types import Choice, Noul, NoulCriteria, Score


def choice(instructions: str, criteria: Mapping[str | int, str]) -> Choice:
    """Pick one option.

    Example: ``choice("Which team?", {"billing": "Payments and refunds", "technical": "Bugs"})``
    """
    # Ids written as numbers ({1: "Ground"}) become strings ("1"), like the API returns them.
    return Choice(instructions=instructions, criteria={str(key): value for key, value in criteria.items()})


def noul(instructions: str, *, yes: str | None = None, no: str | None = None) -> Noul:
    """Yes or no. Optionally describe what yes and no mean. Give both or neither.

    Example: ``noul("Is this urgent?", yes="Needs action today", no="Can wait")``
    """
    if (yes is None) != (no is None):
        raise WityError("noul() needs both yes and no, or neither.")
    if yes is None or no is None:
        return Noul(instructions=instructions)
    return Noul(instructions=instructions, criteria=NoulCriteria(true=yes, false=no))


def score(instructions: str, levels: Sequence[str]) -> Score:
    """Pick a level on a scale of 2 to 10 levels, lowest first.

    Levels can come from a variable, for example a list loaded from a database.

    Example: ``score("How upset is the customer?", ["Calm", "Annoyed", "Angry"])``
    """
    if isinstance(levels, str):
        raise WityError("score() needs a list of levels, not a single string.")
    return Score(instructions=instructions, criteria=list(levels))
