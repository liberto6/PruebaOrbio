# E2 — Rules and persistence

**Status: Planned (2026-11-04).** This document proposes E2; no implementation or
verification is claimed. Read the [assignment](../assignment.html),
[process design](../design/process-design.md), [roadmap](build-roadmap.md) and
[E1 evidence](e1-foundations.md). Local inspection found only the FastAPI/static
shell and two HTTP tests. Existing uncommitted documentation changes are retained.

## Scope and source reconciliation

Deliver deterministic Python validation, coverage, clarification accounting,
outcomes and durable SQLite state without an LLM. Keep modules flat; add a rules
module and a storage module as needed, alongside the existing application. No
conversation parser, regex interpretation, translation, model selection, chat UI,
recruiter UI or orchestration is part of this stage.

One roadmap acceptance statement needs qualification: “Tests cover every Phase 1
rule” includes conversation behavior explicitly assigned to E3. E2 covers the
deterministic contracts below; E3 must demonstrate extraction, wording, ordering,
language changes, reminders, summaries and browser resume through conversation
scenarios. Do not claim full Phase 1 coverage at E2 closure. E1 also assigns
history/resume to E3: E2 supplies storage and retrieval; E3 connects them to the
session and conversation. These are boundary clarifications, not removed features.

Confirmed rejection takes priority over unresolved fields. Explicitly unknown
start timing is acceptable. Job questions do not affect eligibility. These are
explicit process requirements, not implementation assumptions.

## One candidate snapshot and interpreted input

Use strict-mode Pydantic models as the one canonical validated representation and
JSON serialization, not parallel domain, database and LLM schemas. Pin `pydantic`
directly in `requirements.txt` when implementing. The snapshot contains `id`, timestamps,
`closed_at`, `outcome`, `outcome_reasons` (a list of stable codes), `fields`,
`pending_questions`, `summary` and
`recruiter_decision`, `revision` and `stop_requested` (initially false). All seven
field keys always exist:

| Key | Normalized value when resolved |
|---|---|
| `full_name` | Nonblank string, accepted as a complete name by interpretation |
| `driving_license` | Boolean for whether the candidate currently holds a license |
| `work_locations` | `{identified: [{country, locality}], unresolved: [raw text, ...]}`; country uses `ES`/`MX` or another explicitly identified country; locality is an explicit canonical place identifier/name |
| `availability` | `{options: [...], notes: string or null}`; options drawn from `full_time`, `part_time`, `weekends` |
| `preferred_schedule` | `{options: [...], notes: string or null}`; options drawn from `morning`, `afternoon`, `evening`, `flexible` |
| `delivery_experience` | `{years: nonnegative number, platforms: [...]}`; companies/local businesses accepted |
| `start_timing` | `{kind: date/period/condition/unknown, value: string or null}`; date is ISO date; `unknown` has null value |

Each field is `{raw, normalized, status, reason, clarifications_asked}`.
`raw` is the supporting candidate text supplied with the current value or null; `status` is `missing`,
`resolved` or `unresolved`; `reason` is a short stable code or null.
`missing` means no evidence and has both values null. `unresolved` retains raw
evidence and a reason but has normalized null, so stale values cannot drive a
decision. `resolved` has a validated normalized value. No answer is represented
by null, never by false, zero, an empty list or the string “unknown”. Unknown
start timing is instead a resolved object. Full historical evidence remains in
messages; `raw` is not a replacement for history.

The caller supplies **already interpreted** field proposals with one shape:
`{field, status: resolved|unresolved, value, raw, reason}`. `value` uses the
normalized form in the table above, except experience, which is
`{amount, unit: years|months, platforms}`. A resolved proposal has a value; an
unresolved one has a null value and a reason. `raw` replaces the previous raw text.
Code validates types, finite nonnegative numbers, allowed categories and shape; it
normalizes numeric months to years. Reject bool as a numeric duration.
Contradictory combinations such as
zero experience with asserted prior employers require clarification, not invented
facts. No experience resolves to `years: 0, platforms: []`; positive duration
with missing employer information remains unresolved. Numeric rounding must not
silently change the source duration.

Interpretation of names, natural dates, synonyms, conditions, restrictions and
place identity is E3. E2 accepts an explicit unresolved proposal with reason when
interpretation cannot establish a value. A nonblank name alone does not establish
completeness: `status: resolved` is the interpreter's assertion that the name is
complete; otherwise it submits the name as unresolved. Availability/schedule may have empty options if meaningful explicit
restrictions are retained in notes; empty options with no information are not
resolved. No additional eligibility thresholds or schedule matching are added.
An imprecise start answer needs one clarification; a clear explicit inability to
specify timing is accepted immediately. After that one clarification, a still
imprecise answer resolves as `period`, `condition` or `unknown` with its raw text
retained, so an imprecise start answer does not cause review once clarified. If
start timing is ever left unresolved at closure, it follows the general rule and
goes to `pending_review`. The caller
marks these semantic cases; code cannot establish them by scanning text. An invalid
proposal leaves that field unchanged and is returned in a per-field `errors` result;
the rest of the turn still commits so E3 can re-ask. A valid explicit unresolved correction
replaces the old value. E3 must submit the latter when the candidate retracts or
contradicts an earlier answer without supplying a clear replacement.

## Coverage, clarification and outcomes

Store the small fictional catalog explicitly in code/configuration with country:
`ES`: Madrid, Barcelona, Valencia, Sevilla; `MX`: Ciudad de México, Guadalajara,
Monterrey, Puebla. Use stable catalog identifiers for covered places. The caller
must identify other places explicitly too: absence from the catalog is not proof
that an ambiguous mention has been identified. No fuzzy matching, proximity,
translation or external geocoding. Country alone, unresolved locality or residence
without work preference is insufficient evidence. E3 may resolve aliases into
these identifiers; unsupported aliases remain unresolved rather than rejected.

Coverage is `covered` if any identified requested option matches; `not_covered`
only if every requested option is identified and none matches; otherwise
`unresolved`. Preserve unresolved alternatives with their raw evidence even when
one covered option suffices, using the location value shown in the table.
Thus a covered option resolves the field's coverage requirement without pretending
all alternatives are identified. Empty/missing catalog is a configuration error,
never candidate rejection; detect it before processing candidates.

Clarifications are counted **when a clarification question is durably recorded**,
not on each ambiguous input or validation failure. Initial questions do not count.
Permit at most two clarification questions per field over the open application;
never reset the count on correction or resume. At the limit, retain unresolved
evidence for human review and continue. Two ambiguous replies are not themselves
evidence that two questions were asked. Start-timing guidance above still applies.
E2 enforces the budget and persists its consumption; E3 chooses reformulation,
examples, timing and the next question. Corrections are accepted while open even
at the limit: a clear correction resolves the field; a newly ambiguous correction
clears its old normalized value and may require review if no budget remains.

Keep outcome separate from recruiter decision and closure:

1. An explicit current license `false`, or `not_covered`, yields
   `disqualified` regardless of missing/uncertain fields; allow early closure.
2. With license `true`, covered work location and all seven fields resolved,
   yield `qualified`.
3. After collecting the other possible fields, unresolved required evidence
   yields `pending_review`; ambiguity never yields rejection.
4. Before a valid close, state is `incomplete`, including silence or a request
   to stop. No summary is attached to incomplete state.

Use a pure evaluation result that can distinguish “continue” from a closeable
outcome; do not persist tentative qualification as a closed result. To close as
pending review, no field may be `missing`; `unresolved` fields are allowed. Two
clarifications per field are a ceiling, not a quota, and code enforces no minimum;
an explicitly unknown start date already counts as resolved. This gate implements
“collect the other possible fields”, not a new screening criterion. E3 must collect
each field and follow the Phase 1 recovery (rephrase, then examples or options)
before submitting it as unresolved, demonstrated by its evals; early candidate
termination remains incomplete. Re-evaluate
from current fields inside the close transaction. Questions about the job are
stored separately, included in review material and never counted as unresolved
screening fields.

## Minimal persistence contract

Use standard-library `sqlite3` with parameterized SQL and JSON serialization.
Two tables suffice: `applications` for id, timestamps, closure, outcome, recruiter
decision and the canonical snapshot JSON; `messages` for application id, sequence,
role, exact text and timestamp. Keep filterable columns consistent with JSON in
the same transaction; no separate seven-field tables, ORM, migration framework or
event sourcing. This small duplication serves E4 filters, not a second schema.
Use a foreign key, ordered per-application sequence and a uniqueness constraint
on that sequence. Initialize a new ignored local database; schema upgrade policy
can be decided if an actual upgrade becomes necessary.

Expose Python operations, not speculative HTTP endpoints:

- Create an incomplete application with seven missing fields and `unreviewed`
  recruiter decision; load its complete snapshot and ordered messages by id.
- Save an open turn: append candidate and/or assistant messages (either may be
  absent, as with the greeting, the reminder or a candidate message saved before
  closing), apply validated field changes, add pending questions and record actual
  clarification emissions in one transaction. Persist every candidate message,
  including off-topic or invalid answers, even if no fields change: invalid candidate
  evidence becomes a valid unresolved proposal, and an invalid proposal for one field
  does not reject the turn. Reject the whole call only for an unknown or closed
  application, or a stale revision. Use an expected application revision to reject
  stale writes; increment it on success. A retry with an old revision must reload,
  never silently append the same turn again.
- Close: the sequence is save the candidate's turn, evaluate, generate the summary
  outside any transaction, then close, so a failed summary loses nothing and can be
  retried from saved state. Close takes only the final assistant message and summary;
  it atomically appends that message, evaluates the stored fields and saves the
  outcome, `outcome_reasons`, closure timestamp and summary.
  E2 tests may supply fixture summaries; E3 generates real summaries. A closeable
  snapshot without a summary must not close. No successful close is returned
  until commit succeeds. Pending questions remain available for the summary.
- Change recruiter decision independently between `unreviewed`, `advance`,
  `rejected`; this never rewrites screening outcome, fields or conversation.
  E4 supplies the workflow. Minimal list/filter reads may be added when E4 needs
  them; E2 requires durable round-trip of this independent value only.

Closed applications reject further screening changes, messages and repeated
closure; recruiter decision remains mutable. Technical failures roll back the
whole operation, expose a retryable failure and preserve the last committed
state. Do not hold a transaction open across any future LLM/network call.
E3 delivers the closing message only after commit, restores history via browser
session id, handles retry/reload, and stops reminders after closure or a stop
request. E2 persists `stop_requested` while leaving the application incomplete;
explicit resumption can clear it. Reminder timers and session transport are E3
decisions. A committed close whose response
was lost can be recovered by loading the closed record; it must not reopen.

The database is the source of truth for reloading progress, not an in-memory
conversation. Full history means candidate and assistant content; do not persist
hidden reasoning or credentials. This local design does not add authentication.

## Delivery order, tests and closure

For each increment, first observe a relevant pytest failure, implement the smallest
behavior and verify it passes. Use fixtures/interpreted proposals and temporary
SQLite files; no network, model or frontend is necessary.

1. Seven-field serialization and validation: null versus false/0/[], months,
   restrictions, complete/incomplete names, explicit unknown start, still-imprecise
   start after its clarification and invalid shapes; an invalid proposal returns a
   per-field error while valid fields in the same turn commit.
2. Catalog and outcomes: country disambiguation, residence versus work preference,
   mixed covered/uncovered/unresolved alternatives, missing catalog failure,
   rejection priority, four outcomes with their `outcome_reasons`, early rejection
   with null fields, and pending review blocked by `missing` but allowed with
   `unresolved` fields.
3. Clarifications/corrections: initial question excluded, emitted-question limit,
   no count for ambiguous input alone, explicit inability to answer, correction at
   the limit, stale normalized
   value removal, unresolved fields versus harmless job questions.
4. Persistence: reopen a file and recover all data/history, candidate-only and
   assistant-only turns, last candidate message kept when closing fails, independent
   recruiter decision, atomic rollback including final message/summary, stale revision
   rejection, closed-state immutability and resumption of incomplete records.

Close E2 only when these contracts are implemented, saved JSON retains all keys,
existing HTTP checks still pass and `python -m pytest` passes from `app/` using
README commands. During implementation update this checklist and the roadmap
with actual evidence and remaining E3 scenarios; this planning-only task changes
neither the roadmap nor README. No claim of end-to-end screening before E3.

- [ ] Field model, catalog and deterministic rules verified through TDD.
- [ ] Clarification/correction and outcome contracts verified without an LLM.
- [ ] SQLite recovery, atomicity and closure invariants verified.
- [ ] Relevant checks recorded; E3 interpretation/orchestration boundary retained.
