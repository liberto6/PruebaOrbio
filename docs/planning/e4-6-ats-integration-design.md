# E4.6 — ATS integration design

**Status: planned, 2026-11-05. Design work pending.** Implements the assignment bonus
scope: “API spec for connecting to an ATS (design only, no need to build)”. Sources:
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

- [ ] Both artifacts exist and clearly distinguish proposed behaviour from the demo.
- [ ] The OpenAPI document parses and passes one available OpenAPI validator; record
  the tool/version and result. Validate references and example/schema consistency.
  Do not add application dependencies or build a validation framework.
- [ ] Walk through one synthetic application: retrieval, ATS upsert, decision update
  and subsequent read. Include one failed-page retry in that same example to explain
  checkpoint recovery; no extensive failure matrix.
- [ ] Manually check the four outcome values, three decision values, seven field
  shapes, null handling and incomplete summary against the current code/contracts.
- [ ] Record concise evidence and limits here; link the artifacts from the README
  and update roadmap/AGENTS.md. No pytest rerun, frontend build, real ATS call or LLM
  evaluation is required for documentation-only delivery.

Stop when this contract and worked flow are coherent and validated. No claim of
compatibility with a real ATS until a connector is implemented and tested. E5 can
present this as a design bonus; no extra demo application or video is required.

Planning evidence: inspected the assignment, current recruiter operations and stored
screening contracts. Only this brief and planning links are delivered now; the API
specification and integration explanation remain to be written.
