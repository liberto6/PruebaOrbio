# E5 — Delivery

**Status: planned, 2026-11-05.** Sources: [assignment](../assignment.html),
[process design](../design/process-design.md) and [roadmap](build-roadmap.md).

## Goal

Leave the work ready to submit and defend: everything the assignment lists as a deliverable exists, a
reviewer can run it from a clean clone, and the live presentation is rehearsed.

## Scope

**In:**

1. **Code freeze.** The agent, prompts and rules stay as delivered. Only a defect that blocks the demo
   or the clean-clone install can change code, and only after asking.
2. **README.** Reorganized around the assignment's list: setup, architecture overview, key design
   decisions, potential improvements and sample conversations. The stage-by-stage verification log moves
   out of the way, without losing its evidence.
3. **Process design check.** Confirm that the 1-2 page document still matches the delivered behaviour,
   including the single offer of covered cities.
4. **Clean-clone install.** Follow the README literally from a new clone, without the existing
   environment, up to one screening in the browser and its decision in the recruiter tool.
5. **Demo script.** Fixed candidate messages for the live demo and the video, using the real model.
6. **Video.** Script and shot list for 5-10 minutes; the user records it, and cuts are allowed.
7. **Presentation script.** Notes for the product (15 min), architecture (15 min) and discussion
   (10 min) blocks, including limits, “I'd do X differently”, and how this scales to 10K candidates/week.
8. **Repository delivery.** Commit, then publish where the user decides.

**Out:** new features or bonuses, prompt tuning, more eval batteries, Docker, CI, deployment and
authentication. The accepted limits stay [as recorded](e4-5-agent-tone.md#final-batteries-before-e5) and
are presented openly.

## Decisions for the user

- [x] Repository: public, in the user's GitHub account `liberto6`. The assignment does not state otherwise.
- [x] Video narration: English over the Spanish demo.
- [x] Fallback if the model fails live: the recorded video, plus conversations already saved in a local
  database for the recruiter tool, which needs no model.

## Checklist

- [x] Delivered work committed, with the user's approval.
- [x] README reorganized; every link and command checked.
- [x] Process design matches the delivered behaviour.
- [ ] Clean clone: install, `python -m pytest`, `npm run build`, one real screening, one decision.
- [ ] No secret in the repository or its history, and `.env` and the database ignored.
- [ ] Demo script rehearsed once against the real model; its cost recorded.
- [ ] Video script and shot list ready; video recorded by the user, 5-10 minutes.
- [ ] Presentation script ready.
- [ ] Repository published and its link checked from outside, by the user or with their approval.
- [ ] Roadmap, README and `AGENTS.md` match the delivered work.

## Evidence

- README, 2026-11-05: reorganized around the assignment's list; relative links and anchors resolved by script; PowerShell setup, build, 503, health, `SCREENING_DB`, `python -m pytest` (802 passed) and `python -m evals.run --help` run in a throwaway clone with no key and no chat message or paid eval; macOS/Linux commands not verified; the per-stage log left the README, with its one README-only item moved to the [E4 evidence](e4-recruiter-tool.md#e4-evidence-2026-11-05).
