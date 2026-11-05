# E4.6 — ATS integration design

**Status: delivered, 2026-11-05. Design only, nothing implemented.** Artifacts:
[`ats-api.yaml`](../design/ats-api.yaml) and [`ats-integration.md`](../design/ats-integration.md).
See the [evidence](#evidence-2026-11-05). Implements the assignment bonus scope: “API spec for connecting to an ATS (design only, no need to build)”. Sources:
[assignment](../assignment.html), [process design](../design/process-design.md),
[roadmap](build-roadmap.md) and [E4 contracts](e4-recruiter-tool.md).
This is an independent documentation task; E4.5 is closed separately, with known limits.

## Goal and deliverables

Explain how an external ATS could retrieve screening applications and send back a
recruiter decision, without replacing the candidate agent or its existing local UI.
Deliver exactly two design artifacts:

- `docs/design/ats-api.yaml`: a proposed OpenAPI contract with schemas, parameters,
  responses, errors, security declaration and synthetic examples.
- `docs/design/ats-integration.md`: a short explanation of the flow, responsibilities,
  synchronization rules and limits. Include one sequence diagram and one worked
  example; keep it suitable for a 45–60 second explanation in the delivery video.

Mark both as **proposed, not implemented**. They are not the application's generated
OpenAPI documentation, and their example requests are not runnable against the demo.
No new endpoints, connector, database changes, frontend, SDK, mock server, paid calls,
provider account or runtime dependency. Do not choose a particular ATS vendor.

## Flow to design

1. The candidate completes or progresses through screening in Grupo Sazón.
2. An external connector periodically requests new/updated applications and retrieves
   their detail. The connector, not the API itself, performs synchronization.
3. It creates or updates the ATS record using the stable Grupo Sazón application id.
4. A recruiter works in the ATS and chooses a decision.
5. The connector writes that decision back; the local recruiter UI can then read it.

The connector owns ATS-specific field/status mapping and the correspondence between
ids. One Grupo Sazón id identifies an application, not a verified person: never merge
people by name. A second application remains a separate record. No candidate creation
from the ATS, vacancy ingestion, identity matching or automatic hiring decision.

## Contract scope: three proposed operations

Use a separate proposed namespace, `/api/ats/v1`, to distinguish the design from the
existing unauthenticated recruiter routes. Reuse domain meanings, not their current
HTTP transport contract, which lacks synchronization and authentication.

| Operation | Proposed endpoint | Purpose |
|---|---|---|
| List changes | `GET /api/ats/v1/applications` | Paginated applications created/updated since an optional timestamp; no timestamp starts the initial import |
| Read detail | `GET /api/ats/v1/applications/{id}` | Saved screening data, result and recruiter decision |
| Set decision | `PATCH /api/ats/v1/applications/{id}/decision` | Set `unreviewed`, `advance` or `rejected`, independently of the screening result |

List items need only id, modification timestamp, screening outcome and recruiter
decision. Detail includes id/timestamps, the seven fields with their existing status,
raw evidence and normalized values, outcome/reasons, pending job questions, saved
summary and recruiter decision. Preserve all four outcomes and distinguish missing
data from false, zero or explicit unknown start timing. Incomplete records have no
summary; even greeting-only applications may appear in the initial import.

Exclude full transcripts, hidden reasoning, turn/retry metadata and technical
diagnostics from this integration. The full conversation remains in the local tool.
Do not invent contact details, job identifiers or ATS fields the application does
not collect. Explain that a real vendor may require additional mapping/data.

Specify the following once, concisely, in the contract and accompanying explanation:

- A bounded page size, deterministic ordering, opaque continuation cursor and the
  meaning of the timestamp checkpoint. Explain boundary timestamps, changes during
  pagination, when to advance the checkpoint and how restarting a failed sync avoids
  missed records. Duplicate delivery is acceptable when the connector upserts by id;
  do not claim exactly-once delivery or that timestamps alone are sufficient.
- Latest application state, not an event history. `updated_at` includes recruiter
  decisions. Describe how the connector avoids echoing an unchanged decision back
  indefinitely. Same-decision retries have the same business effect, with no claim
  of an immutable timestamp or an exactly-once write.
- All three decisions may replace one another on open or closed applications.
  Preserve screening data and result. Concurrent decisions use last committed write,
  matching the existing simple workflow; document that limit without adding an
  optimistic-locking protocol or audit history.
- Proposed HTTPS and a server-to-server bearer credential, with read/write permission
  requirements and 401/403 responses. Token provisioning is outside this design.
  This does not secure or change the current local application.
- Invalid input, unknown id, invalid/expired cursor and temporary failure responses;
  a consistent error body without internal exception details. State which errors
  permit retry and how decision writes are reconciled after a lost response.

No webhooks, queues, event log, OAuth flow, multi-tenant platform, bulk writes, deletion
sync or rate-limit infrastructure. Retention/deletion synchronization and production
security implementation remain explicit limits, not additional work in this bonus.

## Bounded verification and closure

This is design verification, not backend TDD or integration testing:

- [x] Both artifacts exist and clearly distinguish proposed behaviour from the demo.
- [x] The OpenAPI document parses and passes one available OpenAPI validator; record
  the tool/version and result. Validate references and example/schema consistency.
  Do not add application dependencies or build a validation framework.
- [x] Walk through one synthetic application: retrieval, ATS upsert, decision update
  and subsequent read. Include one failed-page retry in that same example to explain
  checkpoint recovery; no extensive failure matrix.
- [x] Manually check the four outcome values, three decision values, seven field
  shapes, null handling and incomplete summary against the current code/contracts.
- [x] Record concise evidence and limits here; link the artifacts from the README
  and update roadmap/AGENTS.md. No pytest rerun, frontend build, real ATS call or LLM
  evaluation is required for documentation-only delivery.

Stop when this contract and worked flow are coherent and validated. No claim of
compatibility with a real ATS until a connector is implemented and tested. E5 can
present this as a design bonus; no extra demo application or video is required.

## Evidence, 2026-11-05

**Language.** Both artifacts are in English, like the code, README and planning documents:
an API contract is a technical artifact, not product copy or the Phase 1 process design.
Synthetic candidate answers in the examples stay in Spanish, as the product would save them.

**Validator.** `@stoplight/spectral-cli` 6.17.0 with `spectral:oas`, installed outside the
repository: 0 errors, 4 warnings. One is the absent `info.contact`, not invented. Three are
`oas3-operation-security-defined`, about the permission names on the bearer scheme, which
OpenAPI 3.1 allows as role names. Nine deliberately broken copies were all caught: eight
invalid examples through `oas3-valid-media-example` and one broken reference through `invalid-ref`.

**Examples against the models.** A throwaway script, run from `app/`, loaded the four detail
examples through `CandidateFields` and `CandidateSnapshot`, all valid. It adds
`clarifications_asked`, which the contract leaves out and the model requires. For the closed
examples, `evaluate` returned the stated outcome and reasons. Enums and field order were compared
with `rules.py` and `main.py`. The diagram parses with Mermaid 11.17.2. The read-aloud
section has 120 words.

**Walkthrough.** The worked example was followed by hand against the contract: pagination,
tie by id, failed page, unmoved checkpoint, re-delivery, decision, boundary re-delivery, no echo.

**Checked against the code.** Reading the code, and a throwaway run with a temporary database
and no model calls for serialization and repeated decisions:

- `set_recruiter_decision` moves `updated_at` on every save, even with the same value, and keeps
  `revision`. The three decisions replace one another on open and closed applications.
- Opening the chat creates a greeting-only record: all fields missing, `incomplete`, no reasons.
- The saved outcome changes only at closure. So `incomplete`, open, null summary and empty
  reasons always go together, and `<field>_missing` from `evaluate` is never saved.
- `reason` is an open string, partly written by the model. `alternatives_offered` is one possible
  value and can remain on a closed `pending_review`; the contract does not enumerate it.
- Left out as turn or retry metadata: `clarifications_asked`, `revision`, `stop_requested`,
  `language`, `reminder_sent`, `last_question_field`, `last_question_kind`,
  `location_alternatives_offered` and the messages.

**Proposal beyond the code.** The brief's pagination needs behaviour the demo lacks. The current
code stays unchanged, and the requirements are listed in the
[explanation](../design/ats-integration.md#what-a-future-implementation-must-add): order by instant
then id, since the stored text is not time-ordered; `updated_at` never decreasing in commit order,
since `create_application` takes it before its transaction; a fixed timestamp format; an
allowlist; and authentication.

**Limits.** No connector, mock server or real ATS call, so nothing proves that the design works
in operation or suits a given vendor. Spectral checks structure and examples, not the prose
rules. No pytest rerun or frontend build: no application file changed. Next stage: E5.
