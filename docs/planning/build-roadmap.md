# Build Roadmap

High-level plan for Phase 2. Each stage is detailed only when it starts. Source of truth: [assignment](../assignment.html), then [process design](../design/process-design.md).

## Scope

**In:**
- Screening agent following the Phase 1 design, collecting the 7 fields.
- Recruiter tool with the functions set in Phase 1.
- Core requirements and deliverables from the assignment, plus the live presentation.
- Bonus: ES/EN, one reminder, basic guardrails, tests and evals.

**Out:** login and permissions, WhatsApp, real deployment. Voice and other bonus features are reviewed after E4, defaulting to no. The demo runs locally, and the README and presentation say so.

## Decisions so far

- The LLM interprets and writes; code validates and decides the outcome.
- FastAPI and SQLite; minimal React with Vite, built and served by FastAPI.
- TDD on backend logic.
- Code, README and commits in English. Phase 1 document and recruiter tool in Spanish.

## Stages

| Stage | Goal | Includes | Done when | Decide when starting |
|---|---|---|---|---|
| **E1. Foundations** | Set the frame before writing logic | Architecture, tools, repository | Decisions recorded, repository created, empty app starts, sample test passes | Structure and conventions |
| **E2. Rules and persistence** | Code validates and decides without an LLM | Field rules, coverage catalog, clarification limit, 4 outcomes, storage | Tests cover every Phase 1 rule and outcome; saved JSON keeps all keys | Data model and JSON shape |
| **E3. Conversational agent** | Natural conversation from the web chat | LLM, extraction, re-asking, context, summary, failures, ES/EN, guardrails, reminder, resume, evals | A full conversation works in the browser; Phase 1 scenarios reach the expected outcome | LLM model (compare as soon as interpretation works), prompts, scenarios |
| **E4. Recruiter tool** | Recruiters work with the agent's output | List and status filter, detail with conversation and summary, incomplete candidates, recruiter decision | Full path from candidate to recruiter decision; bonus review done | Screens and listing data |
| **E5. Delivery** | Ready to submit and defend | Code freeze, README, sample conversations, video, presentation script | Clean-clone install works from the README; 5-10 min video recorded; live demo rehearsed | Public or private repository, demo script, fallback if the LLM fails |

Order is linear. E4 may start alongside E3 with sample data once E2 is done.

## Current state — 2026-11-05

**E1, E2 and E3 are complete; the E4 brief is written and implementation is pending.**
A candidate can complete a
screening in the browser with the real model (`gpt-6-luna`), reload, retry, stop and resume, and
gets one reminder after 10 minutes of inactivity; closed applications are read-only. No recruiter
tool exists yet. `python -m pytest` reports 685 passed. Real-model evals are in `app/evals/`,
their results and the [sample conversations](../evals/sample-conversations.md) in `docs/evals/`.
See [E3 plan and evidence](e3-conversational-agent.md),
[E2 evidence](e2-rules-and-persistence.md), [E1 evidence](e1-foundations.md) and
[README](../../README.md).

The roadmap's "every Phase 1 rule" covers E2's deterministic rules only; E3 must
prove the conversational behaviour through its scenarios.

Next: implement the [E4 recruiter tool](e4-recruiter-tool.md): list and filters,
detail and independent recruiter decision. No additional bonus is included; E5 follows E4.
The user confirmed E3 complete and [decided](e3-conversational-agent.md#product-decision-a-bare-yes-after-the-reminder)
that a bare "Sí" after the reminder does not count as a licence.
[E3.4 evidence and limits](e3-conversational-agent.md#e34-evidence-2026-11-05)
and the product decisions validated by the user are in the plan.

## If time runs short

Cut in this order:
1. Extra bonus features and additional model-comparison runs beyond the initial shortlist.
2. Recruiter tool visual polish.
3. Eval breadth, keeping critical scenarios.
4. Same-sentence code-switching.
5. The reminder, declared as a deviation from Phase 1.

Never cut: core requirements, fidelity to Phase 1, the two expected products, the deliverables, the live demo, or critical model checks and the recorded rationale for the chosen model.

## Risks

1. **Misread answers cause false rejections.** Code decides from what the LLM extracts, so evals prioritize license, coverage and ambiguous answers.
2. **The live demo fails.** Mitigated by the clean-clone test, rehearsal and the video as backup.
3. **Being unable to defend AI-written code.** Review and understand each stage before closing it.
