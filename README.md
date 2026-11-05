# Grupo Sazón

Local candidate screening and recruiter tool. **A candidate can complete a screening in
the web chat with the real model, and a recruiter can review it and record a decision.**

Read the [assignment](docs/assignment.html), [process design](docs/design/process-design.md),
[roadmap](docs/planning/build-roadmap.md), [E1 checklist](docs/planning/e1-foundations.md),
[E2 plan](docs/planning/e2-rules-and-persistence.md),
[E3 plan](docs/planning/e3-conversational-agent.md)
and [E4 plan](docs/planning/e4-recruiter-tool.md).
[E4.5 — Agent tone](docs/planning/e4-5-agent-tone.md) is closed by user decision with
known limits. The active brief is
[E4.6 — ATS integration design](docs/planning/e4-6-ats-integration-design.md), before E5.

## Windows setup (PowerShell)

Start from the repository root; the first command enters `app/`, where all Python
commands below run. Available local versions were Python 3.13.7,
Node.js 22.17.0, npm 10.9.2 and Git 2.50.0.windows.2.
Setup was verified with a newly created virtual environment and npm ci.

```powershell
Set-Location app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
python -m pytest
python -m uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000 for the candidate chat and
http://127.0.0.1:8000/recruiter for the recruiter tool. In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expected JSON: `{"status":"ok"}`. Stop Uvicorn with Ctrl+C.
If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of
`python` in the pip, pytest and Uvicorn commands; activation is optional.
Install only runtime dependencies with `python -m pip install -r requirements.txt`.

## Architecture

Application code, tests and dependency files live in `app/`; design and planning
live in `docs/`. Agent instructions and this README remain at the repository root.

`app/backend/main.py` contains one FastAPI app. `/api/health` works independently of
the frontend build. `POST /api/applications`, `GET /api/applications/{id}` and
`POST /api/applications/{id}/messages` drive the chat and return only public state: messages,
language, closure, any pending turn and `reminder_due_at`. Resending the same `request_id` and
text is the retry. `POST /api/applications/{id}/reminder` saves the single fixed reminder if the
server finds it due after 10 minutes of inactivity, and otherwise changes nothing; the open
page holds one timeout for it, so nothing is sent while the browser is closed.
`GET /api/recruiter/applications` (optional `outcome` and `recruiter_decision` filters),
`GET /api/recruiter/applications/{id}` and `PATCH /api/recruiter/applications/{id}/decision`
serve the recruiter tool with their own projections. They need no provider credentials, never
call the model, and a decision changes nothing else in the application.
After building, FastAPI serves `app/frontend/dist/index.html` at
`/` and `/recruiter`, and the generated assets at `/assets`. Restart Uvicorn after creating the build.
Unknown API paths return JSON 404; there is no frontend catch-all.
Without a build, startup logs the build commands and `/` and `/recruiter` return an actionable
JSON 503 while the API stays available.

The frontend is one React build with two screens, in plain JavaScript and CSS: the chat, and
`Recruiter.jsx` when the path is exactly `/recruiter`, which never creates an application or
reads the candidate session. It shows a table with a sidebar of folders by recruiter decision
(Bandeja, Avanzadas, Descartadas), and a choice in the detail saves at once. It reads the whole
list and filters in the browser. A plain link in each header switches between the screens for the demo.
Fraunces and DM Sans are bundled from `app/frontend/src/fonts/` under the OFL, so nothing is
requested from Google. There is no router and no
separate frontend development server or CORS setup. The browser keeps the application id
and any unconfirmed request in `localStorage`; SQLite is authoritative for the history.
Opening the page without a saved id creates an application.

`app/backend/rules.py` holds the strict Pydantic candidate model, field validation,
coverage catalog, clarification limit and outcome evaluation. `app/backend/storage.py`
persists applications and messages in SQLite through standard-library `sqlite3`, with
each operation in one transaction. The database defaults to `app/data/screening.db`, created
at startup and ignored by Git; `SCREENING_DB` overrides it, with relative paths resolved
against `app/`. Python validates and determines outcomes while the LLM interprets and writes.
`app/backend/conversation.py` chooses the next action and advances each candidate turn
from saved state. `app/backend/llm.py` holds the model contract, prompts and OpenAI
adapter. The model picks covered places from a closed list and reports others as stated;
Python compares them with the catalog.

**This is a local demo, not production-ready.** There is no authentication or deployment
infrastructure: anyone who can reach the server can read candidate data and change decisions at
`/recruiter`. Separate routes and projections are not access control. The recruiter list returns
all matches, without pagination.

## Model and evals

The model is OpenAI `gpt-6-luna`: economical, with strict structured output. Configure it
by copying `app/.env.example` to `app/.env` and setting `OPENAI_API_KEY`; `SCREENING_MODEL`
is optional. `.env` is ignored by Git. Pytest needs no credentials and makes no network calls.
Without a key the app still starts and saves messages; the chat says the service is unavailable.
**Each chat message costs money**, because it calls the real model.

From `app/`, these commands run scripted conversations against the real model. **Each run
costs money** (a full battery was about 0.09 USD) and saves its results in `docs/evals/`:

```powershell
python -m evals.run
python -m evals.run --held-out --repeat 3
python -m evals.run --only guadalajara,no_license --repeat 3
```

Latest results, the rationale for the model and the known limits are in the
[E3 plan](docs/planning/e3-conversational-agent.md). Real transcripts are in the
[sample conversations](docs/evals/sample-conversations.md).

## Verification recorded on 2026-11-05

- Recruiter detail after limiting “Datos recogidos” to applications without a summary: `npm run build`
  passed. In real Chrome, with a temporary database and no model calls, a closed application shows
  the summary and conversation without that card, and an incomplete one keeps it (1 of 7, all seven
  rows). No summary-failure case or narrow width was checked.
- `python -m pytest` after the E4.5 prompt change: 737 passed. Five real-model conversations, run
  three times in all: the runner reports 5/5 each time, but manual review of the final run fails two
  of the five. Screening outcomes and saved data are as expected. See the
  [E4.5 final cycle](docs/planning/e4-5-agent-tone.md#final-cycle-the-users-acknowledgement-rule).
- `python -m pytest` after E4: 736 passed. `npm run build` passed. Recruiter screen checks in
  real Chrome, with a temporary database and a fake model, and what was not checked: see the
  [E4 evidence](docs/planning/e4-recruiter-tool.md#e4-evidence-2026-11-05).
- `python -m pytest` after E3.4: 685 passed. `npm run build` passed.
- Reminder in the served browser and focused real-model runs (stop/resume and imprecise start, all
  3/3): see the [E3.4 evidence](docs/planning/e3-conversational-agent.md#e34-evidence-2026-11-05).
  With the final prompts, 37/39 runs of the affected cases passed; both failures are provider
  `llm_transient` errors, and those two scenarios then passed 6/6 alone. Replies after a reminder
  passed 21/21. The full batteries were not rerun.
- `python -m pytest` after E3.3: 641 passed. `npm run build` passed.
- Browser checks with the real model (qualified and rejected screenings, reload, retry, closed
  state, new application): see the [E3.3 evidence](docs/planning/e3-conversational-agent.md#e33-evidence-2026-11-05).
- `python -m pytest` after E3.2: 593 passed.
- Final evals with the real model: tuned 89/89 and held-out 82/84, with no false rejection
  or false qualification.

## Verification recorded on 2026-11-04

- Created `app/.venv` and installed `requirements-dev.txt` successfully.
- `python -m pytest` after E2: 192 passed, with temporary SQLite files and no network
  or LLM calls. The environment was not recreated after adding the `pydantic` pin.
- `python -m pip check`: no broken requirements.
- `npm ci` and `npm run build`: passed; the npm lockfile is included.
- `npm audit`: no reported vulnerabilities after updating Vite to 7.3.7 and
  compatible transitive dependencies.
- Uvicorn served the health endpoint, HTML and generated JS/CSS over real HTTP.
- Headless Chrome rendered the React placeholder; the screenshot was inspected.
- Earlier missing-build checks confirmed a useful root 503 and a working API.
- Health TDD was recorded during implementation: 404 before the route, then 200.

The test run emits a Starlette deprecation warning about its HTTPX test adapter;
it does not fail tests. Direct Python dependencies are pinned; transitive Python
versions are resolved by pip and are not a full environment lock.

## Next work

[E4](docs/planning/e4-recruiter-tool.md) is complete.
[E4.5 — Agent tone](docs/planning/e4-5-agent-tone.md) is closed by [user decision](docs/planning/e4-5-agent-tone.md#user-decision-closed-with-known-limits)
with known limits: replies now receive the saved fields and pending questions and usually open
with a brief acknowledgement, but a correction can go unacknowledged and a job question can come
up when not asked. [E4.6 — ATS integration design](docs/planning/e4-6-ats-integration-design.md)
is now planned: a proposed API specification and integration explanation, without
building a connector or changing the running application. E5 completes delivery.

A [scoped location-question change](docs/planning/e4-5-agent-tone.md#scoped-follow-up-work-location-question)
now instructs the agent to mention delivery areas in Spain and Mexico and ask for the
desired city and country together. Coverage rules are unchanged. This prompt change
has not been validated with the real model.

The recruiter detail now shows “Datos recogidos” only when an application has no summary, such as
an incomplete one. Otherwise the internal summary is the single view of the collected data.
