# Development guidance

Build the local Grupo Sazón screening agent and recruiter tool. Read sources in
order: [assignment](docs/assignment.html), [process design](docs/design/process-design.md),
[roadmap](docs/planning/build-roadmap.md), then the active stage plan:
[E2](docs/planning/e2-rules-and-persistence.md).

- Inspect existing work before editing; implement only the requested stage.
- Application code, dependencies and tests live in `app/`. Run Python setup,
  tests and Uvicorn from `app/`, and npm commands from `app/frontend/`.
- Stack: Python venv/pip, FastAPI/Uvicorn, Pydantic, pytest/HTTPX; React/Vite with plain
  JavaScript/CSS built and served by FastAPI. SQLite starts in E2.
- Keep the backend flat. Add dependencies or abstractions only for implemented needs.
- Use backend TDD: observe the relevant test fail, implement, then verify it passes.
  Automated backend tests must not call an LLM or external services.
- Code, README and commits are English; product copy and process design are Spanish.
- E2 excludes LLMs, conversation parsing, chat and recruiter interfaces, new HTTP
  endpoints, authentication, Docker, CI and deployment. Later stages decide their
  own details.
- Use [README](README.md) for setup and checks. Run relevant checks and update the
  active stage checklist and roadmap with actual evidence, limits and next steps.
- Do not select models for future work or create extra continuity/status files.
