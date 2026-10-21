#!/usr/bin/env python3
"""Cases for the Slack step in each reusable workflow (#59, #75).

Run: python3 scripts/test_slack_guard.py

The Slack step runs the local action in .claude-review-action/, and runs on
always(), so it must also require that this repository was checked out into
that folder (#59). The pull request title goes in through the action's title
input, which escapes it, never straight into the message (#75): each caller's
message is filled in as GitHub would and sent through the action's own script,
with curl replaced by a stub that records the request body. Needs bash and jq.
Standard library only.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

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


ACTION = WORKFLOWS.parent.parent / "actions/slack-notify/action.yml"
TITLE = "<!channel> <https://x.example|y> & z"
SAFE = "&lt;!channel&gt; &lt;https://x.example|y&gt; &amp; z"


def action_script():
    lines = ACTION.read_text().splitlines()
    at = lines.index("      run: |")
    return "\n".join(l[8:] for l in lines[at + 1:]) + "\n"


def message_block(step):
    """The message: literal block of a step, unindented."""
    at = step.index("          message: |")
    out = []
    for l in step[at + 1:]:
        if l.strip() and not l.startswith("            "):
            break
        out.append(l[12:])
    return "\n".join(out).strip("\n") + "\n"


def sent(message, title):
    """The text the action would send for a message and title."""
    with tempfile.TemporaryDirectory() as tmp:
        stub = pathlib.Path(tmp, "curl")
        stub.write_text('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && printf %s "$2" > "$BODY"; shift; done\ncat > /dev/null\n')
        stub.chmod(0o755)
        body = pathlib.Path(tmp, "body.json")
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "BODY": str(body),
               "SLACK_WEBHOOK_URL": "https://hooks.example/services/T/B/x", "INPUT_MESSAGE": message, "INPUT_TITLE": title}
        p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", action_script()], env=env, capture_output=True, text=True)
        if p.returncode != 0 or not body.exists():
            return None
        return json.loads(body.read_text())["text"]


files = sorted(p for p in WORKFLOWS.glob("*.yml") if "uses: ./.claude-review-action/actions/slack-notify" in p.read_text())
check("there are workflows with the Slack step", len(files) >= 3, [p.name for p in files])
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
    message = message_block(slack) if "          message: |" in slack else ""
    check(f"75.1 {path.name}: Slack passes the title input as the pull request title",
          "          title: ${{ github.event.pull_request.title }}" in slack, slack[:8])
    check(f"75.2 {path.name}: the message holds no raw pull request title",
          message and "github.event.pull_request.title" not in message, message)
    check(f"75.3 {path.name}: the message holds {{title}} once", message.count("{title}") == 1, message)
    # GitHub fills in the message's own ${{ }} values before the action runs.
    filled = re.sub(r"\$\{\{.*?\}\}", "V", message)
    text = sent(filled, TITLE)
    check(f"75.4 {path.name}: through the action, the title arrives escaped and the rest of the message unchanged",
          text is not None and text == filled.replace("{title}", SAFE) and TITLE not in text, (text, filled))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
