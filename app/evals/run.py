"""Runs the critical scenarios against the real model: `python -m evals.run` from app/."""

import argparse
import hashlib
import json
import logging
import re
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import openai

from backend import llm
from backend.conversation import (
    CLARIFICATION_BUDGET,
    TurnFailed,
    handle_message,
    start_application,
)
from backend.rules import MAX_CLARIFICATIONS, CandidateFields, folded
from backend.storage import init_db, load_application
from evals.scenarios import ANY, SCENARIOS, detected_language, replies_of

RESULTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "evals"
BUDGET_USD = 2.0
INPUT_USD, CACHED_USD, OUTPUT_USD = 0.10 / 1e6, 0.01 / 1e6, 0.50 / 1e6
WORKERS = 4
CLOCK = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)  # A Thursday, for relative dates.

INTERNAL_TERMS = re.compile(
    # Only names that are not ordinary words: "availability" is plain English.
    "|".join(
        [
            *(name for name in CandidateFields.model_fields if "_" in name),
            "pending_review",
            "not_answered",
            "unresolved",
            "qualified",
        ]
    )
)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
MONEY = re.compile(r"\d\s*(€|euros?|pesos|usd|mxn|\$)|[€$]\s*\d")
PROMISE = re.compile(r"entrevista|interview|te (llamaremos|contactaremos)|we will (call|contact)")
DENIED_DOUBTS = re.compile(r"dudas( pendientes)?\s*:\s*(ninguna|no hay|sin dudas)")
OUTCOME_WORDS = {"qualified": "superado", "disqualified": "descalificado", "pending_review": "pendiente"}


def cost_of(calls: list[dict]) -> float:
    total = 0.0
    for call in calls:
        total += (call["input"] - call["cached"]) * INPUT_USD
        total += call["cached"] * CACHED_USD + call["output"] * OUTPUT_USD
    return total


def _value_problem(name: str, expected, actual) -> str | None:
    if name == "full_name":
        same = folded(actual) == folded(expected)
    elif name == "work_locations":
        identified = {f"{p['country']}/{p['locality']}" for p in actual["identified"]}
        same = identified == expected["identified"] and bool(actual["unresolved"]) == expected["unresolved"]
    elif name in ("availability", "preferred_schedule"):
        same = set(actual["options"]) == expected
    elif name == "delivery_experience":
        platforms = folded(" | ".join(actual["platforms"]))
        same = (
            abs(actual["years"] - expected["years"]) < 0.01
            and len(actual["platforms"]) == len(expected["platforms"])
            and all(platform in platforms for platform in expected["platforms"])
        )
    elif name == "start_timing":
        same = actual["kind"] == expected["kind"] and expected["value"] in (ANY, actual["value"])
    else:
        same = actual == expected
    return None if same else f"{name}: expected {expected}, got {actual}"


def score(scenario, run: dict) -> list[str]:
    """Every failed assertion of one run. An empty list is a pass."""
    problems = []
    if run["turn_failure"]:
        problems.append(f"turn failed: {run['turn_failure']}")
    if run["unused_messages"]:
        problems.append(f"closed with {run['unused_messages']} messages unused")
    for warning in run["warnings"]:
        problems.append(f"model failure: {warning}")
    if run["outcome"] != scenario.outcome:
        problems.append(f"outcome: expected {scenario.outcome}, got {run['outcome']} {run['outcome_reasons']}")

    fields = run["fields"]
    for name, (status, value) in scenario.fields.items():
        state = fields[name]
        if state["status"] != status:
            problems.append(f"{name}: expected {status}, got {state['status']} (raw={state['raw']!r})")
        elif status == "resolved" and value is not ANY:
            problem = _value_problem(name, value, state["normalized"])
            if problem:
                problems.append(problem)
    for name, state in fields.items():
        asked = state["clarifications_asked"]
        if asked != scenario.clarifications.get(name, 0):
            problems.append(f"{name}: expected {scenario.clarifications.get(name, 0)} clarifications, got {asked}")
        if asked > CLARIFICATION_BUDGET.get(name, MAX_CLARIFICATIONS):
            problems.append(f"{name}: {asked} clarifications is above its budget")
    if scenario.next_field and run["last_question_field"] != scenario.next_field:
        problems.append(f"next question: expected {scenario.next_field}, got {run['last_question_field']}")

    replies = replies_of(run)
    if run["language"] != scenario.language:
        problems.append(f"language: expected {scenario.language}, got {run['language']}")
    if replies and detected_language(replies[-1]) != scenario.language:
        problems.append(f"last reply not in {scenario.language}: {replies[-1]!r}")
    for reply in replies:
        problems.extend(_reply_problems(reply))
    if run["closed"] and replies:
        if "?" in replies[-1]:
            problems.append(f"closing message asks something: {replies[-1]!r}")
        if PROMISE.search(folded(replies[-1])):
            problems.append(f"closing message promises something: {replies[-1]!r}")
    problems.extend(_summary_problems(scenario, run))
    for check in scenario.checks:
        problem = check(run)
        if problem:
            problems.append(problem)
    return problems


def _reply_problems(reply: str) -> list[str]:
    problems = []
    if EMOJI.search(reply):
        problems.append(f"emoji in reply: {reply!r}")
    if INTERNAL_TERMS.search(reply):
        problems.append(f"internal term in reply: {reply!r}")
    if MONEY.search(reply.lower()):
        problems.append(f"invented money term: {reply!r}")
    if reply.count("?") > 1:
        problems.append(f"more than one question: {reply!r}")
    if len(re.findall(r"[.!?]+(?:\s|$)", reply)) > 3:
        problems.append(f"more than three sentences: {reply!r}")
    return problems


def _summary_problems(scenario, run: dict) -> list[str]:
    summary = run["summary"]
    if not run["closed"]:
        return ["summary saved on an open application"] if summary else []
    if not summary:
        return ["closed without a summary"]
    text = folded(summary)
    problems = []
    if detected_language(summary) != "es":
        problems.append(f"summary not in Spanish: {summary!r}")
    if MONEY.search(summary.lower()):
        problems.append(f"invented money term in summary: {summary!r}")
    if OUTCOME_WORDS[run["outcome"]] not in text:
        problems.append(f"summary lacks the result {run['outcome']}: {summary!r}")
    if run["outcome"] == "pending_review" and DENIED_DOUBTS.search(text):
        problems.append(f"summary denies the doubts under review: {summary!r}")
    for words in scenario.summary_has:
        if not any(word in text for word in words):
            problems.append(f"summary lacks {words}: {summary!r}")
    for word in scenario.summary_lacks:
        if word in text:
            problems.append(f"summary contains {word!r}: {summary!r}")
    return problems


def wrong_decisions(scenario, run: dict) -> dict:
    return {
        "false_rejection": run["outcome"] == "disqualified" and scenario.outcome != "disqualified",
        "false_qualification": run["outcome"] == "qualified" and scenario.outcome != "qualified",
        "unnecessary_review": run["outcome"] == "pending_review" and scenario.outcome != "pending_review",
    }


class _WarningsOfThisThread(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.thread = threading.get_ident()
        self.messages = []

    def emit(self, record):
        if record.thread == self.thread:
            self.messages.append(record.getMessage())


def _turn_state(snapshot) -> dict:
    fields = snapshot.fields
    return {
        "stop_requested": snapshot.stop_requested,
        "closed": snapshot.closed_at is not None,
        "has_summary": snapshot.summary is not None,
        "pending_questions": len(snapshot.pending_questions),
        "last_question": [snapshot.last_question_field, snapshot.last_question_kind],
        "clarifications": {
            name: getattr(fields, name).clarifications_asked for name in CandidateFields.model_fields
        },
    }


def run_conversation(scenario, adapter) -> dict:
    """Drives the fixed messages through the real coordinator and a temporary database."""
    warnings = _WarningsOfThisThread()
    logger = logging.getLogger("backend.conversation")
    logger.addHandler(warnings)
    turn_seconds = []
    turn_states = []
    turn_failure = None
    unused = 0
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            db_path = Path(directory) / "eval.db"
            init_db(db_path)
            application_id = start_application(db_path).id
            for index, text in enumerate(scenario.messages):
                if load_application(db_path, application_id).snapshot.closed_at is not None:
                    unused = len(scenario.messages) - index
                    break
                started = time.perf_counter()
                try:
                    handle_message(db_path, application_id, f"r{index}", text, adapter, now=lambda: CLOCK)
                except TurnFailed as exc:
                    turn_failure = f"message {index}: {exc.code}"
                    break
                finally:
                    turn_seconds.append(round(time.perf_counter() - started, 3))
                turn_states.append(_turn_state(load_application(db_path, application_id).snapshot))
            stored = load_application(db_path, application_id)
    finally:
        logger.removeHandler(warnings)
    snapshot = stored.snapshot
    return {
        "scenario": scenario.name,
        "held_out": scenario.held_out,
        "closed": snapshot.closed_at is not None,
        "outcome": snapshot.outcome,
        "outcome_reasons": snapshot.outcome_reasons,
        "language": snapshot.language,
        "last_question_field": snapshot.last_question_field,
        "pending_questions": snapshot.pending_questions,
        "fields": snapshot.fields.model_dump(),
        "summary": snapshot.summary,
        "transcript": [{"role": m.role, "text": m.text} for m in stored.messages],
        "turn_failure": turn_failure,
        "unused_messages": unused,
        "warnings": warnings.messages,
        "turn_seconds": turn_seconds,
        "turn_states": turn_states,
        "calls": adapter.calls,
        "cost_usd": cost_of(adapter.calls),
    }


def prompts_hash() -> str:
    material = [
        llm.INTERPRETATION_PROMPT,
        llm.REPLY_PROMPT,
        llm.SUMMARY_PROMPT,
        json.dumps(llm.extraction_schema(), sort_keys=True),
    ]
    return hashlib.sha256("\n".join(material).encode()).hexdigest()[:16]


def spent_so_far() -> float:
    return sum(
        json.loads(path.read_text(encoding="utf-8"))["totals"]["cost_usd"]
        for path in RESULTS_DIR.glob("*.json")
    )


def selected(args) -> list:
    chosen = [s for s in SCENARIOS if s.held_out == args.held_out]
    if args.only:
        needles = args.only.split(",")
        chosen = [s for s in chosen if any(needle in s.name for needle in needles)]
    return chosen


def totals_of(runs: list[dict]) -> dict:
    calls = [call for run in runs for call in run["calls"]]
    turns = [seconds for run in runs for seconds in run["turn_seconds"]]
    return {
        "runs": len(runs),
        "passed": sum(not run["failures"] for run in runs),
        "false_rejection": sum(run["false_rejection"] for run in runs),
        "false_qualification": sum(run["false_qualification"] for run in runs),
        "unnecessary_review": sum(run["unnecessary_review"] for run in runs),
        "interpretation_unusable": sum(len(run["warnings"]) for run in runs),
        "turns": len(turns),
        "input_tokens": sum(call["input"] for call in calls),
        "cached_tokens": sum(call["cached"] for call in calls),
        "output_tokens": sum(call["output"] for call in calls),
        "reasoning_tokens": sum(call["reasoning"] for call in calls),
        "cost_usd": cost_of(calls),
        "turn_seconds_median": statistics.median(turns) if turns else None,
        "turn_seconds_max": max(turns) if turns else None,
        "seconds_by_step": {
            step: round(statistics.mean(c["seconds"] for c in calls if c["step"] == step), 2)
            for step in sorted({c["step"] for c in calls})
        },
    }


def print_report(runs: list[dict], totals: dict, path: Path, spent: float) -> None:
    for name in dict.fromkeys(run["scenario"] for run in runs):
        attempts = [run for run in runs if run["scenario"] == name]
        passed = sum(not run["failures"] for run in attempts)
        tag = "held-out " if attempts[0]["held_out"] else ""
        print(f"{'PASS' if passed == len(attempts) else 'FAIL'} {passed}/{len(attempts)} {tag}{name}")
        for attempt in attempts:
            for failure in attempt["failures"]:
                print(f"     - {failure}")
    print(json.dumps(totals, indent=1))
    print(f"Saved {path}")
    print(f"Cost of this run {totals['cost_usd']:.4f} USD; total so far {spent:.4f} of {BUDGET_USD} USD")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, help="Runs per scenario, instead of each scenario's own")
    parser.add_argument("--only", help="Comma-separated parts of scenario names")
    parser.add_argument("--held-out", action="store_true", help="Run the held-out scenarios instead")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    spent_before = spent_so_far()
    if spent_before >= BUDGET_USD:
        raise SystemExit(f"Budget of {BUDGET_USD} USD reached: {spent_before:.4f} spent")

    template = llm.llm_from_env()
    spending = [spent_before]
    lock = threading.Lock()

    def attempt(scenario):
        with lock:
            if spending[0] >= BUDGET_USD:
                return None
        adapter = llm.OpenAILLM(template._client, template.model)
        run = run_conversation(scenario, adapter)
        run["failures"] = score(scenario, run)
        run.update(wrong_decisions(scenario, run))
        with lock:
            spending[0] += run["cost_usd"]
        return run

    work = [s for s in selected(args) for _ in range(args.repeat or s.repeat)]
    started_at = datetime.now(timezone.utc)
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(attempt, work))
    runs = [run for run in results if run is not None]

    totals = totals_of(runs)
    result = {
        "started_at": started_at.isoformat(),
        "model": template.model,
        "sdk": openai.__version__,
        "settings": {
            "interpretation_effort": llm.INTERPRETATION_EFFORT,
            "writing_effort": llm.WRITING_EFFORT,
            "timeout_seconds": llm.TIMEOUT_SECONDS,
            "max_retries": llm.MAX_RETRIES,
            "max_output_tokens": llm.MAX_OUTPUT_TOKENS,
        },
        "prompts_hash": prompts_hash(),
        "arguments": vars(args),
        "stopped_by_budget": len(runs) < len(work),
        "totals": totals,
        "runs": runs,
    }
    path = RESULTS_DIR / f"e3-2-{started_at:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print_report(runs, totals, path, spent_before + totals["cost_usd"])


if __name__ == "__main__":
    main()
