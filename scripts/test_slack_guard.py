#!/usr/bin/env python3
"""Cases for when the Slack step may run (#59). Run: python3 scripts/test_slack_guard.py

The Slack step runs the local action in .claude-review-action/, and runs on
always(), so it must also require that this repository was checked out into
that folder. Checks every reusable workflow that has the step. Standard
library only.
"""
import pathlib
import sys

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github/workflows"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:400]}")


def steps(lines):
    """[(index, name, lines)] for each step."""
    at = [i for i, l in enumerate(lines) if l.startswith("      - name: ")]
    return [(s, lines[s][14:], lines[s:e]) for s, e in zip(at, at[1:] + [len(lines)])]


files = sorted(p for p in WORKFLOWS.glob("*.yml") if "uses: ./.claude-review-action/actions/slack-notify" in p.read_text())
check("there are workflows with the Slack step", len(files) >= 2, [p.name for p in files])
for path in files:
    named = {n: (i, b) for i, n, b in steps(path.read_text().splitlines())}
    co_at, co = named.get("Checkout review action repo (prompts)", (-1, []))
    clear_at, _ = named.get("Clear the action folder", (-1, []))
    slack_at, slack = named.get("Notify Slack", (-1, []))
    cond = next((l.strip() for l in slack if l.strip().startswith("if:")), "")
    check(f"59.1 {path.name}: the action checkout has id action-checkout", "        id: action-checkout" in co, co[:3])
    check(f"59.2 {path.name}: Slack needs that checkout to have succeeded, as an && term with no || to undo it",
          "&& steps.action-checkout.outcome == 'success' &&" in cond and "||" not in cond, cond)
    check(f"59.3 {path.name}: Slack still runs on always(), only for pull_request and when asked",
          cond.startswith("if: always() &&") and "inputs.slack_notify" in cond and "github.event_name == 'pull_request'" in cond, cond)
    check(f"59.5 {path.name}: clear, then checkout, then Slack", 0 <= clear_at < co_at < slack_at, (clear_at, co_at, slack_at))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
