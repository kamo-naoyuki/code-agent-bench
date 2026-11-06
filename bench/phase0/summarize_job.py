#!/usr/bin/env python3
"""Summarise Harbor job directories: per-trial table plus per-(job, model) aggregates.

Reads only files Harbor already wrote (job result.json, trial result.json). Used in Phase 0 to
get "cost and time per trial" (PLAN.md 0.3 Step 4) and to flag trials whose agent produced
nothing, because Harbor records e.g. a broken agent install as a plain AgentTimeoutError
(see reports/phase0.md) and the usual reward table would count it as an agent failure.

Usage: summarize_job.py jobs/<job> [jobs/<job> ...]   (exit status 1 if any trial is suspicious)
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def phase_seconds(result, key):
    block = result.get(key)
    if not block or not block.get("started_at") or not block.get("finished_at"):
        return None
    return (ts(block["finished_at"]) - ts(block["started_at"])).total_seconds()


def load_trials(job_dir):
    for trial_json in sorted(Path(job_dir).glob("*/result.json")):
        result = json.loads(trial_json.read_text())
        usage = result.get("agent_result") or {}
        exception = (result.get("exception_info") or {}).get("exception_type")
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        info = result.get("agent_info") or {}
        model = (info.get("model_info") or {}).get("name") or info.get("name") or "?"
        trajectory = trial_json.parent / "agent" / "trajectory.json"
        total = None
        if result.get("started_at") and result.get("finished_at"):
            total = (ts(result["finished_at"]) - ts(result["started_at"])).total_seconds()
        yield {
            "job": Path(job_dir).name,
            "trial": trial_json.parent.name,
            "task": result.get("task_name"),
            "agent": info.get("name"),
            "model": model,
            "reward": rewards.get("reward"),
            "exception": exception,
            "n_in": usage.get("n_input_tokens"),
            "n_out": usage.get("n_output_tokens"),
            "n_cache": usage.get("n_cache_tokens"),
            "cost": usage.get("cost_usd"),
            "premium": (usage.get("metadata") or {}).get("premium_requests"),
            "setup_s": phase_seconds(result, "agent_setup"),
            "agent_s": phase_seconds(result, "agent_execution"),
            "verify_s": phase_seconds(result, "verifier"),
            "total_s": total,
            "has_trajectory": trajectory.exists(),
        }


def suspicious(row):
    """A trial whose LLM-driven agent left no trace is a harness problem until proven otherwise.

    A timeout that still has a trajectory and token usage is a genuine budget exhaustion
    (PLAN.md 6.2: counted as an agent failure), so it is not flagged.
    """
    if row["agent"] in ("oracle", "nop"):
        return None
    if not row["has_trajectory"]:
        return "no ATIF trajectory (agent made no recorded progress)"
    if not row["n_in"]:
        return "no input tokens recorded"
    if row["exception"] and row["exception"] != "AgentTimeoutError":
        return f"exception={row['exception']}"
    return None


def fmt(value, spec=""):
    return "-" if value is None else format(value, spec)


def main(job_dirs):
    rows = [row for job in job_dirs for row in load_trials(job)]
    if not rows:
        print("no trials found")
        return 1
    header = f"{'job':26} {'task':34} {'model':14} rew  {'exception':18} {'in':>7} {'out':>6} {'cache':>6} {'$':>8} {'prem':>5} {'setup':>6} {'agent':>6} {'verif':>6} {'total':>6}"
    print(header)
    print("-" * len(header))
    bad = 0
    for r in rows:
        flag = suspicious(r)
        bad += bool(flag)
        print(
            f"{r['job'][:26]:26} {(r['task'] or '?')[:34]:34} {r['model'][:14]:14} "
            f"{fmt(r['reward'], '.1f'):>3}  {(r['exception'] or '-')[:18]:18} "
            f"{fmt(r['n_in']):>7} {fmt(r['n_out']):>6} {fmt(r['n_cache']):>6} {fmt(r['cost'], '.4f'):>8} {fmt(r['premium'], '.2f'):>5} "
            f"{fmt(r['setup_s'], '.0f'):>6} {fmt(r['agent_s'], '.0f'):>6} {fmt(r['verify_s'], '.0f'):>6} {fmt(r['total_s'], '.0f'):>6}"
            + (f"   <-- SUSPICIOUS: {flag}" if flag else "")
        )
    print()
    groups = {}
    for r in rows:
        groups.setdefault((r["job"], r["model"]), []).append(r)
    print("per (job, model): n | mean reward | mean $/trial | total premium requests | mean total s/trial | sum tokens in/out/cache")
    for (job, model), items in sorted(groups.items()):
        rewards = [i["reward"] for i in items if i["reward"] is not None]
        costs = [i["cost"] for i in items if i["cost"] is not None]
        premiums = [i["premium"] for i in items if i["premium"] is not None]
        totals = [i["total_s"] for i in items if i["total_s"] is not None]
        mean = lambda xs: sum(xs) / len(xs) if xs else None
        print(
            f"  {job[:26]:26} {model[:14]:14} n={len(items)} reward={fmt(mean(rewards), '.2f')} "
            f"$={fmt(mean(costs), '.4f')} premium={fmt(sum(premiums) if premiums else None, '.2f')} "
            f"sec={fmt(mean(totals), '.0f')} "
            f"tokens={sum(i['n_in'] or 0 for i in items)}/{sum(i['n_out'] or 0 for i in items)}/{sum(i['n_cache'] or 0 for i in items)}"
        )
    if bad:
        print(f"\n{bad} suspicious trial(s): do not count them as agent failures before checking agent/ logs.")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
