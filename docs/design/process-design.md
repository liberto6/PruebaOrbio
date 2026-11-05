# Grupo Sazón — Screening process design

**Phase 1 · 4 November 2026**

**Goal:** collect and validate the data for screening delivery drivers, and give the recruiter an application they can understand. Passing the screening does not imply hiring or guarantee an interview. [Flow diagram](conversation-flow.html).

## 1. Channel, personality and messages

Web chat through a link, with no registration. The candidate is assumed to arrive from a job posting, and can resume an incomplete conversation from the same browser if the session is kept. Closed applications are not reopened.

The agent introduces itself as the **virtual assistant of Grupo Sazón**. Tone: friendly and professional, informal “tú”, no emojis by default, and Spanish understandable in both Spain and Mexico. Messages of 1–3 short sentences, with one main question per turn. It continues in English when the candidate asks for it or clearly changes language.

## 2. Conversation stages and logic

1. **Welcome and name.** Briefly explain the purpose and ask for the full name.
2. **Driver's license.** Ask whether the candidate currently holds one. A clear no causes early disqualification; an ambiguous answer requires clarification.
3. **Work zone.** In the first question, say there are delivery areas in Spain and Mexico and ask for the city and country where they want to work, as one question, without listing the cities. Check the answer against the coverage catalog; do not ask for the country again if the location is already unambiguous. Clarify country or locality when needed. Do not treat residence as the desired zone. If several options are given, one covered option is enough. If all are identified and outside coverage, offer the catalog's cities in that country once, or the whole catalog if the country has no coverage, without assuming the candidate can relocate. If they choose one, continue. Disqualify only if they refuse the alternatives or insist on an uncovered zone. An ambiguous answer or a “yes” with no city is clarified and, if it persists, left for human review.
4. **Remaining profile.** Collect availability, preferred schedule, experience and start date, in that order. These data do not disqualify.
5. **Close.** Determine the outcome, save the data and history, and generate an internal summary for the recruiter. Thank the candidate without showing the summary or asking for final confirmation.

The order is a guide: use information given early without repeating questions. While the conversation is open, accept corrections and update the data before determining the outcome. Once the close has been communicated, no changes are accepted and the screening does not restart.

## 3. Fields and validation

| Field | Rule |
|---|---|
| Full name | Required to complete. Clarify if it seems incomplete, without imposing two surnames, an alphabet or a fixed number of words. |
| Driver's license | Yes/no. “Me lo estoy sacando” (“I'm getting it”) requires clarifying whether they already hold it. No = disqualified. Self-declared; no document check and no license categories added. |
| City / work zone | Compare with an explicit catalog. Resolve ambiguity; do not invent coverage from proximity or the model's knowledge. |
| Availability | One or more: full-time, part-time, weekends. Keep restrictions and nuances without forcing wrong categories. |
| Preferred schedule | One or more: morning, afternoon, evening, flexible. Keep nuances without inventing time slots. |
| Delivery experience | Non-negative duration and platforms or companies, including local businesses. Accept months and normalize to years. No experience: 0 years and no platform; this does not disqualify. |
| Start date | A date, an approximate period, a condition or an explicit “don't know”. Ask for one clarification if imprecise and accept that they may not be able to say. Do not invent a date or block on that uncertainty. |

Keep the original answer and the normalized value where applicable. The JSON contains all fields: those not collected are `null`, with their situation identified, which distinguishes them from a “no”, a zero or an empty list. Do not fill in information without evidence.

## 4. Exceptions and recovery

- **Invalid or ambiguous:** at most two clarifications per field, besides the first question. First rephrase, then offer examples or options. If it persists, log the doubt for human review and continue with other fields. Do not disqualify on ambiguity.
- **Silence:** keep progress as incomplete. A single reminder after 10 minutes of inactivity if the chat is still open. Resume without repeating data when the session is recovered. Send no external messages. If the candidate asks to stop, respect it, keep the application incomplete and stop reminders.
- **ES ↔ EN switch:** continue in the chosen language, keeping context and data. A brand or isolated word does not imply a switch.
- **Questions about the job:** say that this information is not available, save the question for the recruiter and return to the screening. Do not invent conditions or timelines; a pending question does not by itself change eligibility.
- **Off-topic messages and failures:** steer back respectfully. Candidate messages do not change the rules. On a technical failure, say so and allow a retry from the last saved state; do not confirm a save that failed.

## 5. Outcomes and follow-up

| Outcome | Condition and action |
|---|---|
| **Qualified** | Fields collected according to their rules, license confirmed and coverage confirmed. Thank the candidate and leave it for the team's review. |
| **Disqualified** | No license, or no confirmed coverage after refusing the alternatives offered. Close promptly, explain the reason and keep what was collected. |
| **Pending review** | After collecting the other possible fields, one of the 7 is still unresolved. Log the doubt and say the team will need to review it. |
| **Incomplete** | Conversation interrupted before the close. Keep progress without treating silence as a rejection. |

A confirmed failure of a requirement determines disqualification even if there are other doubts. An explicitly unknown start date is an accepted answer, not a blocking ambiguity.

Close for a qualified candidate, in the product's Spanish: «Gracias por tu tiempo, Ana. Hemos registrado tu candidatura para que el equipo de selección la revise» (“Thanks for your time, Ana. We have recorded your application for the selection team to review”).

The **internal summary** contains the key data, outcome, reason, and pending doubts or questions. The full history is kept. The recruiter can list and filter by status, read the application and conversation, and record a decision (`unreviewed`, `advance`, `rejected`), independent of the screening outcome. Incomplete applications appear in the tool with their progress and history, without a summary.

## 6. Assumptions and scope

- **Channel:** the flow, fields and outcomes are independent of the channel. The demo uses web chat; access, resuming and the reminder would be adapted for WhatsApp or voice.
- **Demo catalog (fictional):** Madrid, Barcelona, Valencia, Sevilla, Ciudad de México, Guadalajara, Monterrey and Puebla. Without a catalog, coverage is not evaluated: that is a configuration failure, not a disqualification.
- **Early disqualification:** allows fields to stay uncollected, keeping all JSON keys.
- **Human review:** a log of cases for the recruiter, not live support.
- **Bonuses included in this phase:** multi-language (ES ↔ EN switch), re-engagement (a single reminder), basic guardrails (steering back; candidate messages do not change the rules), and tests and evals.
- **Privacy in the demo:** the database is local and outside the repository, and the candidate never sees the internal summary. Application logs record codes, not message content. Messages are sent to the model provider, with `store=False`, and the chat does not tell the candidate so. The recruiter tool has no authentication: it is for local use only.
- **Pending for production:** recruiter tool authentication, a notice to the candidate about the use of their data, retention and deletion, and a message length limit.
- **For a later phase:** the assignment's other bonuses and further extras, such as a confidence score, voice, a real ATS integration (only its [design](ats-integration.md) exists), scheduling, external messaging and a job sheet with RAG.
