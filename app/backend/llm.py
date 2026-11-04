"""What the coordinator expects from a language model: schemas and failure types only."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field

from backend.rules import (
    CandidateFields,
    FieldName,
    Language,
    Location,
    Outcome,
    Proposal,
    QuestionKind,
    StrictModel,
)
from backend.storage import Message


class Interpretation(StrictModel):
    proposals: list[Proposal]
    job_questions: list[str]
    language: Language | None  # None keeps the current language.
    stop_intent: Literal["stop", "resume"] | None  # None keeps the current stop state.


class Action(StrictModel):
    kind: Literal["acknowledge_stop", "close", "ask", "clarify"]
    field: FieldName | None = None
    attempt: Literal[1, 2] | None = None  # Clarify only: 1 rephrases, 2 offers examples or options.


class TurnContext(StrictModel):
    """Built only from persisted state, so a retry behaves the same as the first pass."""

    messages: list[Message]
    fields: CandidateFields
    pending_questions: list[str]
    language: Language
    last_question_field: FieldName | None
    last_question_kind: QuestionKind | None
    catalog: list[Location]
    now: datetime
    # Closing only: what rules.evaluate says about the saved fields.
    outcome: Outcome | None = None
    outcome_reasons: list[str] = Field(default_factory=list)


class ScreeningLLM(Protocol):
    def interpret(self, context: TurnContext) -> Interpretation: ...

    def write_reply(self, context: TurnContext, action: Action) -> str: ...

    def write_summary(self, context: TurnContext) -> str: ...


class LLMFailure(Exception):
    """The model gave no usable output: refusal, truncation, timeout or provider error."""

    transient = False


class TransientLLMFailure(LLMFailure):
    """May work if repeated. The adapter applies the one automatic retry, not the coordinator."""

    transient = True


class PermanentLLMFailure(LLMFailure):
    """Repeating it automatically is pointless: invalid credentials, refusal and the like."""
