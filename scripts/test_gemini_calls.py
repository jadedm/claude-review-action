#!/usr/bin/env python3
"""Cases for the parallel Gemini calls (#30). Run: python3 scripts/test_gemini_calls.py

Runs the real "Call Gemini API" step, cut out of gemini-review.yml, against a
stub `curl` whose reply depends on the file names in the request: fail500,
timeout, flaky429 (429 once, then a review), unreadable, and slow (3 s).
The step writes fixed /tmp paths, so this must not run beside a real review.

Standard library only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
WORKFLOW = REPO / ".github/workflows/gemini-review.yml"
CHUNKS = pathlib.Path("/tmp/review-chunks")
TMP = [pathlib.Path("/tmp", n) for n in ("review-prompt.txt", "review-coverage.json", "gemini-review.json")]

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:600]}")


def step_script(name):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body, indent = [], None
    for line in lines[run + 1:]:
        if line.strip() and indent is None:
            indent = len(line) - len(line.lstrip())
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    return "\n".join(body) + "\n"


CALL = step_script("Call Gemini API")

STUB = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
out = args[args.index("-o") + 1]
body = sys.stdin.read()
state = os.environ["STUB_STATE"]
def reply(code, payload):
    with open(out, "w") as f:
        f.write(payload)
    sys.stdout.write(str(code))
    sys.exit(0)
def review(summary):
    text = json.dumps({"summary": summary, "issues": [{"file": summary, "line": 1, "description": "d",
                       "severity": "low", "category": "c"}], "positives": [], "overall_assessment": "comment"})
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})
name = next((n for n in ("fail500", "timeout", "flaky429", "unreadable", "slow", "fast", "plain") if f"--- {n}.py ---" in body), "plain")
if name == "fail500":
    reply(500, '{"error": {"message": "boom"}}')
if name == "timeout":
    sys.exit(28)
if name == "flaky429":
    marker = os.path.join(state, "flaky")
    if not os.path.exists(marker):
        open(marker, "w").close()
        reply(429, '{"error": {"message": "slow down"}}')
if name == "unreadable":
    reply(200, json.dumps({"candidates": [{"content": {"parts": [{"text": "not a review"}]}, "finishReason": "STOP"}]}))
if name == "slow":
    time.sleep(3)
reply(200, review(name))
'''


def run_calls(chunk_names):
    """chunk_names: one list of file names per call. Returns (proc, review, coverage, seconds)."""
    shutil.rmtree(CHUNKS, ignore_errors=True)
    for p in TMP:
        p.unlink(missing_ok=True)
    work = pathlib.Path(tempfile.mkdtemp())
    (work / ".claude-review-action").symlink_to(REPO)
    bin_dir = work / "_bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(STUB)
    (bin_dir / "curl").chmod(0o755)
    state = work / "_state"
    state.mkdir()
    if chunk_names is None:
        chunk_names = []  # the planner made no calls and wrote no chunk directory
    for n, names in enumerate(chunk_names, 1):
        d = CHUNKS / str(n)
        d.mkdir(parents=True)
        (d / "code.txt").write_text("".join(f"--- {name} ---\nx = 1\n\n" for name in names))
        (d / "diff.txt").write_text("")
        (d / "files.json").write_text(json.dumps(names))
    pathlib.Path("/tmp/review-prompt.txt").write_text("Review this.\n")
    pathlib.Path("/tmp/review-coverage.json").write_text(json.dumps({"omitted": [], "filtered": 0, "diff_sent": 0, "diff_total": 0}))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GEMINI_API_KEY="k", GEMINI_MODEL="m", STUB_STATE=str(state))
    start = time.monotonic()
    proc = subprocess.run(["bash", "-e", "-c", CALL], cwd=work, env=env, capture_output=True, text=True)
    seconds = time.monotonic() - start
    out = pathlib.Path("/tmp/gemini-review.json")
    review = json.loads(out.read_text()) if out.exists() else None
    coverage = json.loads(pathlib.Path("/tmp/review-coverage.json").read_text())
    shutil.rmtree(work)
    return proc, review, coverage, seconds


def reasons(coverage):
    return {o["file"]: o["reason"] for o in coverage["omitted"]}


print("parallel calls")
proc, review, cov, secs = run_calls([["plain.py"]])
check("30.1 one call: its review is written as it came", proc.returncode == 0 and review["summary"] == "plain" and "parts" not in review,
      (proc.stdout[-400:], review))
proc, review, cov, secs = run_calls([["fail500.py"], ["plain.py"]])
check("30.10 one call gets HTTP 500: the other's review posts and the step exits 0",
      proc.returncode == 0 and [p["summary"] for p in review["parts"]] == ["plain"], (proc.stdout[-600:], review))
check("  the failed call's files are listed, and its error is in the log",
      reasons(cov) == {"fail500.py": "review call failed"} and "HTTP 500" in proc.stdout, (cov, proc.stdout[-400:]))
proc, review, cov, secs = run_calls([["fail500.py"], ["timeout.py"]])
check("30.12 every call fails: the step fails with each error printed",
      proc.returncode != 0 and "HTTP 500" in proc.stdout and "curl exit 28" in proc.stdout and review is None,
      (proc.returncode, proc.stdout[-600:]))
proc, review, cov, secs = run_calls([["slow.py"], ["slow.py"], ["slow.py"]])
check("30.13 three 3-second calls finish in under 6 s, so they ran in parallel", proc.returncode == 0 and secs < 6, secs)
proc, review, cov, secs = run_calls([["slow.py"], ["fast.py"]])
check("30.21 a slow call 1 and a fast call 2: merged in call order",
      [p["label"] for p in review["parts"]] == ["Part 1 of 2", "Part 2 of 2"]
      and [p["summary"] for p in review["parts"]] == ["slow", "fast"], review)
proc, review, cov, secs = run_calls([["flaky429.py"], ["timeout.py"], ["fail500.py"]])
check("30.22 a 429 then success, a timeout and a 500 in one run: the first posts, the others are listed",
      proc.returncode == 0 and [p["summary"] for p in review["parts"]] == ["flaky429"]
      and reasons(cov) == {"timeout.py": "review call failed", "fail500.py": "review call failed"}, (proc.stdout[-600:], cov))
proc, review, cov, secs = run_calls([["unreadable.py"], ["plain.py"]])
check("30.11 an unreadable reply beside a good one: posts, the unreadable call's files listed",
      proc.returncode == 0 and reasons(cov) == {"unreadable.py": "reply unreadable"}, cov)

proc, review, cov, secs = run_calls(None)
check("30.28 no planned calls: the step exits 0 and writes a nothing-sent review",
      proc.returncode == 0 and review == {"nothing_sent": True}, (proc.returncode, proc.stdout[-300:], proc.stderr[-300:]))

shutil.rmtree(CHUNKS, ignore_errors=True)
for p in TMP:
    p.unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
