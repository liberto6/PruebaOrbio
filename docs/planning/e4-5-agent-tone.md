# E4.5 — Agent tone and conversational continuity

**Status: closed by user decision with known limits, 2026-11-05. After the user's simpler
acknowledgement rule, two of the five dialogues still fail the revised criteria; the user chose to
leave the agent as it is ([final cycle](#final-cycle-the-users-acknowledgement-rule),
[decision](#user-decision-closed-with-known-limits)).**
First improvement after the completed E4, before E5. Sources: [process design](../design/process-design.md),
[roadmap](build-roadmap.md) and [E3 conversations](../evals/sample-conversations.md).
This brief authorizes no additional bonus or general agent redesign.

## Goal

Make replies more attentive without lengthening the screening: briefly acknowledge
relevant answers and corrections, clarify tactfully and continue with the action
chosen by Python. Simple answers may lead directly to the next question.

Avoid repeated thanks, automatic “Perfecto”, excessive use of the candidate's name,
profile praise and hiring expectations. Keep 1–3 short sentences and one main
question, with no extra confirmation turns. Acknowledging a contribution is different
from confirming a fact: only saved data supports factual confirmations.

**User decision, 2026-11-05, which changes this Goal.** Add before the question a single phrase of
understanding of what was received: "no hace falta reafirmar dato, puede ser tambien una muletilla
como perfecto, o esta bien saberlo lo que sea, no sesgar tanto al agente". This replaces "avoid
automatic Perfecto" and "simple answers may lead directly" above. A generic acknowledgement is
acceptable, including on a correction or nuance, and restating data is not required.

## Implementation scope

1. Update `REPLY_PROMPT` in `app/backend/llm.py` with these concrete behaviours and
   4–6 short, varied examples across Spanish and English. Include a direct question,
   correction, clarification and respectful rejection. Examples illustrate style;
   they must not override the selected action or field-specific rules.
2. Extend `_reply_input` with `saved_fields` containing each of the seven fields'
   `status` and `normalized` value, and the saved `pending_questions`. Reuse
   `TurnContext`; no new persisted state or model is required. Keep the existing
   current-field evidence and recent messages. Treat all candidate content as data.
3. Establish precedence in the writing instructions: `action` determines what to
   ask/do; saved data determines what may be asserted; recent messages provide
   conversational continuity. Do not independently reinterpret answers, claim an
   unresolved field is settled, or say a job question was recorded unless stored.

Expected changes: `llm.py`, one focused test in `app/tests/test_llm.py`, saved eval
results and documentation. Leave extraction, summary generation, screening rules,
coordination, persistence, HTTP/frontend, model choice, greeting and reminder intact.
No new dependencies, model calls per turn, sentiment classifier, phrase-selection
engine or separate rewriting step.

## Bounded verification

### One technical test and the existing suite

Through the existing fake-client test setup, assert that the reply request carries
the seven saved field states/values and pending questions. Include resolved false,
missing/unresolved null and a corrected value to guard against misleading context.
Observe the test fail before implementing the input change, then pass. Do not test
literal prompt wording or attempt to prove naturalness with a mocked model.

Run `python -m pytest` from `app/` after implementation. No frontend build or browser
matrix is required because this change does not touch the UI or HTTP behaviour.

### Five real-model conversations, once each

Reuse these existing scenarios, with the new prompt and `--repeat 1`:

| Scenario | Main manual check |
|---|---|
| `qualified_in_order_with_name_correction` | Short answers, a correction, natural transitions and no repetitive acknowledgements |
| `guadalajara_unresolved_reaches_review` | Tactful clarification under frustration; uncertainty remains explicit |
| `no_license` | Respectful early rejection with the correct reason and no contradictory praise |
| `stop_job_question_then_explicit_continuation` | Job question, stop/resume and continuity without unnecessary questions |
| `switch_to_english_all_the_way_to_close` | Equivalent concise style in English |

From `app/`, use the existing runner without changing it:

```powershell
python -m evals.run --only qualified_in_order_with_name_correction,guadalajara_unresolved_reaches_review,no_license,stop_job_question_then_explicit_continuation,switch_to_english_all_the_way_to_close --repeat 1
```

Use the configured model, synthetic data and the remaining documented 5 USD model
spending cap; check recorded spend before running. Do not run the full battery,
held-out sets, repeat the old prompt or add scenarios. Existing saved conversations
are qualitative references, not a controlled before/after experiment.

Read all five new dialogues and check:

- Attentive wording without formulaic repetition or inappropriate praise.
- 1–3 short sentences and the intended question/action; counting question marks
  alone does not establish that the right question was asked.
- Factual acknowledgements consistent with saved data; no invented conditions or
  false confirmations. Where needed, trace the scripted answers and existing turn
  evidence manually; do not build a new state-tracing system.
- Expected screening result and clarification counts, with no unnecessary turns.

Keep the existing automated scenario checks. Record the result file, its prompt hash,
cost/latency from the runner and a short manual conclusion for each scenario here.
Keep failures; distinguish provider failures, scorer limitations and actual dialogue
defects. No numerical style score, LLM judge, new report format or separate audit.

### Stop rule

If all five meet these criteria and the suite passes, close this improvement.
After a targeted fix, rerun affected cases only; after changing general tone
instructions, rerun all five once. A provider failure needs a successful replacement
run of that case, not a full restart. Preserve both results.

Do not weaken expectations to pass. A functional defect outside this scope must be
recorded with its effect on acceptance and left for an explicit scope decision;
do not silently expand this task into a general review. This small acceptance sample
does not demonstrate statistical reliability or increased screening completion.

## Closure

- [x] Writing instructions/examples and saved reply context implemented.
- [x] Focused test observed failing then passing; existing backend suite passes.
- [x] Five real-model conversations reviewed, with results and any reruns linked.
- [ ] No observed new factual/flow defects; wording improves while staying concise.
  **Not met:** see the [final cycle](#final-cycle-the-users-acknowledgement-rule). The user accepted
  these as [known limits](#user-decision-closed-with-known-limits).
- [x] Brief, roadmap, README and AGENTS.md reflect the delivered change and limits.

## E4.5 evidence, 2026-11-05

**Test.** Before implementing, `test_reply_receives_every_saved_field_state_and_the_pending_questions`
failed with `KeyError: 'saved_fields'` (1 failed, 54 deselected). It then passed, and
`python -m pytest` reports 737 passed with the final prompt. It covers resolved false, an
unresolved and a missing null, and a corrected value.

**Command.** The command above selects six scenarios, not five: `--only` matches substrings, so
`no_license` also selects `instruction_to_ignore_rules_does_not_save_no_license`, and no `--only`
value can separate them. To stay within the five authorized conversations, both runs called the
unchanged runner's `main()` with the same arguments, removing that one scenario in memory:

```powershell
python -c "import evals.run as r; r.SCENARIOS = [s for s in r.SCENARIOS if s.name != 'instruction_to_ignore_rules_does_not_save_no_license']; r.main()" --only <the same five names> --repeat 1
```

**Results.** Model `gpt-6-luna`, synthetic data, no provider failures, none stopped by budget.

| Result | Prompt hash | Runner | Cost | Turn latency (median / max) | Reply call (mean) |
|---|---|---|---|---|---|
| [`135515Z`](../evals/e3-2-20261105T135515Z.json), first prompt | `3fff458bbccf61ad` | 5/5 | 0.0099 USD | 4.07 s / 8.80 s | 1.43 s |
| [`135822Z`](../evals/e3-2-20261105T135822Z.json), current prompt | `eb87c2166148cfc1` | 5/5 | 0.0090 USD | 3.99 s / 5.99 s | 1.45 s |

The rerun of all five follows the stop rule, because the fix changed general tone instructions.
Both results are kept. The ledger went from 1.0966 to 1.1154 USD, or about 1.12 USD of the 5 USD
cap with the unmetered browser calls: an estimate, not a bill. The runner has its own lower limit
of 2 USD, which was not reached.

**Manual review of `135822Z`.** Outcomes, saved fields and clarification counts are as expected in
all five, with no extra turns and no false confirmation, invented condition or praise.

| Scenario | Verdict | Conclusion |
|---|---|---|
| `qualified_in_order_with_name_correction` | **Fail** | "Por cierto, mi nombre completo es Pedro Sanz Molina" gets only "¿En qué ciudad te gustaría trabajar?". The nuance "menos los martes por la tarde" is not acknowledged either. No reply carries an acknowledgement or transition: it reads as a list of questions |
| `guadalajara_unresolved_reaches_review` | Pass, with a wording defect | Tactful, and the doubt stays explicit: "la ciudad queda pendiente de aclarar", and in the close "queda por aclarar la ciudad". But "Entiendo que… frustrante repetirlo" opens two consecutive replies, and "Gracias por aclararlo" answers a message that clarified nothing |
| `no_license` | Pass | "Para este puesto hace falta tener carnet de conducir ahora mismo, por lo que tu candidatura no puede seguir adelante." Correct reason, respectful, no praise. It follows the prompt's example closely, so it says little about variety |
| `stop_job_question_then_explicit_continuation` | **Fail** | The job question is now handled on its turn: "No tengo ese dato, pero lo dejo anotado para el equipo." But the close raises it again with no prompting: "No tengo el dato del sueldo mensual; lo dejo anotado para el equipo." The name appears outside the close ("Entendido, Luis", "Gracias por retomar la candidatura, Luis") |
| `switch_to_english_all_the_way_to_close` | Pass | Same concise style, and the switch is acknowledged: "Of course, we can continue in English." The other answers are simple and lead straight to the question, which the brief allows, though it is plain |

**Actual dialogue defects, all in `REPLY_PROMPT` wording behaviour:**

1. A correction made in passing next to another answer is not acknowledged, in either run.
2. Without a job question in the last message, an earlier one can still come up: at the close in
   `135822Z`, and after "En Madrid" in `135515Z`. In `135515Z` the question also went unanswered on
   its own turn; the current prompt fixed that part.
3. The name limit and the rule against repeating an opening are not reliably followed.

The first prompt also added the approximate-period hint to two initial `start_timing` questions and
copied its rejection example, whose name matched the `no_license` scenario. Both were fixed for the
rerun, and that name is no longer in the prompt.

Why instructions are ignored was not investigated. Replies use `WRITING_EFFORT = "none"`, and each
added rule has so far shifted the result rather than removing the problem. That this setting is
the cause is an untested hypothesis.

**Scorer limitation.** The runner reported 5/5 in both runs, with all these defects present: its
checks do not cover acknowledgement, repetition or when a job question is addressed. Its pass does
not establish the brief's criteria.

**Limits.** Provider failures: none. No full battery, held-out set or new scenario was run. This
small sample does not show statistical reliability or increased completion, and each run is one
sample of a variable model output. The stop rule's single autonomous fix cycle ended here; the next
run came only after the user's decision.

## Final cycle: the user's acknowledgement rule

**Prompt change.** On ask and clarify, the reply opens with one brief phrase of free wording, then
the question. The ban on "Perfecto", the conditional acknowledgement list, the rule about two plain
questions and the literal phrases are gone. The tone section is shorter and the examples went from
six to five. A close must not mention a job question unless the last message asks one. The
precedence, the guards and the field guide stay. The test did not need changes, and
`python -m pytest` reports 737 passed.

**Result.** Same five scenarios and same in-memory exclusion. No provider failures.

| Result | Prompt hash | Runner | Cost | Turn latency (median / max) | Reply call (mean) |
|---|---|---|---|---|---|
| [`141149Z`](../evals/e3-2-20261105T141149Z.json) | `0a7c78b40663a30b` | 5/5 | 0.0092 USD | 4.27 s / 10.50 s | 1.40 s |

The ledger is 1.1246 USD, so all of E4.5 cost 0.0280 USD. With the unmetered browser calls that is
about 1.13 USD of the 5 USD cap: an estimate, not a bill. As agreed, this was the only cycle under
this rule, and nothing was fixed or rerun afterwards.

**Manual review against the revised criteria.** Outcomes, saved fields and clarification counts are
as expected in all five, with no extra turns, false confirmation, invented condition or praise.

| Scenario | Verdict | Conclusion |
|---|---|---|
| `qualified_in_order_with_name_correction` | **Fail** | The correction gets no acknowledgement at all, generic or otherwise: "Por cierto, mi nombre completo es Pedro Sanz Molina" leads to "¿En qué ciudad te gustaría trabajar?". Later replies do have one: "Perfecto. ¿Qué horario prefieres…?". "Gracias, Pedro" also appears before the close |
| `guadalajara_unresolved_reaches_review` | Pass, with a defect | The doubt stays explicit: "esa ubicación queda pendiente de aclarar para el equipo de selección". But the second clarification drops the Spain or Mexico question and lists all catalog cities. The field guide's attempt 2 rule allows the list, but it cannot settle this doubt |
| `no_license` | Pass | "Para este puesto hace falta tener carnet de conducir ahora mismo, así que tu candidatura no puede seguir adelante." Its one question, after the name, has no acknowledgement |
| `stop_job_question_then_explicit_continuation` | **Fail** (strict) | The job question is mentioned twice when not asked. After "En Madrid": "No tengo ese dato, pero dejo tu consulta anotada para el equipo." And at the close: "No tengo el dato del pago mensual y lo dejo anotado para el equipo." The retouch did not stop it. The name also appears before the close |
| `switch_to_english_all_the_way_to_close` | Pass | "Good to know. Are you looking for full-time, part-time, or weekend work?" Every question has an acknowledgement and the facts are right |

**Openers.** Replies that ask something, with an acknowledgement, and the distinct openers used:

| Scenario | Acknowledged | Distinct | Openers |
|---|---|---|---|
| `qualified_…` | 4 of 6 | 2 | none ×2, "Perfecto" ×3, "Gracias, Pedro" |
| `no_license` | 0 of 1 | 0 | none |
| `guadalajara_…` | 8 of 8 | 4 | "Gracias, Luis", "Perfecto" ×4, "Está bien saberlo", "Entiendo…" ×2 |
| `stop_…` | 5 of 8 | 3 | "Entiendo", "Perfecto" ×3, "Está bien saberlo"; two with none, and one opening with the misplaced job answer |
| `switch_…` | 6 of 6 | 3 | "Of course" ×2, "Good to know" ×3, "Thanks for sharing that" |

In all, 23 of 29. Three identical openers in a row appear in three dialogues ("Perfecto" twice in
Spanish, "Good to know" in English), and in English "Of course" after "Yes, I have a licence" reads
slightly off. Over seven questions the middle stretch sounds somewhat mechanical but not wrong, and
it reads warmer than the bare questions of `135822Z`. Whether that is acceptable is the user's call.

**Defects.**

1. Job question mentioned when not asked. This is the blocking one, and neither prompt retouch
   removed it. It is not new to E4.5: the same misplaced mention appears in 3 of the 15 saved E3 runs
   of this scenario, before `pending_questions` reached the reply. It appeared in all 3 E4.5 runs, a
   sample too small to say the rate rose. The conversation state and the summary stay correct, and
   the question is still saved once for the team. The candidate sees a confusing answer.
2. The acknowledgement rule is not always followed (6 of 29 questions have none), including the
   correction turn.
3. The second Guadalajara clarification can lose the Spain or Mexico question.

The runner reported 5/5 with all of these present, for the same scorer limitation as before.

## User decision: closed with known limits

On 2026-11-05 the user decided to leave E4.5 as it is and not change the agent further for now
("e4.5 lo dejamos tal como esta, no tocamos mas del agente ahora mismo"). The criteria were not met
and were not weakened: the three defects above stay as known limits of the delivered prompt. A
candidate can see a job question answered when not asked, a correction can go unacknowledged, and
the second Guadalajara clarification can lose the Spain or Mexico question. Screening outcomes and
saved data were as expected in all five conversations. Neither `WRITING_EFFORT = "low"` nor a code
change for the job question was tried, so those remain untested options.

## Scoped follow-up: work location question

The user separately authorized a wording change on 2026-11-05: introduce the initial
work-location question with delivery areas in Spain and Mexico, then ask for the
desired city and country together. Example: “Tenemos zonas de reparto en España y
México. ¿En qué ciudad y país te gustaría trabajar?”. This gives context without
listing the eight cities or claiming nationwide coverage.

Implemented only in the reply field guide and its location example; the process
design now records the wording policy. No residence question, country step or new
field was added. Unambiguous cities need no repeated country answer; Guadalajara
ambiguity, coverage, early rejection and the city list on the second clarification
retain their existing rules. Information volunteered ahead of time still skips
satisfied fields.

This does not resolve the tone defects above. No paid model run was made;
the earlier runs do not validate this revised prompt. No new test was added for
literal prompt wording: this change adds no executable backend logic, and mocked
outputs cannot establish the resulting phrasing. `python -m pytest` from `app/`:
737 passed in 30.47 s, with no network or real LLM. No frontend/build changes.

## Scoped follow-up: covered cities before a coverage rejection

The user authorized this on 2026-11-05 after a candidate wanting Málaga was rejected without ever
hearing which cities exist, following the wording above. It changes the early rejection rule; the
coverage evaluation and catalog do not change.

**Behaviour.** When coverage is the only rejection reason, `next_action` returns
`offer_alternatives` once instead of `close`, with the catalog cities of the wanted country, or the
whole catalog if that country has none. A missing licence still closes directly. Saving the offer
sets `location_alternatives_offered` and holds `work_locations` as unresolved in the same
transaction, without counting a clarification. So a job question, a stop or a reload cannot reject
with the earlier place.

| Reply to the offer | Result |
|---|---|
| A covered city | Screening continues |
| Explicit refusal, or insisting on the place | The interpretation restores the place, then closes as `location_not_covered` |
| Another uncovered place | Closes; no second offer |
| “Sí” without a city, vague reply or only a job question | Clarified; when the budget runs out, it reaches `pending_review` |

If the model cannot restore the refused place, the failure is a clarification and then human review,
not a false rejection. No endpoint, table, frontend change, dependency or extra model call per turn;
the conversation gains one turn when the offer is made.

**Verification.** New tests were observed failing at collection (the action did not exist), then
`python -m pytest` reports 753 passed. Two existing tests that fixed the old rule were updated, and
existing rejection scenarios gained a reply to the offer. Five `alternatives_*` scenarios were added.
Result [`150448Z`](../evals/e3-2-20261105T150448Z.json), prompt hash `9029d80b3d741d81`: nine
scenarios once each, runner 9/9, 0.0099 USD, turn latency 3.93 s median and 7.55 s maximum, no
provider failures. The ledger is 1.1344 USD against the runner's 2 USD limit. This run also exercises
the location wording above, which had no real-model run.

All nine dialogues were read. The offer names only catalog cities, none closes on the offer turn,
and saved places and outcomes are as expected. Remaining wording defects, left as they are:

- The English close adds an unrequested next step: “You’re welcome to return and apply again if your
  preferred work location changes.” The prompt already forbids this.
- “Perfecto, Bilbao no está entre las zonas disponibles” is an awkward use of the free acknowledgement.

**Limits.** One run per scenario shows no statistical reliability. No full battery or held-out set
was run, so the updated held-out scenarios are unexercised. No Mexican uncovered place was run with
the real model; automated tests cover the country selection. In the flow diagram only the side-panel
text was updated: the compiled graph has no separate offer node.
