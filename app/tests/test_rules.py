import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.rules import CandidateSnapshot, apply_proposals, new_snapshot

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
FIELD_NAMES = {
    "full_name",
    "driving_license",
    "work_locations",
    "availability",
    "preferred_schedule",
    "delivery_experience",
    "start_timing",
}


def test_new_snapshot_json_keeps_every_key_as_missing():
    data = json.loads(new_snapshot("app-1", NOW).model_dump_json())

    assert set(data) == {
        "id",
        "created_at",
        "updated_at",
        "closed_at",
        "outcome",
        "outcome_reasons",
        "fields",
        "pending_questions",
        "summary",
        "recruiter_decision",
        "revision",
        "stop_requested",
    }
    assert data["closed_at"] is None
    assert data["outcome"] == "incomplete"
    assert data["outcome_reasons"] == []
    assert data["pending_questions"] == []
    assert data["summary"] is None
    assert data["recruiter_decision"] == "unreviewed"
    assert data["revision"] == 0
    assert data["stop_requested"] is False
    assert set(data["fields"]) == FIELD_NAMES
    for field in data["fields"].values():
        assert field == {
            "raw": None,
            "normalized": None,
            "status": "missing",
            "reason": None,
            "clarifications_asked": 0,
        }


def test_snapshot_survives_a_json_round_trip():
    snapshot = new_snapshot("app-1", NOW)

    assert CandidateSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_snapshot_json_without_a_field_key_is_invalid():
    data = json.loads(new_snapshot("app-1", NOW).model_dump_json())
    del data["fields"]["start_timing"]

    with pytest.raises(ValidationError):
        CandidateSnapshot.model_validate_json(json.dumps(data))


def proposal(field, value, status="resolved", raw="candidate text", reason=None):
    return {
        "field": field,
        "status": status,
        "value": value,
        "raw": raw,
        "reason": reason,
    }


def missing_fields():
    return new_snapshot("app-1", NOW).fields


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("driving_license", False, False),
        ("delivery_experience", {"amount": 0, "unit": "years", "platforms": []},
         {"years": 0, "platforms": []}),
    ],
)
def test_falsy_answers_are_resolved_and_distinct_from_null(field, value, expected):
    result = apply_proposals(missing_fields(), [proposal(field, value)])

    data = json.loads(result.fields.model_dump_json())
    assert result.errors == []
    assert data[field]["status"] == "resolved"
    assert data[field]["normalized"] == expected
    assert data[field]["normalized"] is not None


def test_applying_a_proposal_replaces_raw_and_keeps_clarification_count():
    fields = missing_fields()
    fields.start_timing.clarifications_asked = 1

    result = apply_proposals(
        fields, [proposal("start_timing", {"kind": "unknown", "value": None}, raw="no sé")]
    )

    assert result.fields.start_timing.raw == "no sé"
    assert result.fields.start_timing.clarifications_asked == 1


def test_applying_proposals_does_not_mutate_the_input_fields():
    fields = missing_fields()

    apply_proposals(fields, [proposal("driving_license", True)])

    assert fields.driving_license.status == "missing"


def test_incomplete_name_is_submitted_as_unresolved():
    result = apply_proposals(
        missing_fields(),
        [proposal("full_name", None, status="unresolved", raw="Ana", reason="name_incomplete")],
    )

    assert result.errors == []
    field = result.fields.full_name
    assert (field.status, field.normalized, field.raw, field.reason) == (
        "unresolved",
        None,
        "Ana",
        "name_incomplete",
    )


def test_complete_name_is_accepted_on_the_interpreter_assertion():
    result = apply_proposals(missing_fields(), [proposal("full_name", "Ana", raw="Soy Ana")])

    field = result.fields.full_name
    assert (field.status, field.normalized, field.raw) == ("resolved", "Ana", "Soy Ana")


def test_unresolved_correction_clears_the_old_normalized_value():
    first = apply_proposals(missing_fields(), [proposal("driving_license", True, raw="sí")])

    second = apply_proposals(
        first.fields,
        [proposal("driving_license", None, status="unresolved", raw="bueno, no sé", reason="license_ambiguous")],
    )

    field = second.fields.driving_license
    assert (field.status, field.normalized, field.raw) == ("unresolved", None, "bueno, no sé")


@pytest.mark.parametrize(
    "submitted",
    [
        proposal("full_name", None),
        proposal("full_name", "Ana", status="unresolved", reason="name_incomplete"),
        proposal("full_name", None, status="unresolved", reason=None),
        proposal("full_name", None, status="unresolved", raw=None, reason="name_incomplete"),
        proposal("full_name", "Ana", status="missing"),
        proposal("full_name", "Ana", raw="   "),
        proposal("full_name", None, status="unresolved", reason="  "),
        {"field": "full_name", "status": "resolved", "value": "Ana", "raw": "Ana"},
        {**proposal("full_name", "Ana"), "extra": 1},
    ],
)
def test_invalid_proposal_shape_leaves_field_unchanged_and_reports_it(submitted):
    start = apply_proposals(missing_fields(), [proposal("full_name", "Ana García")]).fields

    result = apply_proposals(start, [submitted])

    assert result.fields == start
    assert [error.field for error in result.errors] == ["full_name"]
    assert result.errors[0].error


@pytest.mark.parametrize(
    "submitted",
    ["not a proposal", None, proposal("salary", 1), {**proposal("salary", 1), "field": 3}],
)
def test_proposal_without_a_known_field_is_reported_and_changes_nothing(submitted):
    start = missing_fields()

    result = apply_proposals(start, [submitted])

    assert result.fields == start
    assert len(result.errors) == 1


def test_invalid_proposal_does_not_block_valid_fields_in_the_same_turn():
    result = apply_proposals(
        missing_fields(),
        [
            proposal("full_name", "Ana García"),
            proposal("driving_license", "yes"),
            proposal("start_timing", {"kind": "unknown", "value": None}),
        ],
    )

    assert [error.field for error in result.errors] == ["driving_license"]
    assert result.fields.full_name.normalized == "Ana García"
    assert result.fields.driving_license.status == "missing"
    assert result.fields.start_timing.status == "resolved"


@pytest.mark.parametrize(
    "field_json",
    [
        {"raw": "x", "normalized": True, "status": "missing", "reason": None, "clarifications_asked": 0},
        {"raw": "x", "normalized": True, "status": "unresolved", "reason": "r", "clarifications_asked": 0},
        {"raw": None, "normalized": None, "status": "unresolved", "reason": "r", "clarifications_asked": 0},
        {"raw": "x", "normalized": None, "status": "resolved", "reason": None, "clarifications_asked": 0},
        {"raw": None, "normalized": True, "status": "resolved", "reason": None, "clarifications_asked": -1},
    ],
)
def test_snapshot_with_an_inconsistent_field_state_is_invalid(field_json):
    data = json.loads(new_snapshot("app-1", NOW).model_dump_json())
    data["fields"]["driving_license"] = field_json

    with pytest.raises(ValidationError):
        CandidateSnapshot.model_validate_json(json.dumps(data))


def experience(amount, unit="years", platforms=("Glovo",)):
    return {"amount": amount, "unit": unit, "platforms": list(platforms)}


@pytest.mark.parametrize(
    ("amount", "unit", "years"),
    [
        (18, "months", 1.5),
        (6, "months", 0.5),
        (7, "months", 7 / 12),
        (0.5, "years", 0.5),
        (3, "years", 3),
    ],
)
def test_experience_is_normalized_to_years_without_rounding(amount, unit, years):
    result = apply_proposals(
        missing_fields(), [proposal("delivery_experience", experience(amount, unit), raw="un tiempo en Glovo")]
    )

    field = result.fields.delivery_experience
    assert result.errors == []
    assert field.status == "resolved"
    assert field.normalized.years == years
    assert field.normalized.platforms == ["Glovo"]
    assert field.raw == "un tiempo en Glovo"


def test_local_business_counts_as_a_platform():
    result = apply_proposals(
        missing_fields(),
        [proposal("delivery_experience", experience(2, platforms=["Pizzería Luigi"]))],
    )

    assert result.fields.delivery_experience.normalized.platforms == ["Pizzería Luigi"]


@pytest.mark.parametrize(
    "value",
    [
        experience(True),
        experience(False, platforms=[]),
        experience("3"),
        experience(None),
        experience(-1),
        experience(float("nan")),
        experience(float("inf")),
        experience(10**400),
        experience(1, unit="weeks"),
        experience(1, platforms=[""]),
        experience(1, platforms=[3]),
        {"amount": 1, "unit": "years"},
        {"years": 1, "platforms": ["Glovo"]},
        None,
        3,
    ],
)
def test_invalid_experience_value_is_reported(value):
    result = apply_proposals(missing_fields(), [proposal("delivery_experience", value)])

    assert [error.field for error in result.errors] == ["delivery_experience"]
    assert result.fields.delivery_experience.status == "missing"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (experience(0, platforms=["Glovo"]), "experience_zero_with_platforms"),
        (experience(0, "months", ["Glovo"]), "experience_zero_with_platforms"),
        (experience(2, platforms=[]), "experience_without_platforms"),
    ],
)
def test_contradictory_or_partial_experience_stays_unresolved_and_clears_old_value(value, reason):
    start = apply_proposals(
        missing_fields(), [proposal("delivery_experience", experience(1))]
    ).fields

    result = apply_proposals(start, [proposal("delivery_experience", value, raw="what they said")])

    field = result.fields.delivery_experience
    assert result.errors == []
    assert (field.status, field.normalized, field.raw, field.reason) == (
        "unresolved",
        None,
        "what they said",
        reason,
    )


def test_snapshot_with_negative_years_is_invalid():
    data = json.loads(new_snapshot("app-1", NOW).model_dump_json())
    data["fields"]["delivery_experience"] = {
        "raw": "x",
        "normalized": {"years": -1, "platforms": []},
        "status": "resolved",
        "reason": None,
        "clarifications_asked": 0,
    }

    with pytest.raises(ValidationError):
        CandidateSnapshot.model_validate_json(json.dumps(data))


def test_availability_keeps_options_and_notes():
    value = {"options": ["part_time", "weekends"], "notes": "solo hasta junio"}

    result = apply_proposals(missing_fields(), [proposal("availability", value)])

    field = result.fields.availability
    assert result.errors == []
    assert field.normalized.options == ["part_time", "weekends"]
    assert field.normalized.notes == "solo hasta junio"


@pytest.mark.parametrize("field_name", ["availability", "preferred_schedule"])
def test_empty_options_resolve_when_notes_keep_an_explicit_restriction(field_name):
    value = {"options": [], "notes": "solo martes por la noche"}

    result = apply_proposals(missing_fields(), [proposal(field_name, value)])

    data = json.loads(result.fields.model_dump_json())
    assert result.errors == []
    assert data[field_name]["status"] == "resolved"
    assert data[field_name]["normalized"] == value


@pytest.mark.parametrize("field_name", ["availability", "preferred_schedule"])
@pytest.mark.parametrize(
    "value",
    [
        {"options": [], "notes": None},
        {"options": [], "notes": "   "},
        {"options": ["sometimes"], "notes": None},
        {"options": "morning", "notes": None},
        {"options": ["morning", "full_time"], "notes": None},
        {"options": ["flexible"]},
        ["flexible"],
        None,
    ],
)
def test_invalid_options_value_is_reported(field_name, value):
    result = apply_proposals(missing_fields(), [proposal(field_name, value)])

    assert [error.field for error in result.errors] == [field_name]
    assert getattr(result.fields, field_name).status == "missing"


def test_schedule_accepts_its_own_categories():
    value = {"options": ["morning", "afternoon", "evening", "flexible"], "notes": None}

    result = apply_proposals(missing_fields(), [proposal("preferred_schedule", value)])

    assert result.errors == []
    assert result.fields.preferred_schedule.normalized.options == value["options"]


def test_work_locations_keep_identified_and_unresolved_options():
    value = {
        "identified": [{"country": "ES", "locality": "madrid"}, {"country": "FR", "locality": "Lyon"}],
        "unresolved": ["cerca del centro"],
    }

    result = apply_proposals(missing_fields(), [proposal("work_locations", value)])

    data = json.loads(result.fields.model_dump_json())
    assert result.errors == []
    assert data["work_locations"]["normalized"] == value


@pytest.mark.parametrize(
    "value",
    [
        {"identified": [], "unresolved": []},
        {"identified": [{"country": "ES"}], "unresolved": []},
        {"identified": [{"country": " ", "locality": "madrid"}], "unresolved": []},
        {"identified": [{"country": "ES", "locality": ""}], "unresolved": []},
        {"identified": [], "unresolved": [""]},
        {"identified": "madrid", "unresolved": []},
        {"identified": []},
        "madrid",
        None,
    ],
)
def test_invalid_work_locations_value_is_reported(value):
    result = apply_proposals(missing_fields(), [proposal("work_locations", value)])

    assert [error.field for error in result.errors] == ["work_locations"]
    assert result.fields.work_locations.status == "missing"


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "date", "value": "2026-12-01"},
        {"kind": "period", "value": "en primavera"},
        {"kind": "condition", "value": "cuando termine el contrato actual"},
        {"kind": "unknown", "value": None},
    ],
)
def test_start_timing_kinds_resolve(value):
    result = apply_proposals(missing_fields(), [proposal("start_timing", value)])

    data = json.loads(result.fields.model_dump_json())
    assert result.errors == []
    assert data["start_timing"]["status"] == "resolved"
    assert data["start_timing"]["normalized"] == value


def test_explicit_unknown_start_is_resolved_immediately_without_a_clarification():
    result = apply_proposals(
        missing_fields(),
        [proposal("start_timing", {"kind": "unknown", "value": None}, raw="no puedo concretar")],
    )

    field = result.fields.start_timing
    assert field.status == "resolved"
    assert field.clarifications_asked == 0
    assert field.raw == "no puedo concretar"


def test_still_imprecise_start_after_its_clarification_resolves_and_keeps_raw():
    first = apply_proposals(
        missing_fields(),
        [proposal("start_timing", None, status="unresolved", raw="pronto", reason="start_imprecise")],
    ).fields
    assert first.start_timing.status == "unresolved"
    first.start_timing.clarifications_asked = 1

    second = apply_proposals(
        first,
        [
            proposal(
                "start_timing",
                {"kind": "period", "value": "pronto"},
                raw="pues pronto, no sé más",
                reason="start_imprecise_after_clarification",
            )
        ],
    )

    field = second.fields.start_timing
    assert second.errors == []
    assert (field.status, field.raw, field.reason, field.clarifications_asked) == (
        "resolved",
        "pues pronto, no sé más",
        "start_imprecise_after_clarification",
        1,
    )
    assert field.normalized.kind == "period"


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "date", "value": "pronto"},
        {"kind": "date", "value": "2026-13-01"},
        {"kind": "date", "value": "20261201"},
        {"kind": "date", "value": None},
        {"kind": "period", "value": None},
        {"kind": "period", "value": " "},
        {"kind": "condition", "value": None},
        {"kind": "unknown", "value": "unknown"},
        {"kind": "soon", "value": "pronto"},
        {"kind": "unknown"},
        "unknown",
        None,
    ],
)
def test_invalid_start_timing_value_is_reported(value):
    result = apply_proposals(missing_fields(), [proposal("start_timing", value)])

    assert [error.field for error in result.errors] == ["start_timing"]
    assert result.fields.start_timing.status == "missing"


def test_full_snapshot_round_trip_keeps_resolved_values_and_all_keys():
    fields = apply_proposals(
        missing_fields(),
        [
            proposal("full_name", "Ana García"),
            proposal("driving_license", False),
            proposal("work_locations", {"identified": [], "unresolved": ["por el norte"]}),
            proposal("availability", {"options": ["weekends"], "notes": None}),
            proposal("preferred_schedule", {"options": [], "notes": "solo noches de martes"}),
            proposal("delivery_experience", experience(0, platforms=[])),
            proposal("start_timing", {"kind": "unknown", "value": None}),
        ],
    ).fields
    snapshot = new_snapshot("app-1", NOW).model_copy(update={"fields": fields})

    text = snapshot.model_dump_json()

    assert CandidateSnapshot.model_validate_json(text) == snapshot
    stored = json.loads(text)["fields"]
    assert stored["driving_license"]["normalized"] is False
    assert stored["availability"]["normalized"]["notes"] is None
    assert stored["delivery_experience"]["normalized"] == {"years": 0, "platforms": []}
    assert stored["start_timing"]["normalized"] == {"kind": "unknown", "value": None}


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", 3),
        ("full_name", "   "),
        ("driving_license", 1),
        ("driving_license", 0),
        ("driving_license", "true"),
    ],
)
def test_strict_mode_rejects_coercible_values(field_name, value):
    result = apply_proposals(missing_fields(), [proposal(field_name, value)])

    assert [error.field for error in result.errors] == [field_name]
    assert getattr(result.fields, field_name).status == "missing"
