#!/usr/bin/env python3
"""Cases for saying when Claude did not review (#15). Run: python3 scripts/test_claude_skip.py

Runs the real "Say when Claude did not review" step of claude-review.yml in
bash against a stub `gh` that serves the pull request's files and comments
from a state file and records what is posted. Standard library only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github/workflows/claude-review.yml"
MARKER = "<!-- claude-review-action: not reviewed -->"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


lines = WORKFLOW.read_text().splitlines()
start = lines.index("      - name: Say when Claude did not review")
end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("      - name:"))
STEP = lines[start:end]
run_at = STEP.index("        run: |")
SCRIPT = "\n".join(l[10:] for l in STEP[run_at + 1:]) + "\n"
IF = next(l.strip() for l in STEP if l.strip().startswith("if:"))

STUB = r'''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["STUB_STATE"]))
args = sys.argv[1:]
if state.get("fail"):
    sys.stderr.write("HTTP 502: bad gateway\n"); sys.exit(1)
if args[0] == "pr" and args[1] == "comment":
    body = open(args[args.index("--body-file") + 1]).read()
    state["posted"].append(body)
    json.dump(state, open(os.environ["STUB_STATE"], "w"))
    sys.exit(0)
path = [a for a in args if a.startswith("repos/")][0]
jq = args[args.index("--jq") + 1]
if path.endswith("/comments"):
    rows = [{"body": b, "user": {"login": who}} for who, b in state["comments"]]
elif path.endswith("/files"):
    rows = [{"filename": f} for f in state["files"]]
else:
    sys.exit(2)
# The step's two jq filters count matches; emulate them for one page.
# One printed count per page, as gh --paginate --jq does; PAGES splits the rows.
pages = max(int(state.get("pages", 1)), 1)
for n in range(pages):
    page = rows[n::pages]
    if "contains" in jq:
        marker = jq.split('contains(\\"', 1)[1].split('\\"', 1)[0] if 'contains(\\"' in jq else jq.split('contains("', 1)[1].split('"', 1)[0]
        print(sum(1 for r in page if r["user"]["login"] == "github-actions[bot]" and marker in r["body"]))
    else:
        print(sum(1 for r in page if r["filename"].startswith(".github/workflows/")))
'''


def run(files, comments=(), fail=False, pages=1):
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "bin").mkdir()
    (work / "bin/gh").write_text(STUB)
    (work / "bin/gh").chmod(0o755)
    state = work / "state.json"
    state.write_text(json.dumps({"files": list(files), "comments": [list(c) for c in comments], "posted": [], "fail": fail, "pages": pages}))
    out = work / "out"
    env = dict(os.environ, PATH=f"{work / 'bin'}:{os.environ['PATH']}", STUB_STATE=str(state), GITHUB_OUTPUT=str(out),
               RUNNER_TEMP=str(work), GH_TOKEN="t", REPO="o/r", PR_NUMBER="7")
    p = subprocess.run(["bash", "-e", "-c", SCRIPT], cwd=work, env=env, capture_output=True, text=True)
    posted = json.loads(state.read_text())["posted"]
    output = out.read_text() if out.exists() else ""
    shutil.rmtree(work)
    return p, posted, output


print("skip notice")
p, posted, output = run([".github/workflows/claude.yml", "src/a.py"])
check("15.1 skipped, PR edits a workflow: one comment naming the cause, a warning, reviewed=false",
      p.returncode == 0 and len(posted) == 1 and MARKER in posted[0] and ".github/workflows/" in posted[0]
      and "::warning::Claude did not review" in p.stdout and "reviewed=false" in output, (p.stdout, p.stderr, posted))
p, posted, output = run(["src/a.py"])
check("15.2 skipped, no workflow edit: one comment pointing at the log",
      p.returncode == 0 and len(posted) == 1 and "this run's log says why" in posted[0] and ".github/workflows/" not in posted[0], posted)
p, posted, output = run([".github/workflows/claude.yml"], comments=[("someone", "hello"), ("github-actions[bot]", f"{MARKER}\n\n**Claude did not review this pull request.** old")])
check("15.3 already said on this PR: no second comment, still warns",
      p.returncode == 0 and posted == [] and "::warning::" in p.stdout and "not posting again" in p.stdout, (p.stdout, posted))
check("15.4 the step runs only when the review step succeeded and Claude set no conclusion",
      IF == "if: success() && steps.claude-review.outputs.conclusion == ''", IF)
slack = WORKFLOW.read_text().split("      - name: Notify Slack", 1)[1]
check("15.4 Slack says not reviewed when the step ran", "steps.skipped.outputs.reviewed == 'false'" in slack and "Not reviewed" in slack, slack[:300])
p, posted, output = run([".github/workflows/claude.yml"], fail=True)
check("15.5 a failing gh call fails the step instead of passing quietly", p.returncode != 0 and posted == [], (p.returncode, p.stderr[-200:]))
p, posted, output = run([".github/workflows/claude.yml"], comments=[("pr-author", f"{MARKER} planted by someone else")])
check("15.6 a marker posted by anyone but the workflow does not silence the notice", len(posted) == 1, (p.stdout, posted))
p, posted, output = run(["a.py", "b.py", ".github/workflows/claude.yml", "c.py"], pages=3,
                        comments=[("x", "a"), ("github-actions[bot]", MARKER), ("y", "b")])
check("15.8 counts are summed across pages: a workflow file on page 2 and the marker on page 2 are both seen",
      posted == [] and "changes a file under" in p.stdout, (p.stdout, posted))

import re
filters = re.findall(r"--jq (\"[^\n]*\"|'[^\n]*')", SCRIPT)
if shutil.which("jq") and len(filters) == 2:
    marker_filter = filters[0][1:-1].replace('\\"', '"').replace("$marker", MARKER)
    files_filter = filters[1][1:-1]
    def jq(f, data):
        return subprocess.run(["jq", f], input=json.dumps(data), capture_output=True, text=True).stdout.strip()
    check("15.7 the real comment filter counts marker comments",
          jq(marker_filter, [{"body": "x", "user": {"login": "github-actions[bot]"}}, {"body": MARKER + " old", "user": {"login": "github-actions[bot]"}},
                             {"body": MARKER, "user": {"login": "someone"}}]) == "1", marker_filter)
    check("15.7 the real files filter counts workflow files only",
          jq(files_filter, [{"filename": ".github/workflows/a.yml"}, {"filename": "src/.github/workflows/b.yml"}, {"filename": "a.py"}]) == "1", files_filter)
else:
    check("15.7 jq available and both filters found", False, (shutil.which("jq"), filters))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
