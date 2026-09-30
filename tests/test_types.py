from __future__ import annotations

from typing import Any

import pydantic
import pytest

from wity import Choice, Noul, NoulCriteria, Score, WityError, choice, noul, score


def as_json(question: pydantic.BaseModel) -> dict[str, Any]:
    return question.model_dump(mode="json", exclude_none=True)


class TestBuilders:
    def test_make_the_question_objects_the_api_expects(self) -> None:
        assert as_json(choice("Which team?", {"billing": "Payments", "technical": "Bugs"})) == {
            "type": "choice",
            "instructions": "Which team?",
            "criteria": {"billing": "Payments", "technical": "Bugs"},
        }
        assert as_json(noul("Is it urgent?")) == {"type": "noul", "instructions": "Is it urgent?"}
        assert as_json(noul("Is it urgent?", yes="Today", no="Can wait")) == {
            "type": "noul",
            "instructions": "Is it urgent?",
            "criteria": {"true": "Today", "false": "Can wait"},
        }
        assert as_json(score("How bad?", ["Low", "High"])) == {
            "type": "score",
            "instructions": "How bad?",
            "criteria": ["Low", "High"],
        }

    def test_option_ids_written_as_numbers_become_strings_like_the_api_returns_them(self) -> None:
        assert choice("Which floor?", {1: "Ground", 2: "First"}).criteria == {"1": "Ground", "2": "First"}

    def test_score_levels_can_come_from_any_sequence(self) -> None:
        levels_from_db = ("Calm", "Annoyed", "Angry")
        assert score("How upset?", levels_from_db).criteria == ["Calm", "Annoyed", "Angry"]

    def test_the_classes_can_be_used_directly(self) -> None:
        assert Choice(instructions="Which?", criteria={"a": "A", "b": "B"}) == choice("Which?", {"a": "A", "b": "B"})
        assert Noul(instructions="Yes?", criteria=NoulCriteria(true="y", false="n")) == noul("Yes?", yes="y", no="n")
        assert Score(instructions="How?", criteria=["x", "y"]) == score("How?", ["x", "y"])


class TestWrongInput:
    @pytest.mark.parametrize("given", [{"yes": "Today"}, {"no": "Can wait"}])
    def test_noul_needs_both_yes_and_no_or_neither(self, given: dict[str, str]) -> None:
        with pytest.raises(WityError, match="both yes and no"):
            noul("Is it urgent?", **given)

    def test_score_refuses_a_single_string(self) -> None:
        with pytest.raises(WityError, match="list of levels"):
            score("How bad?", "Low")

    def test_a_misspelled_field_is_an_error_not_dropped(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="criterion"):
            Choice(instructions="Which?", criterion={"a": "A"})  # type: ignore[call-arg]

    def test_choice_criteria_must_be_a_mapping(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            Choice(instructions="Which?", criteria=["a", "b"])  # type: ignore[arg-type]

    def test_questions_cant_be_changed_after_building(self) -> None:
        question = noul("Is it urgent?")
        with pytest.raises(pydantic.ValidationError):
            question.instructions = "Something else"
