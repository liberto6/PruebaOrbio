# E3 - Conversational agent

**Status: Complete, 2026-11-05, with the limits in the
[E3.4 evidence](#e34-evidence-2026-11-05).** The user confirmed E3 complete and requested
the [E4 brief](e4-recruiter-tool.md). Sources:
[assignment](../assignment.html), [process design](../design/process-design.md),
[roadmap](build-roadmap.md) and [E2 contracts and limits](e2-rules-and-persistence.md).

## Goal and scope

A candidate completes screening in a web chat; the seven fields, full history,
result and internal summary are saved. E2 remains responsible for validation,
coverage and outcomes. E3 interprets language, coordinates turns and exposes the chat.

Retain the adopted ES/EN, basic guardrails, single reminder and evals. The recruiter
interface is E4; submission packaging and presentation are E5. Exclude voice,
WhatsApp, RAG, authentication, deployment and additional screening criteria.
Use Python functions and the selected provider's SDK. No workflow framework,
queues, generic provider platform or additional agents are needed for this scope.

## Decisions and boundaries

- **Model: OpenAI `gpt-6-luna`, decided 2026-11-05 to keep selection simple.** It is
  economical (0.10/0.50 USD per million input/output tokens) and supports strict
  structured output; a probe call worked. Anthropic was dropped as paid only. Gemini
  Flash has free-tier quota that could not be confirmed and timed out at 60 s on its
  only probe call, so it is tried only if Luna fails the critical evals. Spending cap:
  5 USD for the whole stage, stopping to ask before exceeding it.
- Use provider-supported structured output, reuse E2 value models where compatible,
  and validate locally. A valid schema does not guarantee a correct interpretation.
  Pin only SDKs actually used and check compatibility with Python dependencies.
- Configure `SCREENING_MODEL` (default `gpt-6-luna`), with the credential from
  `OPENAI_API_KEY`. Changing provider needs an adapter, not just a model string.
  Missing credentials leave health/static pages available and
  return a clear chat error. Do not log credentials or hidden model reasoning.
- Normal answered turns use interpretation followed by reply generation after
  Python chooses an action. Closing also needs a summary. These are design choices,
  not a universal two-call requirement. Greeting, reminder and technical errors can
  use short ES/EN templates; no LLM call is needed for fixed operational messages.
- The browser stores the opaque application id; no separate session table. This
  identifies a local-demo conversation, not an authenticated identity.

## Turn contract and recovery

Use the existing SQLite database, revisions and transactions. Add only the durable
metadata needed to distinguish completed work from pending work:

- A small `turns` table keyed by application id and client-generated request id,
  with candidate message sequence, phase (`received`, `interpreted`, `completed`)
  and committed reply sequence. Retain the request id across transport retries;
  the same id with different text is a conflict, not another message.
- Conversation metadata on the snapshot: active language (ES by default), the last
  screening question's field and kind (initial/clarification), and `reminder_sent`.
  A reminder or technical error must not replace the screening question context.
- At most one unfinished candidate turn per application. Another request must wait
  or resume that turn. Enforce this on the server, not only by disabling the input.

These are small E3 extensions to `storage.py`, not features already supplied by E2.
The phase update and its corresponding messages/snapshot changes must share one
transaction. Extend the existing storage operations to do this; do not chain separate
commits and assume they are atomic. Keep revision checks for concurrent requests.
Additive metadata gets safe defaults for existing local records; no migration
framework or silent database deletion. Preserve all existing E2 behaviour.

For each candidate message:

1. **Receive:** validate the HTTP envelope, then atomically save the message once
   and create the `received` turn. Duplicate requests reload their recorded state.
   New messages to a closed application are refused; retries of a completed closing
   request return its already committed result.
2. **Interpret:** outside a transaction, provide saved history, field states, the
   explicit catalog, current date/time context and the last screening question.
   Return proposals, job questions, language preference and stop/resume intent.
   Accept information given ahead of time and corrections while the application is open.
3. **Validate and apply:** distinguish malformed model output from candidate doubt.
   A schema/type failure, refusal, truncation or invalid proposal applies no data:
   retain the saved message and existing fields, and finish the turn with a fixed
   ES/EN message asking the candidate to repeat it. This consumes no clarification
   budget, keeps the screening question context and never blocks the conversation.
   Record a stable code for each case so evals count it as a model failure.
   Provider unavailability (timeout, quota, credentials) is different: show the
   technical notice and keep the turn pending for an explicit retry.
   Prevalidate the complete extraction before applying it; E2's per-field error
   behaviour alone is not an atomic extraction commit. Genuine ambiguous/retracted
   evidence becomes a valid `unresolved` proposal and clears the stale value.
   Commit valid proposals, pending questions, language/stop intent and `interpreted`
   together. A candidate who replies without answering the asked field may leave
   that field unresolved with the actual reply as evidence; never mark unasked
   fields unresolved merely to force progress. Silence is not a candidate reply.
4. **Choose the next action** using the persisted state and the rules below.
5. **Finish in exactly one branch:**
   - Normal reply or stop acknowledgement: generate for the chosen action, validate
     nonblank text, then atomically save it, its question metadata, any clarification
     increment and the `completed` turn. A refused clarification must not produce
     a message or consume budget; reload/reselect rather than returning unsaved text.
   - Close: generate the final message and an internal summary outside transactions.
     Base the summary on saved facts, result/reasons and pending doubts/questions.
     Validate both as nonblank strings, including serialization-safe text. Use the
     close operation to re-evaluate and atomically save the final message, summary,
     outcome, timestamp and `completed` turn. Do not first save the final message
     through the normal branch. Return it only after commit.

A retry resumes the persisted phase: `received` interprets; `interpreted` chooses
and writes without reapplying extraction; `completed` returns confirmed history.
A failed summary leaves the candidate message and interpreted data intact. A reply,
closing message or summary that keeps failing holds its turn until a retry succeeds:
an accepted, documented limit, unlikely because these outputs are plain text. A lost
HTTP response after commit never repeats the append or close. On revision conflict,
reload and discard work generated for an outdated state. Concurrent retries may
repeat an external call, but must not duplicate its database effects; preventing
all duplicate provider charges is outside this local demo's scope.

Set finite provider timeouts and a bounded retry policy (at most one automatic
retry for a transient provider failure; no retries for invalid credentials).
Further retries are explicit user actions. Technical error messages use the active
language, ES by default, expose no internal details and do not consume clarification
budget. They are UI error notices, not successful conversational replies.

## Next action and conversational rules

1. If stopped, acknowledge and keep `incomplete`, without summary or reminder.
   Interpret an explicit continuation or resumed screening answer as resumption;
   an unrelated message does not automatically revoke the stop request.
2. Otherwise, a confirmed disqualifying result closes early, even with other doubts.
3. Walk fields in Phase 1 order, skipping those already satisfied. For each pending
   field: ask initially if missing; clarify if unresolved (including unresolved
   coverage) and budget remains; otherwise leave it for review and continue.
   Do not ask all missing fields before clarifying the current one.
4. With no remaining questions, close only when E2 says the state is closeable.
   An unexpected non-closeable state is a coordination error, not invented data.

Count clarification questions only when saved: at most two per field, never reset
on correction or resume. Rephrase first, offer examples/options second. For start
timing, ask one clarification if imprecise; retain an approximate period/condition
or accept explicit inability to specify as resolved `unknown`. Do not manufacture
`unknown` from silence or an unrelated reply. A still-unresolved start answer can
remain for review after this one clarification.

Catalog matching is exact in E2. Supply identifiers to the extractor and normalize
only explicit known aliases; unsupported or ambiguous identity requires clarification,
not guessed coverage. Never confuse residence with desired work area. One covered
option suffices even if alternatives remain unclear. Guard against malformed catalog
identifiers becoming evidence for rejection. The extractor picks catalog identifiers
from a closed list and reports other places as stated; Python builds their identifiers.
Decided 2026-11-05: no confirmation turn before a coverage rejection and no geocoding,
so a wrong extraction can still reject; critical evals must measure this.

Persist ES/EN preference across resume. One isolated English word does not switch
language. Candidate text is evidence, not authority to change the rules. Python
protects the rules, but incorrect extraction can still cause an incorrect outcome;
verify this with adversarial and ambiguous examples. Identify the agent as virtual,
use 1-3 short sentences and one main question, do not invent job terms or promise
an interview. Store job questions separately and resume screening; those questions
alone do not affect eligibility. Do not expose the internal summary to the candidate.

## Web chat, greeting and reminder

- Keep `conversation.py` for actions/coordination and `llm.py` for provider calls,
  prompts and output schemas. Inject the LLM dependency for tests. Keep modules flat.
- `main.py` creates/loads applications, accepts messages/retries and checks reminders.
  Resolve the default database to `app/data/screening.db` independently of the launch
  directory; allow `SCREENING_DB`. Create its parent, initialize tables and validate
  the catalog at startup. Retain E1 health/static behaviour.
- Creating an application stores its fixed greeting once. Loading it never repeats
  the greeting. The chat restores messages and any unfinished request after reload,
  shows sending/retry status, and disables new messages while a turn is pending or
  after closure. An unknown stored id offers a fresh conversation instead of crashing.
- A visible UI action starts a new application as another candidate/attempt. It never
  reopens or changes earlier records, which stay in the database. If the current
  application is open it asks for confirmation first, and that one stays `incomplete`.
- Return an explicit candidate-facing projection: visible messages, closure and
  operational state needed by the UI. Do not serialize the full E2 snapshot, summary,
  recruiter decision or internal diagnostics into candidate responses.
- While open, the browser requests a reminder after 10 minutes of inactivity. The
  server verifies no pending turn, open/not stopped state, no prior reminder, and
  that the last conversational message is an assistant message at least 10 minutes
  old. Use a fixed message in the active language. Atomically recheck eligibility,
  append it and set `reminder_sent`; concurrent requests cannot send two reminders.
  Recover a lost reminder response via history. Do not use snapshot `updated_at`,
  which recruiter actions can change. No background scheduler or external messaging.

## Delivery order

Use TDD with a fake LLM; no network or credentials in pytest. Review the next-action
function before completing E3.1 and review each block before starting the next.

| Block | Scope | Done when |
|---|---|---|
| **E3.1 Coordination** | Actions, minimal storage extensions, turn phases, close and retry | Fake-LLM scenarios and failure/concurrency checks pass without losing or duplicating stored work |
| **E3.2 Real LLM** | OpenAI adapter, extraction, replies, summary, ES/EN/guardrails, eval runner | Full conversations work; critical cases pass; quality, cost and latency are measured |
| **E3.3 Web chat** | Endpoints, public projection, chat, session/resume and visible errors | A full browser screening works, including reload, retry and closed state |
| **E3.4 Validation and closure** | Single reminder, remaining evals, examples and documentation | Agreed scenarios pass; results, limitations and sample conversations are saved |

Real calls require the relevant provider credentials. Agree a small spending cap
before running paid comparisons; this plan does not authorize unlimited API usage.
No real candidate data is needed: use synthetic cases. No E4 interface in these blocks.

## E3.3 implementation brief — revised 2026-11-05

**Status: implemented; see [E3.3 evidence](#e33-evidence-2026-11-05).** Connect the existing coordinator and model to
the browser: three endpoints and one chat screen. Reuse E3.1/E3.2; do not reopen
screening rules or model selection. Prioritize completing the candidate journey
and moving on to the recruiter tool over additional infrastructure hardening.

### Scope and implementation

- In `main.py`, load `app/.env` without overriding environment variables before
  resolving `SCREENING_DB`. Resolve relative database paths against `app/`; default
  to `app/data/screening.db`. Create the parent directory, initialize SQLite and
  validate the catalog before accepting screening requests. Ignore `app/data/`,
  including SQLite journal files; custom database locations remain the operator's
  responsibility.
- Keep health/static behaviour. Missing provider credentials must not prevent
  startup, creation or loading of a conversation.
- Reuse `start_application` and `handle_message`. Persist a valid incoming message
  before provider initialization can fail. A completed request must be recoverable
  without constructing a provider client or requiring credentials. Use a deferred
  provider dependency or an equivalent small change, not a new orchestration layer.
- Keep modules flat and inject a fake model for HTTP tests. Read the application,
  messages and unfinished turn in one transaction for a consistent public response.
  No new table is required.
- Build one React chat screen with existing JavaScript/CSS. No streaming,
  WebSockets, periodic polling, frontend router or new UI/state library.
- The reminder and its timed browser check remain E3.4. Recruiter screens remain
  E4. No new model-comparison battery or submission packaging in this block.

### HTTP and public response

| Operation | Endpoint | Contract |
|---|---|---|
| Create | `POST /api/applications` | Return 201 with a new application and its single saved greeting |
| Load | `GET /api/applications/{id}` | Return 200 with saved public state; no model call |
| Send or retry | `POST /api/applications/{id}/messages` | Accept strict string `request_id` and `text`; return 200 after processing or recovering a completed turn |

There is no separate retry endpoint. Resubmit the same request id and exact text;
the coordinator already resumes the recorded phase without duplicating committed
effects. The same id with different text is a conflict.

Use an explicit response allowlist: `id`, `messages` (sequence, role, text and
created_at), `language`, `closed` and `pending` (null or request_id and candidate
message sequence). The message sequence lets the browser recover the pending text
from history even when its local request copy is missing. Do not serialize a
snapshot and then remove selected keys. Summary, fields, recruiter decision and
internal diagnostics are absent from successful and error responses alike.

Validate blank/non-string ids and text and invalid Unicode before storage. Use
stable public error codes with localized notices; do not return exception text or
the raw validation input. The browser reloads public state when reconciliation is
needed, rather than assuming whether a failed HTTP request committed.

| Situation | HTTP / public code | Browser behaviour |
|---|---|---|
| Invalid request envelope | 422 / `invalid_request` | Keep the text for correction; no automatic retry |
| Unknown application | 404 / `application_not_found` | Offer a new conversation; do not silently create one |
| Same request id, different text | 409 / `request_conflict` | Reload state; do not change the id and silently resend |
| Another unfinished turn | 409 / `turn_pending` | Reload and recover that turn; block new messages |
| New message to a closed application | 409 / `application_closed` | Reload the closure and disable the input |
| Missing/invalid credentials or quota failure | 503 / `service_configuration` | Keep the request; explain that the service is unavailable now and retry later |
| Other handled technical failure (provider, generated reply/summary, coordination or storage) | 503 / `service_unavailable` | Keep the request, reload state and offer explicit retry; do not claim a successful save |
| Unexpected failure | 500 / `internal_error` | Same recovery behaviour with a generic notice |

An unusable interpretation already handled by the coordinator is a completed
conversational turn, not an HTTP error: return its saved repeat-request message.
A duplicate completed closing request returns 200, not `application_closed`.
Technical notices remain outside conversation history. Detailed causes stay in
server diagnostics; the UI shares one recovery path for technical failures.

### Browser state and recovery

- Persist the application id and only the unconfirmed request's id and exact text
  locally before sending. SQLite is authoritative for conversation history.
- On reload, fetch saved state and recover any unfinished turn. GET does not
  identify which completed turn matches a local request: never infer completion
  by matching message text. If the local request remains unconfirmed, keep it
  and offer explicit resubmission with the same id and exact text. POST already
  handles both an unreceived request and a completed turn. Clear the local copy
  after its successful response. No model call on page load and no loss of the
  local request on a failed load. A closed conversation stays read-only; retrying
  an unconfirmed closing request only recovers its committed result.
- Show sending status immediately. Disable new messages during sending, an
  unconfirmed request, an unfinished turn and closure. The server enforces pending
  turns and closure; the browser also guards its local unconfirmed request.
  A voluntary stop is not closure: writing must remain possible to resume.
- On reload during processing, say that a message is pending confirmation, not
  that it necessarily failed. The original server request may still be running.
  Concurrent retries may duplicate provider calls, but not committed messages;
  this is the existing accepted local-demo limit. Do not add cross-tab
  synchronization, active-request tracking or a separate recovery protocol.
- Render saved messages as text, preserve keyboard access and support mobile
  widths. Errors appear beside the input; the closing message stays in history.
- Use the active conversation language for buttons and notices, Spanish by
  default. For configuration/quota failures use the equivalent of
  “El servicio no está disponible ahora mismo. Puedes volver a intentarlo más
  tarde”, rather than implying that immediate repetition fixes the cause.
- “Nueva candidatura” creates another record. Confirm before leaving an open
  application; retain it as incomplete. Never reopen or alter the earlier record.
  Without identity, repeated attempts by the same person cannot be prevented.

Creation is not covered by candidate-turn idempotency: a lost creation response
can leave an unused record with only its greeting. Do not add a creation-recovery
protocol for this demo or claim exactly-once creation.

**Visual style, selected by the user on 2026-11-05:** extend existing CSS with an
off-white background, charcoal text, terracotta accent and system fonts. One responsive
column, virtual-assistant header, messages and input; no extra screens or panels.

**First visit, decided by the user on 2026-11-05:** opening the page creates the
application and shows the greeting. Someone who only opens the page therefore leaves an
incomplete record holding just the greeting, which will appear in the E4 panel.

### Delivery and acceptance

Implement backend behaviour through TDD: observe the relevant failing test, then
implement and verify. All HTTP tests, including existing health tests, must use a
temporary database and no real provider. Test new HTTP wiring and browser-facing
behaviour. E3.1 retains exhaustive phase/concurrency coverage: do not reproduce
that matrix over HTTP or add another stress/mutation campaign.

1. Startup, configuration, creation/load and public projection.
2. Message processing, error mapping, idempotency and recovery.
3. Browser chat and session restoration.
4. Browser verification, regression checks and documentation.

- [x] HTTP checks cover unique greeting, strict inputs, unknown/closed ids,
  missing credentials, provider/storage/unexpected failures and public allowlist.
- [x] HTTP checks cover sending, duplicate/conflicting ids, refusal of a new
  message while a turn is pending, one recoverable failure and retrieval of a
  completed closing request after a lost response, including without credentials.
- [x] Browser checks cover reload mid-conversation and during an unfinished turn,
  retry without duplicate messages, closed state, unknown stored id and a new
  candidature that leaves the old record intact.
- [x] A complete qualified screening and an early rejection work in the browser
  with the real model and synthetic data, within the remaining documented E3 cap.
  Verify saved summary and history, and that the summary is absent from public
  responses. Record actual evidence; fake-model checks alone do not close E3.3.
- [x] Backend suite and frontend build pass. Record concise evidence and limits
  here; update roadmap and README with current behaviour and a link to the evidence,
  without duplicating the report. Check AGENTS.md still matches delivered scope.

Show waiting feedback immediately, but do not promise a 60-second turn limit:
provider timeout/retry settings apply to individual calls and a turn uses several
sequential calls. Existing model limits remain; demo contingency packaging is E5.

Planning baseline checked on 2026-11-05: 593 pytest tests passed and the frontend
build passed. These are pre-E3.3 checks, not acceptance evidence for this block.

## E3.4 implementation brief — 2026-11-05

**Status: implemented; see [E3.4 evidence](#e34-evidence-2026-11-05).** Finish E3 with the
single reminder, focused real-model checks for the remaining behaviours and representative
conversations. Reuse E3.1–E3.3 and their evidence; do not reopen model selection, add
dependencies or repeat their full eval, concurrency or browser matrices.

### Single reminder

- Reuse `reminder_sent` in the snapshot. Add one storage operation and
  `POST /api/applications/{id}/reminder`, with no body or model dependency.
  Return 200 with the current public state whether a reminder was saved or was
  ineligible; an unknown id remains 404. Reuse existing technical error handling.
- Inside one write transaction, check that the application is open, not stopped,
  has no unfinished turn and has never sent a reminder. Its last conversational
  message must be from the assistant and at least 10 minutes old, using server
  UTC time and message `created_at`, not snapshot `updated_at`. The greeting counts.
  If eligible, append the fixed ES/EN message and set `reminder_sent` together,
  preserving revision checks. Repeated or concurrent calls must save at most one.
- Suggested copy: “Si quieres, podemos continuar cuando estés listo.” / “If you'd
  like, we can continue when you're ready.” Use the saved language. Preserve fields,
  outcome, last screening question and clarification counts; create no candidate
  turn or summary. Stopping or resuming never resets `reminder_sent`.
- Add only `reminder_due_at` to the public allowlist: the last assistant message's
  timestamp plus 10 minutes when the other eligibility conditions hold, otherwise
  null. Compute it from the same consistent read as messages and pending state.
  It schedules a check; the server still rechecks eligibility before writing.
- In the existing chat, schedule one timeout for that deadline, or check immediately
  if it has passed on reload. Suspend it while sending, starting another application
  or holding an unconfirmed request; cancel obsolete timers. A new saved state
  recalculates the deadline. Do not use periodic polling, a scheduler or a new table.
- Render only the saved reminder. On failure, reload history once to recover a
  possible committed reminder; avoid an immediate retry loop or blocking candidate
  input. Discard reminder responses made obsolete by a candidate send or application
  change so they cannot overwrite newer history. A later reload may check again.
  Browser suspension can delay the reminder; no delivery is promised while closed.

### Remaining real-model checks

Stop/resume already exists in the coordinator and fake-model tests. Validate its
interpretation with the existing runner, adding only the cases and assertions needed
below. `TurnContext` currently has no stop state; change that contract only if these
checks expose a failure requiring it.

| Case | Required evidence |
|---|---|
| Stop, unrelated job question, explicit continuation | Stop keeps the application open/incomplete without summary; the job question is stored but does not resume screening; explicit continuation resumes from saved facts and retains clarification counts |
| Stop, then answer the outstanding screening question | The answer resumes screening, applies its data and keeps the saved language; use an English conversation for this case |
| Imprecise start, then approximate period | Ask one clarification, accept the approximate period without inventing a date; otherwise complete profile closes as qualified |
| Imprecise start, then condition | Ask one clarification, retain the condition; otherwise complete profile closes as qualified |
| Imprecise start, then explicit inability to specify | Ask one clarification, resolve as `unknown`; otherwise complete profile closes as qualified |

Run each of these five scenarios three times. Inspect intermediate stop state and
clarification counts, not only the final outcome. Reuse the existing unresolved-start
case as the negative control: one clarification, then `pending_review` if all other
fields are resolved. Do not expand the scenario battery beyond these gaps.

Use synthetic data and the selected model within the existing 5 USD E3 cap. Before
running, account for recorded eval spend (about 0.83 USD in E3.2) and the unmetered E3.3
browser calls; do not present the runner's ledger as exact total spend. Record new
tokens, estimated cost and latency with the existing runner. Keep failed runs. Link
the actual result files even if the runner retains its `e3-2-` filename prefix.

Expected states and facts must pass, with no observed false rejection/qualification
or invented critical facts. Manually inspect the new dialogue and summaries for
faithfulness, Spanish internal summaries and ES/EN behaviour. If a defect needs a
prompt or adapter change, rerun the affected existing critical cases as well; do
not claim the old results validate changed prompts. Record any unresolved failure
and its effect on closure rather than silently weakening expectations.

### Delivery and acceptance

Implement the reminder backend through TDD, with controlled time, temporary SQLite
and no network. Extend existing tests; do not introduce a frontend test framework.

- [x] Storage checks: just before/at 10 minutes; ES/EN; no reminder when stopped,
  closed, pending or already sent; atomic rollback and simultaneous checks; question
  context and clarification counts unchanged. Resume permits a first reminder but
  never a second one.
- [x] HTTP checks: saved reminder and ineligible no-op return the public projection,
  unknown id, operation without credentials, and recovery after a lost response.
- [x] Served-browser checks: timed appearance once, reload without duplication,
  stop then reload without a reminder, resume in the same application, and no stale
  reminder response replacing a newer turn or another application. Controlled time
  or synthetic message timestamps may shorten the wait; document the method.
- [x] Focused real-model cases above pass and results, cost, latency and manual
  observations are recorded here. A model failure requires correction and relevant
  rechecks before closing this block; accepted earlier limits remain explicit.
- [x] Save `docs/evals/sample-conversations.md` with four actual synthetic transcripts:
  qualified, early rejection, pending review and stop/resume. Reuse existing passing
  runs where suitable; link each source run/case and distinguish candidate-visible
  dialogue from saved outcome and internal summary. Never rewrite a transcript as
  if the model had produced it. This is the assignment deliverable, not a status file.
- [x] Run `python -m pytest` from `app/` and `npm run build` from `app/frontend/`.
  Record actual results and limits; update the E3 closure checklist, roadmap and
  README. Confirm AGENTS.md still matches the active plan, stack and exclusions.

Close E3 only with this evidence. The next implementation stage is E4; recruiter UI,
authentication, Docker, CI, deployment, WhatsApp, voice and E5 packaging remain out
of this block.

## Evals

One adapter implements the interpretation/reply/summary contract; no plugin registry,
routing or failover. Use fixed scenario inputs, expected facts and application rules
so that runs are reproducible and comparable. Start with 20-30 synthetic cases,
including held-out variants not used to tune prompts. Repeat critical ambiguous
cases three times to observe variability. Record model/version/settings, prompts,
scenario results, per-field correctness, false rejection/qualification, unnecessary
review, summary faithfulness, full-turn latency and total billed tokens/cost including
retries and reasoning where applicable. Review dialogue samples manually.

A model passes only with no observed false rejection/qualification or invented
critical facts in the fixed critical cases, and correct expected states, corrections
and summary facts. Do not count excessive `pending_review` as success. This is a
small project acceptance set, not a statistical claim about production reliability.
If Luna fails, inspect prompts/extraction failures before trying another model. The full historical
context is saved; measure the cost of resending it rather than assuming free memory.

Critical E3.2 cases: a qualified path with early information and corrections; no
license; "me lo estoy sacando" both ways; Madrid identifiers; Guadalajara ambiguity;
residence versus work zone; uncovered and mixed zones; repeated ambiguity; explicit
unknown start; months/no experience; ES/EN switch; job questions without invented
terms; an instruction to ignore the screening rules; and a candidate who dodges an
asked field, which must become `unresolved` and reach the clarification limit
instead of repeating the initial question without end. Complete the rest of the
Phase 1 scenarios in E3.4, including stop/resume and imprecise start after clarification.

Fake-LLM/storage/HTTP checks must also cover malformed extraction preserving previous
facts, genuine ambiguous corrections clearing them, failure after each commit phase,
summary failure, lost HTTP responses, duplicate requests with the same/different
payload, stale/concurrent writes, repeated close, stopped state and greeting/reminder
uniqueness. Verify non-string and invalid-Unicode output handling, error language,
missing credentials, unknown/closed ids, and absence of internal summary in public
responses. Use controlled time for reminder tests, not real ten-minute waits.

Keep all comparison results, including failures; choose representative passing
transcripts for the assignment's sample-conversation deliverable. If time is short,
reduce additional model runs and noncritical eval breadth, not critical quality
checks or the recorded rationale for the selected model. Any dropped Phase 1 bonus
must be explicitly reflected in the design and roadmap.

## Closure

- [x] E3.1: next action, turn recovery and atomic storage extensions verified.
- [x] E3.2: complete runner conversations, critical evals and documented model rationale.
- [x] E3.3: browser chat, resume, public responses and failure paths verified.
- [x] E3.4: reminder, remaining evals, results and sample conversations recorded.
- [x] Existing E2 tests and new tests pass without network; no screening rule changed.
- [x] Stage plan, roadmap, README and AGENTS.md reflect delivered work and real limits.

E3 is complete when the browser screening works with the selected real LLM and the
criteria above have evidence. Recruiter workflow and submission/presentation remain
E4/E5. Do not claim completion from fake-LLM tests alone.

### E3.1 evidence (2026-11-05)

- Commits `fde6e19` to `cadc44e`: turn phases and metadata in `storage.py`/`rules.py`,
  `next_action` and turn coordination in `conversation.py`, and the model contract in
  `llm.py`. No HTTP endpoint or provider SDK was added.
- `python -m pytest` from `app/`: 501 passed, with the known Starlette/HTTPX warning.
  No test uses the network or a real LLM.
- An independent read-only review tried to break the block. Executed checks: 300
  randomized conversations against an oracle, 60 rounds of 8 real threads, process
  kills during interpret, reply and summary, and 93 hand mutations (6 survived).
  It found a false rejection through wrong place identifiers, a stale failure phase,
  a close that could orphan a turn and six test gaps. All were fixed in `992ae58` to
  `cadc44e`. The review itself was not repeated on the fixes: its reproduction script
  was rerun and the tech lead read the diff and exercised the repeat-request flow.
- TDD limits: most new tests first failed on a missing name or stub, not on behaviour,
  and some passed when written. Several gap tests were checked by mutation instead.

Known limits E3.2 and E3.3 must respect:

- A clean but wrong identifier, such as `("ES","centro_madrid")` or `("MX","madrid")`,
  still rejects, because Python cannot tell it from a real place such as `("ES","bilbao")`.
  This is the main risk for E3.2: prompts and critical evals must cover it.
- A candidate who dodges a question is re-asked without limit unless the interpreter
  proposes `unresolved`. E3.2 must prove that it does.
- Adapters must translate every SDK error to an `LLMFailure` subclass; anything else
  escapes the coordinator with its raw text. E3.3 needs a generic error handler, and it
  owns envelope validation (blank, non-string and surrogate ids and text) and the
  public projection, because coordinator results include the internal summary.
- A crash between the two repeat-request commits can make the resumed reply consume one
  clarification. The state stays valid; this is accepted as very unlikely.

### E3.2 evidence (2026-11-05)

- Commits `8fa5d01` onward: OpenAI adapter with a closed extraction schema, prompts and
  the eval runner in `app/evals/`. Pins: `openai==3.46.0`, `python-dotenv==1.2.4`.
- `python -m pytest` from `app/`: 593 passed, with no network or real LLM.
- Final prompts, tuned cases (41 scenarios, 89 runs): 89 passed (`e3-2-20261105T052616Z`).
- Final prompts, held-out and independent cases (28 scenarios, 84 runs): 82 passed
  (`e3-2-20261105T052114Z`). One failure is the check not recognizing the spelling
  "carné". The other is real: one job question was not stored in one run.
- Those final runs had no false rejection, false qualification, unnecessary review or
  `interpretation_unusable`. An earlier run of the previous prompts also failed 6 tuned
  runs with `llm_transient` while two batteries ran in parallel. Repeated alone they
  passed 9/9 and the final battery did not repeat them; the cause was not confirmed.
- Cost of E3.2: about 0.83 USD of the 5 USD stage cap. Full-turn latency in the final
  runs: median about 3.2 s; maximum 8.9 s tuned and 18 s held-out.
- All runs, including failures, are in `docs/evals/`.

What the counters do not show: an independent evaluator wrote 20 new cases from the
design without reading the prompts. It got 53/60 with those counters at zero, because
wrong license readings occurred in conversations still open at the end of the script.
The prompt was then changed and three variants were added; those license cases went
55/55 before the "tengo coche" decision below and are no longer a clean measurement. One
evaluator expectation was changed by the tech lead (decision 1 below). The independent
review was not repeated after these changes. Summaries are checked mainly by keywords.
This is a small acceptance set, not a statistical claim about production reliability.

Product decisions from E3.2:

| # | Candidate says or case | Behaviour | Status |
|---|---|---|---|
| 1 | "Me lo quitaron, lo recupero en enero" | Rejects at once: not held today | Validated by the user |
| 2 | "Lo tengo caducado" | Clarifies; still not valid rejects | Validated by the user |
| 3 | Motorbike or foreign licence held now | Counts as holding a licence | Validated by the user |
| 4 | "Sí, tengo coche" or another vehicle after a yes | Counts as holding a licence | **Decided by the user** |
| 5a | "En Vallecas" (district, no city) | Asks which city | Validated by the user |
| 5b | "En Getafe" (town near a catalog city) | Rejects: not in the catalog | Validated by the user |
| 6 | Naming covered cities | Only on the second clarification, and all of them | Validated by the user |
| 7 | Recruiter summary language | Always Spanish | Validated by the user |

Decision 4 can produce a false qualification: a candidate with a car and no licence who
answers this way passes that check. The user accepted the behaviour knowingly.

Known limits:

- A clean but wrong place identifier can still reject; there is no confirmation turn.
- `TurnContext` has no stop state, so the interpreter infers it from history. E3.4 must
  cover this with the stop/resume scenarios.
- A job question can occasionally go unstored, as in the run above.
- A rejection summary can omit nuance, such as a licence recovered in January, and
  "Dudas pendientes" varies between summaries. The full conversation is kept.
- Nothing in Python limits the repeated initial question if the model proposes nothing.

### E3.3 evidence (2026-11-05)

- `main.py` has the three endpoints, an explicit public allowlist and one stable code per
  failure. `DeferredLLM` builds the provider on the first model call, so saving and
  recovering a turn need no credentials. The chat is one React screen; no dependency added.
- `python -m pytest` from `app/`: 641 passed, 48 of them new, with no network or real LLM.
  `npm run build` and `pip check` passed. New HTTP tests were first seen failing, except a
  storage-failure-on-load test that the previous step's handler already satisfied.
- Checked in Chrome through the served build, on a temporary database with synthetic data:

| Check | Result |
|---|---|
| Send without credentials | Message saved once, 503 notice, input blocked, retry offered |
| Restart with the key, then retry | Real model answered; 3 messages, none duplicated |
| Reload during a running turn | Says pending confirmation; the server finished it; retry returned it, 5 messages |
| Plain reload | Same history, one greeting, input ready |
| Qualified screening, real model | Outcome `qualified`, 7 fields, 15 messages, 7 completed turns |
| Early rejection, real model | Outcome `disqualified` after the licence answer, 5 messages |
| Saved summary and public response | Summary saved in both closed records; absent from public responses |
| Closed application after reload | Read-only, closing message in history |
| New application | Direct if closed; confirms if open; cancel keeps it; old record intact and open |
| Unknown stored id | Notice and new-conversation button, nothing created until pressed |
| English conversation | Buttons, placeholder and page language switch; a new application returns to Spanish |
| 386 px wide frame | No horizontal page overflow; the transcript was also inspected in a screenshot |

Limits of this evidence: browser checks were run once by a script driving the page, with
no repeatable frontend test suite. Width was tested in an iframe, not on a device, because
resizing the window had no effect. Most steps were read from the DOM; screenshots timed out
for part of the session. Real-model cost was not metered: about 13 turns, against 0.09 USD
for a full eval battery. The browser did not exercise a lost closing response or provider
timeouts; HTTP tests cover those.

Known limits:

- A request confirmed by neither side after the server finished it shows a dimmed
  "waiting for confirmation" copy of the text until the candidate retries. The retry only
  recovers the saved result. This is by design: completion is never inferred from text.
- A record opened and never used stays as an incomplete application with its greeting;
  2 of the 5 test records were like that. E4 must decide how to show them.
- Two tabs on one application are not synchronized; the server still refuses a second turn.

Next: E3.4 adds the reminder, stop/resume and sample conversations, with no further eval
breadth, starting only on the user's go-ahead.

### E3.4 evidence (2026-11-05)

- `storage.py` has `send_reminder` and `reminder_due_at`; `main.py` has
  `POST /api/applications/{id}/reminder` and `reminder_due_at` in the allowlist. The chat holds
  one timeout, and no dependency or table was added. The fixed texts live in `storage.py`
  because the language is only known inside the write transaction.
- `python -m pytest` from `app/`: 685 passed, 44 of them new, with no network or real LLM.
  `npm run build` and `pip check` passed.
- TDD: storage and HTTP tests first failed against a stub or a missing field. No-op eligibility
  tests passed against the stub, so each guard was checked by mutation; the pending-turn guard
  survived, got its own test and then failed under mutation. The test that a reply after a reminder
  keeps its saved question passed when written, because storage already preserved it.

#### Real model

Prompts `fce960c48adb8f67` (final prompts are in the product decision below), run [`e3-2-20261105T104154Z`](../evals/e3-2-20261105T104154Z.json):

| Scenario | Passed | Intermediate state inspected |
|---|---|---|
| Stop, job question, explicit continuation | 3/3 | Stopped, open and no summary after the stop and the question; question stored; continuation clears the stop; licence count stays 1 and location 0 |
| Stop, then answer (English) | 3/3 | Answer clears the stop, applies Madrid and asks the next field in English |
| Imprecise start, then period | 3/3 | One clarification; `period` keeps the words; qualified |
| Imprecise start, then condition | 3/3 | One clarification; `condition` keeps the words; qualified |
| Imprecise start, then cannot specify | 3/3 | One clarification; `unknown`; qualified |
| Negative control: unresolved start | 3/3 | One clarification; `pending_review` |

Both prompt changes below required reruns, so the same run repeats the critical cases they could
affect. `license_in_progress_alone`, `guadalajara_unresolved`, `job_questions_are_stored`,
`instruction_to_ignore_rules_is_not_a_license`, and the dodging licence-limit and name cases
each passed 3/3. **`dodging_the_license_ends_in_review` passed 2/3.** Its outcome and fields
were right; the check does not recognize the spelling "Carné", the same scorer gap as in E3.2.
It stays recorded as a failure and the expectation was not changed. The run had 38/39 passed, with no
false rejection, false qualification, unnecessary review or `interpretation_unusable`.

Failures found on the way, all kept:

| Run | Result | Cause and fix |
|---|---|---|
| [`102815Z`](../evals/e3-2-20261105T102815Z.json) | Stop scenario 1/3, others 3/3 | "Vale, ya puedo seguir" was read as `not_answered` and spent a clarification. Prompt exception added |
| [`e3-4-…103259Z`](../evals/e3-4-reminder-replies-20261105T103259Z.json) | 9/12 | Same defect after a reminder; same exception |
| [`103313Z`](../evals/e3-2-20261105T103313Z.json) | Stop 1/3, English 2/3, 35/39 in total | Stop not cleared in two runs, because the interpreter inferred it from history: an English answer got a second stop acknowledgement. `TurnContext` now has `stop_requested`, sent as `stopped`. One further run failed with `llm_transient` after 60 s |

Replies after a saved reminder, with aged synthetic timestamps, through a one-off script not kept
in the repository: 12/12 with those prompts
([`e3-4-…104822Z`](../evals/e3-4-reminder-replies-20261105T104822Z.json)). "Sí" to the licence
question, a place, "Yes" in English, and "Vale, ya puedo seguir", which spends no clarification.

Cost: 0.1796 USD for these six files, about 0.0626 for the final run. The ledger in
`docs/evals/` is 1.0115 USD. Adding the unmetered browser calls (about 13 turns in E3.3 and 8 here,
at about 0.0003 USD per turn) gives about 1.02 USD of the 5 USD cap: an estimate, not a bill.
Full-turn latency in the final run: median 3.2 s, maximum 36 s over 237 turns. Ten turns took over
25 s, with single calls of 31-34 s, which matches the 30 s timeout plus its one retry; the cause
was not investigated further.

Manual reading of the final runs (the 6 whole stop dialogues, the closing turns of the 9 start
dialogues from the start question on, and their 12 summaries): summaries are
Spanish and faithful, with no invented date; start answers are kept in the candidate's words, and
English stays English after the fixed Spanish greeting. Dialogue defects seen, not fixed:

- In one final run the answer to a job question asked while stopped ignored it, and the "no tengo
  esa información" arrived a turn later. The question was stored in all runs.
- Some initial start questions already offer an approximate period, which the prompt reserves for
  the clarification. One clarification was odd: "aunque estés tramitando otra".

#### Served browser

Built frontend served by FastAPI on port 8765, in Chrome, with a temporary `SCREENING_DB`, since
deleted. Time was shortened by a throwaway script that moved `created_at` of an application's
messages in that database and then reloaded the page; the timer itself ran for real, for 4 to 25 s.
Results were read from the DOM and a wrapper around `fetch` that logged, held or failed reminder
calls. No screenshots. Real model for the candidate turns; each check ran once.

| Check | Result |
|---|---|
| Timed appearance | Absent before the deadline, then shown once from one POST; input stayed usable |
| Reload after it | Same two messages, one reminder |
| Reply after the reminder | Name accepted, next question asked |
| Stop, then reload an hour past due | `reminder_due_at` null, no reminder, input usable |
| Resume in the same application | Same id, screening continues, a new deadline; first reminder then appears once and a reload keeps one |
| Stale response against a newer turn | Reminder response held while a message was sent; on release the 10 messages of the newer state stayed |
| Stale response against another application | Held across "Nueva candidatura"; the new one kept its one greeting, and the old record has its reminder |
| Lost reminder response | POST then one GET, no second POST, no notice; reminder shown |

The first round ran before the final prompts. Stop, reload, answer-to-resume and the reminder after
resuming were repeated on the final code; the stale and lost-response checks were not, because the
final change only touched interpretation, not the frontend or the endpoint.

#### Limits

- Full batteries were not rerun with the final prompts, as instructed; earlier results for other
  cases come from the previous prompts. The early-rejection sample also uses them.
- The timer compares the browser clock with a server timestamp. If the browser runs ahead, its one
  check is a no-op and the reminder waits for a reload. Both are the same machine in this demo.
  A suspended or closed browser sends nothing, as the brief accepts.
- There is no repeatable frontend test suite, and the reminder can arrive while someone is typing.
  Their draft is kept.
- Runs and samples use fixed scripts. This is a small acceptance set, not a statistical claim.
- Earlier limits remain, except that `TurnContext` now has the stop state.
- [Sample conversations](../evals/sample-conversations.md) are copied by script from run files.

#### Product decision: a bare yes after the reminder

The results above show a bare "Sí" after the reminder counting as holding a licence, which could
produce a false qualification. The user decided that "Si quieres, podemos continuar" answered with
"Sí" does not mean holding a licence, knowing that a model gives no absolute guarantee. A bare
acceptance right after the reminder now answers no field: the question is asked again and no
clarification is spent. A reply with its own content, such as "Sí, tengo carnet", still counts.

Prompts `7cd32219725ef7e1`, the final ones for E3:

| Run | Result | Notes |
|---|---|---|
| [`e3-4-…110416Z`](../evals/e3-4-reminder-replies-20261105T110416Z.json) | 21/21 | "Sí", "Vale", "Yes" and "Vale, ya puedo seguir" keep the licence unsaved with no clarification spent; "Sí, tengo carnet" and a place are saved; "Sí" with no reminder still counts. One-off script, not kept |
| [`110610Z`](../evals/e3-2-20261105T110610Z.json) | 37/39 | Same critical cases as the run above. No false rejection, false qualification, unnecessary review or `interpretation_unusable`. Two failures are `llm_transient`, in `stop_job_question_then_explicit_continuation` and `imprecise_start_then_period` |
| [`111651Z`](../evals/e3-2-20261105T111651Z.json) | 6/6 | Those two scenarios repeated alone, 3/3 each |

The failures are attributed to the provider because they were timeouts and did not recur; this was
not confirmed further. `dodging_the_license_ends_in_review` passed 3/3 here, but its scorer gap
on "Carné" remains. The sample conversations and stop/start tables above used the previous prompts,
which differ only in this exception. Full batteries were not rerun.

Limits this adds:

- In 1 of the 3 English "Yes" runs, the saved state was right (licence unsaved, licence question
  still pending) but the reply said the licence had been noted and asked for the city. The script
  does not check reply text, so it counts as passed. The licence is asked again on the next turn.
- Latency worsened in `110610Z`: 16 of 231 turns took over 25 s, and four single calls took about
  243 s, well beyond the expected 30 s plus one retry. So the configured timeout does not bound a
  call in practice. `111651Z` reached 40 s at most. The cause was not investigated, and it can show
  in a live demo.

New cost: 0.0853 USD for these three files, 0.2649 USD for E3.4 in all. The ledger is 1.0966 USD;
with the unmetered browser calls, about 1.10 USD of the 5 USD cap, an estimate rather than a bill.

Next: [E4 recruiter tool](e4-recruiter-tool.md), as requested by the user.
