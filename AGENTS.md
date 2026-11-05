# Development guidance

Build the local Grupo Sazón screening agent and recruiter tool. Read sources in
order: [assignment](docs/assignment.html), [process design](docs/design/process-design.md),
[roadmap](docs/planning/build-roadmap.md), then the active stage plan:
[E5 — Delivery](docs/planning/e5-delivery.md). E4 is complete.
[E4.6 — ATS integration design](docs/planning/e4-6-ats-integration-design.md) is delivered,
design only. E4.5 is closed by user decision with known limits: do not change the agent
further or spend on model runs without a new request.

- Inspect existing work before editing; implement only the requested stage.
- Application code, dependencies and tests live in `app/`. Run Python setup,
  tests and Uvicorn from `app/`, and npm commands from `app/frontend/`.
- Stack: Python venv/pip, FastAPI/Uvicorn, Pydantic, pytest/HTTPX; React/Vite with plain
  JavaScript/CSS built and served by FastAPI, with SQLite for persistence; OpenAI SDK
  and python-dotenv for the model.
- Keep the backend flat. Add dependencies or abstractions only for implemented needs.
- Use backend TDD: observe the relevant test fail, implement, then verify it passes.
  Automated backend tests must not call an LLM or external services. Real-model evals
  live in `app/evals/`, outside pytest; they need credentials and cost money.
- Code, README and commits are English; product copy and process design are Spanish.
- Recruiter routes are unauthenticated: local demo only. Authentication, Docker, CI,
  deployment, WhatsApp, voice and bonus implementations remain excluded. The ATS API in
  `docs/design/` is a proposal, including its security: it is not implemented. Preserve the
  completed E3 screening and E4 recruiter behaviour.
- Use [README](README.md) for setup and checks. Run relevant checks and update the
  active stage checklist and roadmap with actual evidence, limits and next steps.
- A stage is not finished until the stage plan, roadmap, README and this file
  (active plan, stack, exclusions) match the delivered work.
- Do not select models for future work or create extra continuity/status files.
