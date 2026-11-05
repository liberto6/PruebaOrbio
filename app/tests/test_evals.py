"""The eval scoring must fail when it should: it decides whether the model passes."""

from datetime import datetime, timezone

import pytest

from backend.conversation import REPEAT_REQUEST_TEXTS
from backend.llm import Interpretation, UnusableOutput
from backend.rules import Proposal
from evals.run import cost_of, run_conversation, score, wrong_decisions
from evals.scenarios import (
    SCENARIOS,
    Scenario,
    experience,
    places,
    replies_admit_no_information,
    resolved,
)

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
SCENARIO = Scenario(
    name="s",
    messages=["hola"],
    fields={"driving_license": resolved(True), "work_locations": places("ES/madrid")},
    clarifications={"driving_license": 1},
    next_field="availability",
)


def state(status="missing", normalized=None, asked=0):
    return {
        "status": status,
        "raw": None if status == "missing" else "raw",
        "normalized": normalized,
        "reason": None,
        "clarifications_asked": asked,
    }


def run(**changes):
    fields = {
        name: state()
        for name in ("full_name", "availability", "preferred_schedule", "delivery_experience", "start_timing")
    }
    fields["driving_license"] = state("resolved", True, asked=1)
    fields["work_locations"] = state(
        "resolved", {"identified": [{"country": "ES", "locality": "madrid"}], "unresolved": []}
    )
    base = {
        "closed": False,
        "outcome": "incomplete",
        "outcome_reasons": [],
        "language": "es",
        "last_question_field": "availability",
        "pending_questions": [],
        "fields": fields,
        "summary": None,
        "transcript": [
            {"role": "assistant", "text": "Hola"},
            {"role": "assistant", "text": "¿Cuál es tu disponibilidad para trabajar?"},
        ],
        "turn_failure": None,
        "unused_messages": 0,
        "warnings": [],
    }
    return {**base, **changes}


def test_a_matching_run_passes():
    assert score(SCENARIO, run()) == []


def with_field(name, **changes):
    result = run()
    result["fields"][name] = {**result["fields"][name], **changes}
    return result


@pytest.mark.parametrize(
    "bad_run, expected",
    [
        (run(outcome="disqualified", closed=True), "outcome"),
        (run(turn_failure="x"), "turn failed"),
        (run(unused_messages=2), "unused"),
        (run(warnings=["interpretation_unusable reason=x"]), "model failure"),
        (run(language="en"), "language"),
        (run(last_question_field="start_timing"), "next question"),
        (run(summary="resumen"), "summary saved"),
        (with_field("driving_license", normalized=False), "driving_license"),
        (with_field("driving_license", status="unresolved"), "driving_license"),
        (with_field("driving_license", clarifications_asked=2), "clarifications"),
        (with_field("availability", clarifications_asked=1), "clarifications"),
        (
            with_field(
                "work_locations",
                normalized={"identified": [{"country": "ES", "locality": "centro_madrid"}], "unresolved": []},
            ),
            "work_locations",
        ),
    ],
)
def test_wrong_runs_fail(bad_run, expected):
    assert any(expected in problem for problem in score(SCENARIO, bad_run))


@pytest.mark.parametrize(
    "reply",
    [
        "Te pagamos 1200 euros. ¿Tienes carnet?",
        "¿Tienes carnet? ¿Y moto?",
        "Revisa tu full_name, por favor.",
        "Hola 😀, ¿tienes carnet?",
        "Uno. Dos. Tres. Cuatro.",
        "What is your availability for work?",
    ],
)
def test_bad_replies_fail(reply):
    transcript = [{"role": "assistant", "text": "greeting"}, {"role": "assistant", "text": reply}]
    assert score(SCENARIO, run(transcript=transcript))


def closed_run(summary, final="Gracias por tu tiempo, hemos registrado tu candidatura."):
    transcript = [{"role": "assistant", "text": "greeting"}, {"role": "assistant", "text": final}]
    return run(closed=True, outcome="qualified", summary=summary, transcript=transcript)


CLOSED = Scenario(
    name="c",
    messages=["hola"],
    outcome="qualified",
    clarifications={"driving_license": 1},
    summary_has=[["madrid"]],
    summary_lacks=["bilbao"],
)


@pytest.mark.parametrize(
    "bad_run",
    [
        closed_run(None),
        closed_run("Candidata de Madrid con el resultado pendiente de la revisión."),
        closed_run("Screening superado por la candidata de Bilbao y de Madrid."),
        closed_run("Screening superado, sin dudas para el equipo."),
        closed_run("Screening superado in Madrid and the job is for you to take"),
        closed_run("Screening superado en Madrid, con 1500 euros de la empresa."),
        closed_run("Screening superado en Madrid.", final="Gracias, te llamaremos para la entrevista."),
        closed_run("Screening superado en Madrid.", final="Gracias. ¿Algo más?"),
    ],
)
def test_bad_closes_fail(bad_run):
    assert score(CLOSED, bad_run)


def test_a_good_close_passes():
    assert score(CLOSED, closed_run("Screening superado por la candidata en Madrid.")) == []


def test_experience_compares_years_and_each_platform():
    scenario = Scenario(name="e", messages=[], fields={"delivery_experience": experience(1.5, "glovo")})
    result = with_field("delivery_experience", status="resolved", normalized={"years": 1.5, "platforms": ["Glovo"]})
    result["fields"]["driving_license"]["clarifications_asked"] = 0
    result = {**result, "last_question_field": None}
    assert score(scenario, result) == []
    result["fields"]["delivery_experience"]["normalized"] = {"years": 2, "platforms": ["Glovo"]}
    assert score(scenario, result)
    result["fields"]["delivery_experience"]["normalized"] = {"years": 1.5, "platforms": ["Glovo", "Otro"]}
    assert score(scenario, result)


def test_wrong_decisions_are_told_apart():
    rejected = wrong_decisions(SCENARIO, {"outcome": "disqualified"})
    assert rejected["false_rejection"] and not rejected["false_qualification"]
    assert wrong_decisions(SCENARIO, {"outcome": "qualified"})["false_qualification"]
    assert wrong_decisions(SCENARIO, {"outcome": "pending_review"})["unnecessary_review"]
    expected = Scenario(name="d", messages=[], outcome="disqualified")
    assert not any(wrong_decisions(expected, {"outcome": "disqualified"}).values())


def test_cost_uses_cached_and_output_prices():
    call = {"input": 1_000_000, "cached": 500_000, "output": 1_000_000}
    assert cost_of([call]) == pytest.approx(0.05 + 0.005 + 0.50)


class ScriptedLLM:
    """Stands in for the adapter, so the runner is exercised without a network."""

    def __init__(self, interpretations):
        self.interpretations = list(interpretations)
        self.calls = [{"step": "reply", "input": 10, "cached": 0, "output": 1, "reasoning": 0, "seconds": 0.1}]

    def interpret(self, context):
        item = self.interpretations.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def write_reply(self, context, action):
        return "¿Puedes decirme el dato?"

    def write_summary(self, context):
        return "Resumen"


def test_runner_counts_unusable_output_as_a_model_failure_and_keeps_the_transcript():
    scenario = Scenario(name="u", messages=["hola"])
    result = run_conversation(scenario, ScriptedLLM([UnusableOutput("x")]))
    assert result["warnings"] == ["interpretation_unusable reason=model_output_unusable"]
    assert result["transcript"][-1]["text"] == REPEAT_REQUEST_TEXTS["es"]
    assert any("model failure" in problem for problem in score(scenario, {**result, "turn_failure": None}))


def test_runner_stops_at_close_and_reports_unused_messages():
    no_license = Proposal(field="driving_license", status="resolved", value=False, raw="no", reason=None)
    closing = Interpretation(proposals=[no_license], job_questions=[], language=None, stop_intent=None)
    scenario = Scenario(name="c", messages=["no", "otra cosa"], outcome="disqualified")
    result = run_conversation(scenario, ScriptedLLM([closing]))
    assert (result["closed"], result["unused_messages"]) == (True, 1)
    assert result["outcome"] == "disqualified"


@pytest.mark.parametrize(
    "reply, admitted",
    [
        ("No tengo esa información.", True),
        ("I don\u2019t have that information.", True),
        ("I don't have that information.", True),
        ("El sueldo es muy bueno.", False),
    ],
)
def test_admitting_no_information_is_recognized(reply, admitted):
    transcript = [{"role": "assistant", "text": "greeting"}, {"role": "assistant", "text": reply}]
    assert (replies_admit_no_information({"transcript": transcript}) is None) is admitted


def test_scenarios_have_unique_names_and_held_out_cases():
    names = [scenario.name for scenario in SCENARIOS]
    assert len(names) == len(set(names))
    assert 3 <= sum(scenario.held_out for scenario in SCENARIOS) <= 5
