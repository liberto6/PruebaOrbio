# Grupo Sazón

Local candidate screening and recruiter tool, currently at **E1 foundations complete**.
The Spanish placeholder does not accept applications. Screening, candidate storage,
LLM integration and recruiter workflows are not implemented.

Read the [assignment](docs/assignment.html), [process design](docs/design/process-design.md),
[roadmap](docs/planning/build-roadmap.md) and [E1 checklist](docs/planning/e1-foundations.md).

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
frontend development server or CORS setup. SQLite is the future local persistence
choice; E1 creates no database. In later stages Python validates and determines
outcomes while the LLM interprets and writes. No LLM/provider is selected yet.
This is a local demo without authentication or deployment infrastructure.

## Verification recorded on 2026-11-04

- Created `app/.venv` and installed `requirements-dev.txt` successfully.
- `python -m pytest` inside that environment: 2 passed.
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
`AGENTS.md` is active in this session. Claude Code has not been used, so its
instruction loading is explicitly unverified and should be checked on first use.

## Next work

E2 will define field rules, outcomes and SQLite persistence. E3 adds conversation
and LLM integration; E4 adds the recruiter tool; E5 completes delivery. Review the
architecture and commands before starting E2. No E2 functionality is implemented.
