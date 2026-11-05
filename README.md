# Grupo Sazón

Local candidate screening and recruiter tool. **Rules, persistence, turn coordination and
the real model integration are implemented but not connected to the application.** The
Spanish placeholder does not accept applications. The chat and recruiter tool are not
implemented.

Read the [assignment](docs/assignment.html), [process design](docs/design/process-design.md),
[roadmap](docs/planning/build-roadmap.md), [E1 checklist](docs/planning/e1-foundations.md),
[E2 plan](docs/planning/e2-rules-and-persistence.md)
and [E3 plan](docs/planning/e3-conversational-agent.md).

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

Open http://127.0.0.1:8000. In another PowerShell window:

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
the frontend build. After building, FastAPI serves `app/frontend/dist/index.html` at
`/` and the generated assets at `/assets`. Restart Uvicorn after creating the build.
Unknown API paths return JSON 404; there is no frontend catch-all.
Without a build, startup logs the build commands and `/` returns an actionable
JSON 503 while the API stays available.

The frontend uses React, Vite, plain JavaScript and CSS. There is no separate
frontend development server or CORS setup.

`app/backend/rules.py` holds the strict Pydantic candidate model, field validation,
coverage catalog, clarification limit and outcome evaluation. `app/backend/storage.py`
persists applications and messages in SQLite through standard-library `sqlite3`, with
each operation in one transaction. Neither is used by `main.py` yet, and the caller
supplies the database path, so running the application creates no database. Python
validates and determines outcomes while the LLM interprets and writes.
`app/backend/conversation.py` chooses the next action and advances each candidate turn
from saved state. `app/backend/llm.py` holds the model contract, prompts and OpenAI
adapter. The model picks covered places from a closed list and reports others as stated;
Python compares them with the catalog. These modules run only in tests and evals so far.
This is a local demo without authentication or deployment infrastructure.

## Model and evals

The model is OpenAI `gpt-6-luna`: economical, with strict structured output. Configure it
by copying `app/.env.example` to `app/.env` and setting `OPENAI_API_KEY`; `SCREENING_MODEL`
is optional. `.env` is ignored by Git. Pytest needs no credentials and makes no network calls.

From `app/`, these commands run scripted conversations against the real model. **Each run
costs money** (a full battery was about 0.09 USD) and saves its results in `docs/evals/`:

```powershell
python -m evals.run
python -m evals.run --held-out --repeat 3
python -m evals.run --only guadalajara,no_license --repeat 3
```

Latest results, the rationale for the model and the known limits are in the
[E3 plan](docs/planning/e3-conversational-agent.md).

## Verification recorded on 2026-11-05

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

E3 continues with the web chat, reminder and sample conversations; E4 adds the recruiter
tool; E5 completes delivery.
