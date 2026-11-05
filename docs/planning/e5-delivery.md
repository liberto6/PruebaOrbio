# E5 — Delivery

**Status: complete, 2026-11-05, as confirmed by the user.** The demo video is linked in the README. Sources: [assignment](../assignment.html),
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
- [x] Clean clone: install, `python -m pytest`, `npm run build`, one real screening, one decision.
- [x] No secret in the repository or its history, and `.env` and the database ignored.
- [x] Demo script run once against the real model by an agent; its cost recorded. User preparation closed on the user's confirmation that all work is complete.
- [x] Video script and shot list ready; video recorded by the user. Local final video files are present; duration and contents were not independently reviewed at closure.
- [x] Presentation script ready, kept out of Git in `docs/delivery/`, with personal answers for the user to adjust.
- [x] Repository published and its link checked from outside, with the user's approval.
- [x] Roadmap, README and `AGENTS.md` match the delivered work.

## Evidence

- Closure, 2026-11-05: the user confirmed all work complete and requested documentary closure. Local untracked files include `demo-final.mp4`, `demo-final-x1.10.mp4` and `demo-final-sin-pestanas.mp4`. The user supplied the [demo video link](https://drive.google.com/file/d/1hegSkDbeJ-ZmQFlQGbwFQ7s7p3HrP3VV/view?usp=sharing), now in the README. This records user confirmation, not a new independent video review or live rehearsal; external access permissions were not verified. No application code, prompts or evals changed.

- README, 2026-11-05: reorganized around the assignment's list; relative links and anchors resolved by script; PowerShell setup, build, 503, health, `SCREENING_DB`, `python -m pytest` (802 passed) and `python -m evals.run --help` run in a throwaway clone with no key and no chat message or paid eval; macOS/Linux commands not verified; the per-stage log left the README, with its one README-only item moved to the [E4 evidence](e4-recruiter-tool.md#e4-evidence-2026-11-05).
- Clean clone, 2026-11-05: every PowerShell setup step passed on a new clone of `b2cea18`, with no reused environment, and `python -m pytest` reported 802 passed. macOS/Linux, the evals, the no-build path and blocked activation were not part of this run.
- Demo run, 2026-11-05: in real Chrome against the real model, on that clone. Qualified with “maadrid” and a job question, no licence, single offer then rejection for Málaga, English to the close, pending review on Guadalajara, and the reminder after about 10 minutes all behaved as the script says, with no doubled reply and no provider error. Turns took 2.8 to 6.2 s. Most messages were entered by a page script rather than typed. Estimated cost about 0.007 USD from saved run costs; browser calls are not metered. In the recruiter tool, a decision was saved with no key and survived a reload.
- Noticed, left in place by the code freeze: “Malaga” without its accent in the recruiter row, “Good to know” repeated like “Perfecto”, and summary layout that varies between runs.
- Secrets, 2026-11-05: no `.env`, database or key pattern in the tracked files or the history of any branch.
- Process design, 2026-11-05: translated to English at the user's request, with the flow diagram. Product copy stays Spanish, so quoted candidate and closing messages stay Spanish with a translation. The diagram was checked in a headless Chrome screenshot; its click paths were not exercised.
