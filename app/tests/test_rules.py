import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.rules import (
    COVERAGE_CATALOG,
    CandidateSnapshot,
    CatalogError,
    apply_proposals,
    evaluate,
    evaluate_coverage,
    new_snapshot,
    record_clarifications,
)

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
        "language",
        "last_question_field",
        "last_question_kind",
        "reminder_sent",
        "location_alternatives_offered",
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
        proposal("full_name", "Ana", raw="   "),
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
    ["not a proposal", proposal("salary", 1)],
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
        {"raw": None, "normalized": True, "status": "resolved", "reason": None, "clarifications_asked": 0},
        {"raw": "x", "normalized": True, "status": "resolved", "reason": None, "clarifications_asked": 3},
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
        (7, "months", 7 / 12),
        (0.5, "years", 0.5),
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


@pytest.mark.parametrize(
    "value",
    [
        experience(True),
        experience(False, platforms=[]),
        experience("3"),
        experience(-1),
        experience(float("nan")),
        experience(float("inf")),
        experience(10**400),
        experience(1, unit="weeks"),
        experience(1, platforms=[""]),
        {"years": 1, "platforms": ["Glovo"]},
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
        {"options": ["morning", "full_time"], "notes": None},
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
        {"identified": [], "unresolved": [""]},
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
        {"kind": "unknown", "value": "unknown"},
        {"kind": "soon", "value": "pronto"},
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
        ("full_name", "   "),
        ("driving_license", 1),
    ],
)
def test_strict_mode_rejects_coercible_values(field_name, value):
    result = apply_proposals(missing_fields(), [proposal(field_name, value)])

    assert [error.field for error in result.errors] == [field_name]
    assert getattr(result.fields, field_name).status == "missing"


def test_resolved_proposal_without_raw_leaves_the_previous_value_unchanged():
    start = apply_proposals(
        missing_fields(), [proposal("driving_license", True, raw="sí")]
    ).fields

    result = apply_proposals(start, [proposal("driving_license", False, raw=None)])

    assert result.fields == start
    assert [error.field for error in result.errors] == ["driving_license"]


def place(country, locality):
    return {"country": country, "locality": locality}


def work_locations_state(identified=(), unresolved=()):
    value = {"identified": list(identified), "unresolved": list(unresolved)}
    result = apply_proposals(missing_fields(), [proposal("work_locations", value)])
    assert result.errors == []
    return result.fields.work_locations


def test_catalog_holds_exactly_the_demo_places_with_country():
    assert COVERAGE_CATALOG == {
        ("ES", "madrid"),
        ("ES", "barcelona"),
        ("ES", "valencia"),
        ("ES", "sevilla"),
        ("MX", "ciudad_de_mexico"),
        ("MX", "guadalajara"),
        ("MX", "monterrey"),
        ("MX", "puebla"),
    }


@pytest.mark.parametrize(
    ("identified", "unresolved"),
    [
        ([place("ES", "madrid")], []),
        ([place("FR", "lyon"), place("ES", "sevilla")], []),
        ([place("ES", "madrid")], ["por el norte"]),
    ],
)
def test_any_identified_catalog_place_is_covered(identified, unresolved):
    assert evaluate_coverage(work_locations_state(identified, unresolved)) == "covered"


@pytest.mark.parametrize(
    "identified",
    [
        [place("FR", "lyon")],
        [place("FR", "lyon"), place("ES", "toledo")],
        [place("ES", "guadalajara")],
        [place("VE", "valencia")],
        [place("MX", "madrid")],
        [place("ES", "Madrid")],
    ],
)
def test_every_identified_place_outside_the_catalog_is_not_covered(identified):
    assert evaluate_coverage(work_locations_state(identified)) == "not_covered"


@pytest.mark.parametrize(
    ("identified", "unresolved"),
    [
        ([place("FR", "lyon")], ["por el norte"]),
        ([], ["México"]),
        ([], ["cerca de Madrid", "por el centro"]),
    ],
)
def test_any_unidentified_option_without_a_covered_one_is_unresolved(identified, unresolved):
    assert evaluate_coverage(work_locations_state(identified, unresolved)) == "unresolved"


def test_residence_without_work_preference_is_unresolved_coverage():
    result = apply_proposals(
        missing_fields(),
        [
            proposal(
                "work_locations",
                None,
                status="unresolved",
                raw="vivo en Toledo",
                reason="residence_without_work_preference",
            )
        ],
    )

    assert evaluate_coverage(result.fields.work_locations) == "unresolved"


def resolved_proposals():
    return {
        "full_name": proposal("full_name", "Ana García"),
        "driving_license": proposal("driving_license", True),
        "work_locations": proposal(
            "work_locations", {"identified": [place("ES", "madrid")], "unresolved": []}
        ),
        "availability": proposal("availability", {"options": ["full_time"], "notes": None}),
        "preferred_schedule": proposal("preferred_schedule", {"options": ["morning"], "notes": None}),
        "delivery_experience": proposal("delivery_experience", experience(2)),
        "start_timing": proposal("start_timing", {"kind": "date", "value": "2026-12-01"}),
    }


def fields_from(*proposals):
    result = apply_proposals(missing_fields(), list(proposals))
    assert result.errors == []
    return result.fields


def all_resolved_except(*replacements):
    proposals = resolved_proposals()
    for replacement in replacements:
        proposals[replacement["field"]] = replacement
    return fields_from(*proposals.values())


def unresolved(field):
    return proposal(field, None, status="unresolved", raw="no sé", reason="ambiguous")


LICENSE_FALSE = proposal("driving_license", False)
OUTSIDE_CATALOG = proposal(
    "work_locations", {"identified": [place("FR", "lyon")], "unresolved": []}
)


def test_license_false_disqualifies_early_with_null_fields():
    fields = fields_from(LICENSE_FALSE)

    result = evaluate(fields)

    assert result.outcome == "disqualified"
    assert result.outcome_reasons == ["no_driving_license"]
    assert result.closeable is True
    data = json.loads(fields.model_dump_json())
    assert set(data) == FIELD_NAMES
    assert data["full_name"]["normalized"] is None


def test_not_covered_disqualifies_early_with_null_fields():
    result = evaluate(fields_from(OUTSIDE_CATALOG))

    assert result.outcome == "disqualified"
    assert result.outcome_reasons == ["location_not_covered"]
    assert result.closeable is True


def test_both_confirmed_failures_are_reported_together():
    result = evaluate(fields_from(LICENSE_FALSE, OUTSIDE_CATALOG))

    assert result.outcome_reasons == ["no_driving_license", "location_not_covered"]


@pytest.mark.parametrize(
    ("fields", "reasons"),
    [
        (all_resolved_except(OUTSIDE_CATALOG), ["location_not_covered"]),
        (fields_from(LICENSE_FALSE, unresolved("full_name")), ["no_driving_license"]),
        (fields_from(OUTSIDE_CATALOG, unresolved("driving_license")), ["location_not_covered"]),
    ],
)
def test_confirmed_failure_takes_priority_over_other_fields(fields, reasons):
    result = evaluate(fields)

    assert result.outcome == "disqualified"
    assert result.outcome_reasons == reasons
    assert result.closeable is True


@pytest.mark.parametrize("catalog", [None, frozenset()])
@pytest.mark.parametrize("given", [LICENSE_FALSE, OUTSIDE_CATALOG])
def test_missing_catalog_fails_evaluation_instead_of_rejecting(catalog, given):
    with pytest.raises(CatalogError):
        evaluate(fields_from(given), catalog)


@pytest.mark.parametrize("field", sorted(FIELD_NAMES))
def test_one_missing_field_keeps_the_application_open(field):
    proposals = resolved_proposals()
    del proposals[field]

    result = evaluate(fields_from(*proposals.values()))

    assert result.outcome == "incomplete"
    assert result.outcome_reasons == [f"{field}_missing"]
    assert result.closeable is False


def test_pending_review_is_blocked_while_any_field_is_missing():
    result = evaluate(fields_from(unresolved("driving_license"), unresolved("work_locations")))

    assert result.outcome == "incomplete"
    assert result.closeable is False
    assert "driving_license_missing" not in result.outcome_reasons
    assert "full_name_missing" in result.outcome_reasons


@pytest.mark.parametrize(
    "field",
    [
        "full_name",
        "driving_license",
        "work_locations",
        "availability",
        "preferred_schedule",
        "delivery_experience",
        "start_timing",
    ],
)
def test_one_unresolved_field_goes_to_pending_review(field):
    result = evaluate(all_resolved_except(unresolved(field)))

    assert result.outcome == "pending_review"
    assert result.outcome_reasons == [f"{field}_unresolved"]
    assert result.closeable is True


def test_every_unresolved_field_is_listed_and_ambiguity_never_rejects():
    names = [name for name in resolved_proposals() if name != "start_timing"]
    fields = all_resolved_except(*[unresolved(name) for name in names])

    result = evaluate(fields)

    assert result.outcome == "pending_review"
    assert result.outcome_reasons == [
        "full_name_unresolved",
        "driving_license_unresolved",
        "work_locations_unresolved",
        "availability_unresolved",
        "preferred_schedule_unresolved",
        "delivery_experience_unresolved",
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"identified": [], "unresolved": ["por el norte"]},
        {"identified": [place("FR", "lyon")], "unresolved": ["por el norte"]},
    ],
)
def test_resolved_locations_without_confirmed_coverage_neither_qualify_nor_disqualify(value):
    fields = all_resolved_except(proposal("work_locations", value))

    result = evaluate(fields)

    assert fields.work_locations.status == "resolved"
    assert result.outcome == "pending_review"
    assert result.outcome_reasons == ["coverage_unresolved"]
    assert result.closeable is True


def test_all_fields_resolved_with_license_and_coverage_qualifies():
    result = evaluate(all_resolved_except())

    assert result.outcome == "qualified"
    assert result.outcome_reasons == ["screening_passed"]
    assert result.closeable is True


@pytest.mark.parametrize(
    "replacement",
    [
        proposal("delivery_experience", experience(0, platforms=[])),
        proposal("start_timing", {"kind": "unknown", "value": None}),
        proposal("start_timing", {"kind": "period", "value": "pronto"}),
        proposal("availability", {"options": [], "notes": "solo martes"}),
        proposal(
            "work_locations",
            {"identified": [place("ES", "madrid"), place("FR", "lyon")], "unresolved": ["por el norte"]},
        ),
    ],
)
def test_admitted_answers_do_not_prevent_qualification(replacement):
    assert evaluate(all_resolved_except(replacement)).outcome == "qualified"


def test_evaluation_does_not_change_the_fields():
    fields = all_resolved_except()
    before = fields.model_dump_json()

    evaluate(fields)

    assert fields.model_dump_json() == before


def test_recording_a_clarification_counts_only_that_field():
    fields = missing_fields()

    result = record_clarifications(fields, ["full_name"])

    assert result.errors == []
    assert result.fields.full_name.clarifications_asked == 1
    assert result.fields.availability.clarifications_asked == 0
    assert fields.full_name.clarifications_asked == 0


def test_clarification_beyond_the_limit_is_refused_without_changing_state():
    at_limit = record_clarifications(missing_fields(), ["full_name", "full_name"])
    assert at_limit.errors == []
    assert at_limit.fields.full_name.clarifications_asked == 2

    result = record_clarifications(at_limit.fields, ["full_name", "availability"])

    assert [(error.field, error.error) for error in result.errors] == [
        ("full_name", "clarification_limit_reached")
    ]
    assert result.fields.full_name == at_limit.fields.full_name
    assert result.fields.availability.clarifications_asked == 1


def test_malformed_clarification_entries_are_reported_without_a_field():
    start = missing_fields()

    result = record_clarifications(start, [3, ["x"]])

    assert [(error.field, error.error) for error in result.errors] == [
        (None, "unknown_field"),
        (None, "unknown_field"),
    ]
    assert result.fields == start


def test_ambiguous_or_invalid_answers_alone_never_use_the_budget():
    result = apply_proposals(
        missing_fields(),
        [
            unresolved("driving_license"),
            unresolved("driving_license"),
            proposal("full_name", 3),
        ],
    )

    assert result.fields.driving_license.clarifications_asked == 0
    assert result.fields.full_name.clarifications_asked == 0


def at_budget_limit(field_proposal):
    field = field_proposal["field"]
    fields = apply_proposals(all_resolved_except(), [field_proposal]).fields
    return record_clarifications(fields, [field, field]).fields


def test_clear_correction_at_the_limit_resolves_and_keeps_the_count():
    fields = at_budget_limit(unresolved("driving_license"))

    result = apply_proposals(fields, [proposal("driving_license", True, raw="sí, lo tengo")])

    field = result.fields.driving_license
    assert (field.status, field.normalized, field.reason) == ("resolved", True, None)
    assert field.clarifications_asked == 2
    assert evaluate(result.fields).outcome == "qualified"


def test_ambiguous_correction_at_the_limit_clears_a_stale_rejection_for_review():
    fields = at_budget_limit(LICENSE_FALSE)
    assert evaluate(fields).outcome == "disqualified"

    result = apply_proposals(fields, [unresolved("driving_license")])

    field = result.fields.driving_license
    assert (field.status, field.normalized, field.raw) == ("unresolved", None, "no sé")
    assert field.clarifications_asked == 2
    assert evaluate(result.fields).outcome == "pending_review"
    assert record_clarifications(result.fields, ["driving_license"]).errors


def test_explicit_inability_to_answer_stays_closeable_as_pending_review():
    result = evaluate(
        all_resolved_except(
            proposal(
                "preferred_schedule",
                None,
                status="unresolved",
                raw="no lo puedo decir aún",
                reason="candidate_cannot_answer",
            )
        )
    )

    assert result.outcome == "pending_review"
    assert result.outcome_reasons == ["preferred_schedule_unresolved"]
    assert result.closeable is True
