# E1 — Foundations

**Status: Complete (2026-11-04).** See the closure evidence below. Follow the [assignment](../assignment.html),
[process design](../design/process-design.md) and [build roadmap](build-roadmap.md) in that order.

## Goal and decisions

Create a local, runnable application and a repeatable backend test command before implementing screening logic. E1 ends with an empty product shell, not a working screening agent.

- Use one repository and one FastAPI application. FastAPI serves the API under `/api` and the built React/Vite frontend from the same origin. SQLite is the local persistence choice; no database is needed to start E1.
- Keep Python business rules independent of HTTP and LLM calls when those rules arrive in E2. In E3, the LLM will interpret answers and write messages; Python will validate and determine outcomes.
- Use Python with `venv` and `pip`, Node.js with npm, and Git. Check the available runtimes during implementation and record the working versions in the README. Commit pinned Python dependency files and `package-lock.json` for repeatable installation.
- Start with FastAPI and Uvicorn at runtime; use pytest and HTTPX for backend tests. Use React, React DOM and Vite with plain JavaScript and CSS. Add dependencies only when an implemented requirement needs them.
- Use backend TDD: write a failing test, implement the smallest behavior, then refactor with tests passing. No real LLM calls in automated backend tests.
- Write code, README and commits in English. Recruiter-facing copy will be Spanish, as specified in the roadmap.
- Keep development-agent guidance in a short root `AGENTS.md`, with a root `CLAUDE.md` pointing Claude Code to the same instructions. Keep progress in the roadmap and stage checklists, and verified commands in the README. These files support continuity across tools and sessions; they do not select a model or preserve conversation memory. The product's LLM remains an E3 decision.

## Minimal structure

Create files as they become necessary; do not scaffold future layers or empty modules.

```text
app/
  backend/
    __init__.py
    main.py               # FastAPI application, health route, static serving
  frontend/
    src/                  # Minimal React entry and placeholder screen
    index.html
    package.json
    package-lock.json     # Reproducible npm dependency installation
    vite.config.js
  tests/
    test_app.py
  requirements.txt        # Pinned runtime dependencies
  requirements-dev.txt    # Runtime plus test dependencies
  pytest.ini
docs/
  assignment.html
  design/                 # Process design and conversation diagram
  planning/               # Build roadmap and stage plans
.gitignore
AGENTS.md                 # Shared development instructions
CLAUDE.md                 # Entry point referring to AGENTS.md
README.md
```

Keep the backend flat initially. Add rule and storage modules during E2 as behavior requires them. Do not introduce a service/repository hierarchy, dependency injection framework or generic LLM provider abstraction in E1.

## Implementation order

0. **Set up development-agent guidance.** Create a concise `AGENTS.md` in English with the project goal, source-of-truth order, links to design and planning documents, agreed stack, language conventions, backend TDD and scope discipline. Tell agents to read the active stage, inspect existing work, implement only the requested scope, run relevant checks and update the stage checklist with actual results and the next step. Link to the README for commands once verified; do not duplicate the process design. Create a short `CLAUDE.md` that directs Claude Code to read and follow `AGENTS.md`, adding only tool-specific guidance if needed. Check the entry point in each tool when first used. Do not introduce custom skills, orchestration, extra status files or model-specific project rules.
1. **Prepare the local repository.** Check Python, Node.js/npm and Git availability. Initialize Git in the project root and retain the assignment and existing docs. Ignore `.venv`, Python caches, `node_modules`, `app/frontend/dist`, local databases and secret environment files. Repository hosting and public/private visibility remain an E5 decision.
2. **Make backend tests runnable.** Create the virtual environment and dependency files inside `app/`. Write a failing test using FastAPI's test client: `GET /api/health` must return HTTP 200 with exactly `{"status": "ok"}`. Implement the route and run `python -m pytest` from `app/`. This checks application startup and HTTP wiring without domain logic or external services.
3. **Create the empty frontend.** Add a minimal React/Vite application with a clear placeholder indicating that screening is not implemented yet. Provide `npm run build`; do not add routing, a component library or state management. Do not display fake candidates or imply that answers can be submitted.
4. **Connect the local application.** Serve `app/frontend/dist` through FastAPI at `/`, with `/api/health` still reachable. Register API routes before any static mount. Use the built frontend for the acceptance run; a separate Vite dev server and CORS configuration are unnecessary for E1. If the frontend build is missing, log an actionable build instruction while keeping the API and backend tests runnable.
5. **Document and verify.** Write the README setup and run commands, including entering `app/`, virtual environment activation, dependency installation, frontend build, `python -m uvicorn backend.main:app --reload`, and `python -m pytest`. Record the local-only scope and the architecture decision above. Run the documented sequence, inspect `/` in a browser, rerun the health test after adding static serving, and create an initial commit with no generated files or secrets.

## E1 closure checklist

- [x] Root `AGENTS.md` contains the shared development guidance; root `CLAUDE.md` points to it without duplicating those rules. Referenced paths resolve, and guidance loading is checked in the tools used; any unused tool remains explicitly unverified.
- [x] The roadmap identifies the current stage; its checklist records completed work and the next step so another session can resume without relying on chat history.
- [x] Git is initialized locally and the foundation work is committed; existing documents are retained.
- [x] README commands reproduce dependency installation, frontend build, startup and backend tests using the recorded runtime versions.
- [x] After the build, FastAPI serves the React placeholder at `/`; browser assets load without errors and `/api/health` returns the agreed JSON.
- [x] `python -m pytest` passes without credentials, a database or network calls; the health test was observed failing before implementation.
- [x] No screening behavior, candidate data or recruiter workflow is presented as implemented. The README explains the structure and commands for developer review before E2.

### Closure evidence

- Local links resolve. `CLAUDE.md` points to `AGENTS.md` without duplicate rules.
  Claude Code loaded `CLAUDE.md` on first use and reached `AGENTS.md` through it.
- Python 3.13.7, Node 22.17.0, npm 10.9.2 and Git 2.50.0.windows.2.
- Created a fresh `app/.venv` and installed the development requirements.
  `python -m pip check` passed; `python -m pytest` passed both tests.
  The previous real TDD run recorded health returning 404 before implementation.
- Tests emit one upstream Starlette/HTTPX adapter deprecation warning. Direct
  Python pins are retained; transitive dependencies are not fully locked.
- Generated `app/frontend/package-lock.json`; `npm ci` and `npm run build` passed.
  Updated Vite from 7.3.1 to 7.3.7 and compatible transitive dependencies after
  npm audit findings. Final npm audit reported zero vulnerabilities.
- Real Uvicorn HTTP checks returned health JSON and HTML/JS/CSS successfully.
  Headless Chrome rendered the React content; the screenshot was inspected.
  The earlier no-build check confirmed a useful root 503 without breaking the API.
- Source and dependencies live in `app/`; design, assignment and planning in
  `docs/`; agent guidance stays at the repository root. Local links were verified.
- Earlier environment restrictions on installation and Git were resolved by the
  session permission change. Generated dependencies, builds and local verification
  artifacts remain ignored; the foundation is recorded in the initial Git commit.
- No screening, candidate persistence or recruiter workflow is implemented.
  The README documents commands and responsibilities for the developer to review.

**Next step:** start E2 by defining its data model and rules within the existing
roadmap. Presentation rehearsal remains in E5.

## Decisions left to later stages

**E2:** define the data model and complete JSON shape, raw/normalized values, field states, coverage catalog, outcome rules, persistence operations and database initialization. Choose the smallest SQLite access approach then; E1 does not prescribe an ORM, schema or migration framework.

**E3:** choose the LLM provider/model, prompts, extraction contract, conversation orchestration and eval scenarios. Implement history, resume, summaries, failures, ES/EN, guardrails and the single reminder there. No final model choice or LLM dependency is required for E1.

**E4/E5:** implement the recruiter workflow, then complete delivery and presentation. Authentication, CI, Docker and deployment infrastructure are outside E1; the roadmap keeps login and real deployment out of scope.
