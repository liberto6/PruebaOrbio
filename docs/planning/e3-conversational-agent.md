# E3 - Conversational agent

**Status: In progress, revised 2026-11-05.** E3.1 is implemented with a fake LLM; no
real model, HTTP endpoint or chat exists yet, so E3 is not complete. Sources:
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

- **Model is not selected.** Compare a small shortlist in E3.2 before fixing the
  provider. Prefer an economical model that meets the quality criteria below;
  a flagship may be a reference, not an assumed default. Verify current model IDs,
  prices, schema support and SDK options against official documentation at that time.
  Agreed 2026-11-05: providers OpenAI and Google (Anthropic dropped: paid only);
  candidates a Luna model and a Gemini Flash model, exact IDs and free-tier quota
  to be confirmed at E3.2 start; spending cap 5 USD for the whole stage, stopping
  to ask before exceeding it.
- Use provider-supported structured output, reuse E2 value models where compatible,
  and validate locally. A valid schema does not guarantee a correct interpretation.
  Pin only SDKs actually used and check compatibility with Python dependencies.
- Configure `SCREENING_PROVIDER` and `SCREENING_MODEL`, with credentials from the
  chosen provider's environment variable. Changing provider needs an adapter, not
  just a model string. Missing credentials leave health/static pages available and
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
| **E3.2 Real LLM and selection** | Candidate models, SDK adapters, extraction, replies, summary, ES/EN/guardrails, runner | Full conversations work; critical cases pass; measured quality/cost/latency justify the chosen model |
| **E3.3 Web chat** | Endpoints, public projection, chat, session/resume and visible errors | A full browser screening works, including reload, retry and closed state |
| **E3.4 Validation and closure** | Single reminder, remaining evals, examples and documentation | Agreed scenarios pass; results, limitations and sample conversations are saved |

Real calls require the relevant provider credentials. Agree a small spending cap
before running paid comparisons; this plan does not authorize unlimited API usage.
No real candidate data is needed: use synthetic cases. No E4 interface in these blocks.

## Model comparison and evals

Use two economical candidates initially and at most one additional reference if
needed. Confirm the shortlist, access and current prices at E3.2 start. Provider
adapters only need the interpretation/reply/summary contract; do not build a plugin
registry. Retain the chosen integration for the product and reproducible comparison
instructions. No automatic routing between models or provider failover is required.

Use the same scenario inputs, expected facts and application rules for all models;
provider-specific schema formatting is allowed. Start with 20-30 synthetic cases,
including held-out variants not used to tune prompts. Repeat critical ambiguous
cases three times to observe variability. Record model/version/settings, prompts,
scenario results, per-field correctness, false rejection/qualification, unnecessary
review, summary faithfulness, full-turn latency and total billed tokens/cost including
retries and reasoning where applicable. Review dialogue samples manually.

A model passes only with no observed false rejection/qualification or invented
critical facts in the fixed critical cases, and correct expected states, corrections
and summary facts. Do not count excessive `pending_review` as success. This is a
small project acceptance set, not a statistical claim about production reliability.
Choose among passing models by cost, latency and integration effort. If none passes,
inspect prompts/extraction failures before increasing model size. The full historical
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
- [ ] E3.2: complete runner conversations, critical evals and documented model choice.
- [ ] E3.3: browser chat, resume, public responses and failure paths verified.
- [ ] E3.4: reminder, remaining evals, results and sample conversations recorded.
- [ ] Existing E2 tests and new tests pass without network; no screening rule changed.
- [ ] Stage plan, roadmap, README and AGENTS.md reflect delivered work and real limits.

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
