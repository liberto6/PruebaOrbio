# E4 — Recruiter tool

**Status: complete, confirmed by the user on 2026-11-05.** See the
[E4 evidence](#e4-evidence-2026-11-05) and its limits. Sources: [assignment](../assignment.html),
[process design](../design/process-design.md), [roadmap](build-roadmap.md),
[E2 contracts](e2-rules-and-persistence.md) and [E3 evidence](e3-conversational-agent.md).

## Goal and scope

A recruiter can find an application, understand the screening and record a decision
that survives reload. Deliver one Spanish interface with a list in folders and a detail
view, using the existing FastAPI, SQLite and React/Vite application.

Include all four screening outcomes, incomplete applications, the seven fields,
full conversation, saved summary, doubts and job questions. Keep screening outcome
and recruiter decision visibly separate:

| Screening outcome | UI label | Recruiter decision | UI label |
|---|---|---|---|
| `incomplete` | Incompleto | `unreviewed` | Sin revisar |
| `qualified` | Screening superado | `advance` | Avanza |
| `disqualified` | Descalificado | `rejected` | Descartado |
| `pending_review` | Pendiente de revisión | | |

No candidate editing, reopening, messaging, recruiter notes, bulk actions, search,
exports, analytics, scoring, assignment to recruiters or decision audit trail.
Authentication, permissions, Docker, CI, deployment, WhatsApp, voice, ATS, scheduling
and RAG remain outside scope. **Bonus review: add no further bonus in E4; proceed to
E5 after the required recruiter workflow is verified.** Do not change model selection,
prompts or screening rules. E3's recorded limits remain.

## Existing work and minimal implementation

- `storage.py` already supplies `load_application` and `set_recruiter_decision`.
  The latter updates only the decision and `updated_at`, in one transaction, without
  changing the candidate revision. Reuse it for open and closed applications.
- Add a list read to `storage.py`, filtering the existing outcome and decision
  columns with parameterized SQL. Read snapshots without loading each conversation.
  No new table, migration, ORM, repository layer or dependency is needed.
- Put the three HTTP operations in `main.py`, with explicit recruiter projections.
  Keep the existing candidate response allowlist unchanged. Recruiter operations
  work without provider credentials and never call the model or regenerate summaries.
- Serve the existing built frontend at `/recruiter` as well as `/`. Select the
  recruiter component by this exact path before mounting the candidate chat, so
  visiting it does not create an application or read/write the candidate session.
  Use a separate React component and existing CSS; no router or state library.
  Retain health, missing-build behaviour and JSON 404s for unknown API paths.
- This remains a local demo: recruiter routes expose internal data without
  authentication. Separate routes and projections are not access control. Document
  that limit in the README; do not present the application as production-ready.

## HTTP contract

| Operation | Endpoint | Contract |
|---|---|---|
| List | `GET /api/recruiter/applications` | 200 with `{items: [...]}`; optional `outcome` and `recruiter_decision` filters, combined with AND; omitted means all |
| Detail | `GET /api/recruiter/applications/{id}` | 200 with saved recruiter detail |
| Decide | `PATCH /api/recruiter/applications/{id}/decision` | Strict body `{recruiter_decision: "unreviewed" \| "advance" \| "rejected"}`; 200 with the saved `id` and `recruiter_decision` after commit |

The list returns all matches for this local demo, ordered by `created_at` descending
then `id` ascending for ties. No pagination or configurable sort in E4; record this
as a volume limit. Do not sort by `updated_at`, which also changes on a recruiter decision.

Each item contains `id`, `created_at`, `outcome`, `recruiter_decision`, `full_name`
(resolved value or null), `work_locations` (the saved field state) and
`resolved_fields` (count of fields with status `resolved`, out of seven). This count
shows collected data, not eligibility or a score; unresolved coverage can coexist
with a resolved location value.

Detail contains `id`, `created_at`, `updated_at`, `closed_at`, `outcome`,
`outcome_reasons`, `recruiter_decision`, all seven `fields` with their existing
states, `pending_questions`, `summary`, and `messages` with sequence, role, text
and timestamp. Read snapshot and ordered messages consistently through the existing
load operation. Do not expose turn diagnostics or add a second candidate data model.

Invalid filter/body values return 422; unknown detail/decision ids return 404;
storage failures return 503 and unexpected failures 500. Use stable codes and
Spanish recruiter notices without exception text or raw validation input. Reuse
existing error handling where it fits; do not expand the candidate recovery protocol.

Any of the three decisions may replace any other on any application, including an
incomplete or disqualified one. It records the recruiter's judgement, does not change
eligibility, stop screening or notify the candidate. Concurrent recruiter writes use
the existing last-committed-write behaviour; no locks in the UI, version protocol or
audit history are added. A candidate turn must retain a concurrently saved decision.

## Recruiter screen

**List.** A full-width table with a sticky header: Candidato, Zona de trabajo, Screening,
Decisión, Campos recogidos (“5/7”) and Fecha. It scrolls inside its area, horizontally on
narrow widths. **User decision, replacing the two filters:** a mail-style sidebar with counts,
one folder per recruiter decision: “Bandeja” (`unreviewed`, the default), “Avanzadas” and
“Descartadas”. A decided application leaves the inbox for its folder. The inbox orders by
outcome, then newest first: screening superado, pendiente de revisión, incompleto,
descalificado. An “Actualizar” button remains. The screen reads the whole list once and
filters it in the browser; the API filters remain, but the screen no longer filters by outcome.
Include unnamed and greeting-only applications as “Nombre no recogido”; never hide
them or infer that a person who opened the page has answered. “Todavía no hay candidaturas”
appears only when there are none at all; an empty folder has its own message. **Recorded
limit:** counts need the whole list, so at real volume they would have to come from the server.

**Detail.** Selecting a row opens its detail in a non-modal drawer of about 620 px from
the right, taking the full width below 760 px. The left of the table stays usable, so another
row can be picked with it open. “Cerrar” returns focus to the row, or to the list heading if
the row left the list. A click outside the drawer, other than on a row, closes it and leaves
focus on what was clicked. The conversation has a maximum height and scrolls inside its card,
and the summary shows its “Label: value” lines as label and value, without changing how it
is generated. The slide respects reduced motion. In order: name, area and date;
outcome, reasons and decision; internal summary; unresolved doubts and job questions, only
when present; the seven fields in process order; and the entire conversation in
sequence. Use Spanish labels and
readable category/place names; retain restrictions, approximate start wording and
raw evidence for unresolved fields. Make the stored raw answer available alongside
each collected value. **User decision, 2026-11-05:** the seven fields, with their raw answers,
now appear only when an application has no summary; otherwise the summary is the single view of
the collected data and the raw answers remain in the conversation. Distinguish “No recogido”, “Por aclarar”, “No”, zero experience
and explicitly unknown start timing; never render them all as an empty dash.

For incomplete applications, show saved progress and “Sin resumen: conversación
incompleta”; do not infer abandonment from age or generate a summary. Saved messages
can include a candidate turn still awaiting processing. Render text safely as text,
including summaries; no Markdown/HTML rendering is required. Technical field reason
codes get understandable labels, with a generic “Requiere revisión” fallback and
the raw evidence for reasons outside the known set.

**Decision.** A labelled select with no separate save action: choosing a value saves it. After
a failure it returns to the saved value, and choosing again retries. Ignore further choices while a request is in flight. Show success only after confirmation; then refresh the
list so the folders reflect the saved decision. If a response is lost, reload the
detail to reconcile the saved value before offering another save. Do not optimistically
claim persistence. A failed list refresh must not turn a confirmed save into a failure.

Keep selection on the current page; no detail URL or persisted folder state is
required. Keep the folder when returning from detail. Load on entry/selection and on
explicit refresh, with visible loading, error and retry states; no polling or realtime
updates. Discard obsolete responses when selection changes. Extend the
existing off-white, charcoal and terracotta style with readable desktop layout,
usable narrow widths, keyboard access and labels that do not rely on colour.

## Delivery and acceptance

Implement in three increments: storage/list and HTTP through backend TDD; recruiter
screen; browser verification and documentation. Observe relevant tests fail before
implementation. Use temporary SQLite and synthetic applications, without external
services. Reuse E2/E3 regression coverage rather than recreating their test matrices.

- [x] List tests cover empty data, each outcome, combined filters, deterministic
  ordering, unnamed/greeting-only records and progress counts.
- [x] HTTP tests cover list/detail projections, all seven fields, ordered history,
  saved summaries versus incomplete null summaries, invalid inputs, unknown ids and
  handled failures. The operations work without credentials.
- [x] Decision checks cover all allowed values and persistence after reload, open
  and closed applications, and unchanged fields/outcome/history/summary. Retain
  existing coverage that a decision does not invalidate a candidate turn and that
  subsequent candidate work preserves it. Candidate responses still omit internal data.
- [x] In the served browser, inspect a synthetic example of each outcome and a
  greeting-only record; filter, open detail, save/change a decision and reload to
  verify it. Check loading/error recovery, a failed save, narrow width and keyboard
  operation. Record actual evidence and any untested cases, not just screenshots.
- [x] Verify the path from a newly completed candidate screening to its appearance
  in the list, correct detail and durable recruiter decision. A fake model is enough
  for this integration check; real-model behaviour already has E3 evidence. No new
  paid eval battery is required for E4.
- [x] Run `python -m pytest` from `app/` and `npm run build` from `app/frontend/`.
  Record results here. Update roadmap, README (including `/recruiter` and local-demo
  limits) and AGENTS.md to match delivered work before declaring E4 complete.

E4 is done: the candidate-to-recruiter path works, the checks have evidence and the user
confirmed it. The filter wording above is met by the folders, with the API filters still tested.
Next is E5: clean-clone setup verification, final documentation, video and presentation.

## E4 evidence, 2026-11-05

**Checks.** `python -m pytest` from `app/`: 736 passed (685 existing, 51 new in
`tests/test_recruiter.py`). `npm run build` from `app/frontend/`: passed. TDD: the first
run of the new tests gave 49 failed and 2 passed; the 2 were already true (candidate
allowlist, JSON 404 on unknown paths). No real model or external service was called.

**Served browser.** Real Chrome against the built app, served by Uvicorn with a temporary
SQLite database and a fake model; the local `app/data/screening.db` was not touched. Seeded
with one synthetic application per closed outcome, a second disqualified one, a greeting-only
record and an open record with an unresolved name and a candidate turn awaiting processing.

- Read each record: labels, seven fields with raw answers, "No recogido", "Por aclarar: Ana",
  "No", "Sin experiencia (0 años)", "7 meses · Glovo", unknown start, "Malaga (FR)", the fallback
  "Requiere revisión", doubts, job questions, conversation order and "Sin resumen: conversación
  incompleta". HTML in a summary and a message rendered as text.
- Filters: each outcome, a decision, combined, and the no-match message. Six records stayed
  six after repeated visits, so `/recruiter` created no application.
- Decision: saved, changed and confirmed after a page reload. With a filter active, the
  row left the list while the detail and the filter stayed.
- Failures, with `fetch` replaced in the page: a 503 on save (attempted choice kept beside
  "Decisión guardada: X"); a lost response on a committed save (shown as saved after the
  reload); a save and reload both rejected (save disabled until a retry reconciled it); a
  failed list refresh after a confirmed save (save still reported, list recovered with retry);
  a failed detail with retry; loading states; obsolete detail and list responses discarded.
- With the server really stopped, "Actualizar" showed the error and retry.
- Keyboard, with real key presses: Tab to a row, Enter to open, Tab and arrows on the select,
  Enter to save. This found a bug, now fixed: disabling the button dropped focus to the
  page body; focus now moves to the decision box.
- Narrow width: a 390 px iframe of the page, because the window did not shrink. List and
  detail alternate, "Volver al listado" returns to the row, no horizontal overflow.
- Candidate to recruiter: a screening completed in the chat with the fake model appeared first
  in the list, with the right detail and summary, and its decision survived a reload. The
  fake model always returns the same fields, so that record is also named Ana Ruiz.
- Demo link added at the user's request: each header links to the other screen. Physical click
  from chat to panel, scripted click back; the same application reopened and the count stayed
  at one. In a 390 px iframe there is no overflow, and the chat header wraps onto two rows.

**Not checked.** Most clicks and filter changes in the browser were dispatched by script
rather than with a physical mouse. The empty-database message used a mocked response in the
browser (the empty API response has a real test). Saving with the server really stopped was not
exercised: the attempt was void because the select already matched the saved value, so this
path rests on the rejected-`fetch` case. The keyboard focus fix was confirmed through the 503
case, not by repeating the key presses. Not checked: a real narrow window or device, a screen
reader, colour contrast measurements, other browsers, or high volumes. There are no automated
frontend tests.

**UI refactor, on top of 9cd59e8; frontend only.** The first
browser evidence above describes the earlier side-by-side layout, and its failure checks
were repeated below only where stated. `python -m pytest`: 736 passed. `npm run build`: passed.
Real Chrome, built app, temporary SQLite database and fake model:

- Default load: outcome `qualified`, “Screening superado” pressed. Sticky header held while
  scrolling.
- Quick views: “Han avanzado” listed an advanced `pending_review` record; “Todas” listed
  all; a filter combination matching no view pressed none and showed the no-match message.
- Drawer: opened and switched rows with real mouse clicks, one drawer at a time; order, optional
  sections, “Por aclarar: Ana”, an unprocessed candidate turn and HTML shown as text.
- Decision: saved with a real click and kept after a page reload. Failed save (503 by replacing
  `fetch`): attempted choice kept beside “Decisión guardada: X”. Lost response on a committed
  save: shown as saved.
- Keyboard, real keys: quick view with Space, row with Enter, select, save, Cerrar. Focus goes to
  the drawer, stays in the decision box after saving, and fell back to the heading when the
  saved row left the filter. Return to the row was confirmed by script, not by key press.
- Narrow: a 390 px iframe, because the window does not shrink: full-width drawer, the table
  scrolls horizontally inside its area, no page overflow.
- Fonts: both reported loaded and accented text rendered in screenshots; no request to another
  host from the recruiter page. The chat in Spanish, its link, new application, not-found
  state and closed state were seen in the browser.
- Drawer fixes, real mouse clicks with a temporary database: a click inside keeps it open, a
  row switches, empty table space closes it, and a quick view closes it and still applies. A
  31-message conversation scrolls at 340 px. A summary in the saved format shows label and
  value, and free text stays whole. At 390 px (iframe, scripted click) the summary stacks and
  the page does not overflow. Only `npm run build` was rerun: no backend change.
- Saving on selection replaced “Guardar decisión”, and the folder sidebar replaced the filters
  and quick views, so earlier evidence that mentions those controls describes the previous
  screen. Arrow keys on a closed select can save each value they pass through.
- Final screen, temporary database, mostly scripted clicks: folder counts, an empty folder, inbox
  order with qualified and incomplete records, and a decision moving a record from “Bandeja” to
  “Avanzadas”. Saving from the select, a failed save and retry, and a reload that kept the decision
  were checked on the screen just before the sidebar, in a 390 px iframe no overflow on the final
  one. At closure: `python -m pytest` 736 passed, `npm run build` passed.

Not checked on the final screen: “Descartadas”, inbox order with pending or disqualified records,
a reload after moving a record, and closing the drawer with a real click on the sidebar.

Not checked in this refactor: the empty-database message, obsolete responses, list and detail
error states and the save-and-reload-both-fail case (logic unchanged, checked before the
refactor); actual reduced-motion behaviour (the rule is in the built CSS, the setting was not
on); the chat's pending, retry, reminder and confirmation states and its English copy (the diff
touches only its markup and classes); a real narrow window, a screen reader, measured
contrast and other browsers.

**Fonts.** Fraunces and DM Sans, Latin subset, in `app/frontend/src/fonts/` with their OFL
licences; 97,844 bytes of woff2, no npm dependency and no runtime request to Google.

**Limits.** Local demo: recruiter routes expose internal data without authentication. The list
returns all matches, without pagination. The last committed write wins and there is no decision
history. An open detail does not refresh by itself. A lone first name is listed as "Nombre no
recogido" and shown as "Por aclarar" in the detail. `stop_requested` is not shown, as planned.

**Moved from the README in E5.** Recruiter detail after limiting “Datos recogidos” to applications
without a summary: `npm run build` passed. In real Chrome, with a temporary database and no model
calls, a closed application shows the summary and conversation without that card, and an incomplete
one keeps it (1 of 7, all seven rows). No summary-failure case or narrow width was checked.

## Planning evidence

Inspected the current storage, field models, recruiter-decision tests and E3 closure
records on 2026-11-05. E3 records 685 passing backend tests and a successful frontend
build; those checks were not rerun for this documentation-only change and are not E4
acceptance evidence. Existing E3 working-tree changes are outside this planning edit.
