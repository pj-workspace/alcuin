"""User clarification contracts, separate from mutation permission decisions."""
from typing import Annotated

from pydantic import Field, model_validator

from .contracts import StrictModel


class HumanQuestion(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    options: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_question(self) -> "HumanQuestion":
        self.question = self.question.strip()
        self.options = [option.strip() for option in self.options]
        if not self.question or any(not option for option in self.options):
            raise ValueError("Question and options cannot be blank")
        if len(set(self.options)) != len(self.options):
            raise ValueError("Question options must be distinct")
        return self


class HumanAnswer(StrictModel):
    answer: str = Field(default="", max_length=4_000)
    skip: bool = False

    @model_validator(mode="after")
    def validate_answer(self) -> "HumanAnswer":
        self.answer = self.answer.strip()
        if self.skip == bool(self.answer):
            raise ValueError("Provide an answer or choose to continue without one")
        return self
