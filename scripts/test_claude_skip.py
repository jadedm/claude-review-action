#!/usr/bin/env python3
"""Cases for saying when Claude did not review (#15). Run: python3 scripts/test_claude_skip.py

Runs the real "Say when Claude did not review" step of claude-review.yml in
bash against a stub `gh` that serves the pull request's files and comments
from a state file and records what is posted. Standard library only.
"""
import hashlib
import hmac
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github/workflows/claude-review.yml"
OLD_MARKER = "<!-- claude-review-action: not reviewed -->"
KEY = "sk-ant-oat01-test-key"


def keyed(key=KEY, repo="o/r", pr="7"):
    """The marker the step should write, computed here independently (#68)."""
    tag = hmac.new(key.encode(), f"not reviewed {repo}#{pr}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"<!-- claude-review-action: not reviewed {tag} -->"


MARKER = keyed()

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


def run(files, comments=(), fail=False, pages=1, key=KEY, pr="7"):
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "bin").mkdir()
    (work / "bin/gh").write_text(STUB)
    (work / "bin/gh").chmod(0o755)
    state = work / "state.json"
    state.write_text(json.dumps({"files": list(files), "comments": [list(c) for c in comments], "posted": [], "fail": fail, "pages": pages}))
    out = work / "out"
    env = dict(os.environ, PATH=f"{work / 'bin'}:{os.environ['PATH']}", STUB_STATE=str(state), GITHUB_OUTPUT=str(out),
               RUNNER_TEMP=str(work), GH_TOKEN="t", REPO="o/r", PR_NUMBER=pr, MARKER_KEY=key)
    p = subprocess.run(["bash", "-e", "-c", SCRIPT], cwd=work, env=env, capture_output=True, text=True)
    posted = json.loads(state.read_text())["posted"]
    output = out.read_text() if out.exists() else ""
    shutil.rmtree(work)
    return p, posted, output


print("skip notice")
p, posted, output = run([".github/workflows/claude.yml", "src/a.py"])
check("15.1 skipped, PR edits a workflow: one comment pointing at the log, no workflow sentence (#66), a warning, reviewed=false",
      p.returncode == 0 and len(posted) == 1 and MARKER in posted[0] and "this run's log says why" in posted[0]
      and ".github/workflows/" not in posted[0] and "default branch" not in posted[0]
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
print("the notice's own marker (#68)")
# Notices the step itself writes for another pull request and with another key.
_, other_pr, _ = run(["src/a.py"], pr="8")
_, other_key, _ = run(["src/a.py"], key="another-key")
_, same, _ = run(["src/a.py"])
p, posted, output = run(["src/a.py"], comments=[("github-actions[bot]", same[0])])
check("68.2 the step's own notice from an earlier run, same key and pull request, silences the next one",
      p.returncode == 0 and posted == [] and "not posting again" in p.stdout and "::warning::" in p.stdout, (p.returncode, p.stdout, same))
for label, body in (("68.3 the old fixed marker", f"{OLD_MARKER}\n\n**Claude did not review this pull request.** old"),
                    ("68.4 the notice the step wrote on another pull request", other_pr[0]),
                    ("68.5 the notice the step wrote with a different key", other_key[0])):
    p, posted, output = run(["src/a.py"], comments=[("github-actions[bot]", body)])
    check(f"{label}, in a github-actions[bot] comment, does not silence the notice",
          p.returncode == 0 and posted == same, (p.stdout, posted))
p, posted, output = run(["src/a.py"], comments=[("github-actions[bot]", keyed(key=""))], key="")
check("68.7 no key: the earlier-notice check is skipped and the notice is posted",
      p.returncode == 0 and len(posted) == 1 and "not checking for an earlier notice" in p.stdout, (p.stdout, posted))
notice = WORKFLOW.read_text().split("      - name: Say when Claude did not review", 1)[1].split("\n      - name:", 1)[0]
SENTINEL = "sentinel-key-6f1e2d"
leaks, paths = [], []
first = []
for _ in range(2):  # first run posts; second finds that notice and stops
    p, posted, output = run(["src/a.py"], comments=[("github-actions[bot]", b) for b in first], key=SENTINEL)
    paths.append((p.returncode, len(posted), "not posting again" in p.stdout))
    leaks += [where for where, text in (("stdout", p.stdout), ("stderr", p.stderr), ("output", output), ("comment", "".join(posted)))
              if SENTINEL in text]
    first = first or posted
check("68.8 the key comes in through env only, and no output, log or comment holds it, on the posting and the already-said path",
      "          MARKER_KEY: ${{ secrets.claude_oauth_token }}" in notice and "secrets." not in SCRIPT and not leaks
      and paths == [(0, 1, False), (0, 0, True)], (leaks, paths))
tags = {keyed(), keyed(pr="8"), keyed(key="other"), keyed(repo="o/s")}
check("68.9 the marker value is 32 hex characters and differs by pull request, repository and key",
      len(tags) == 4 and all(len(m.split()[-2]) == 32 and int(m.split()[-2], 16) >= 0 for m in tags), tags)
p, posted, output = run(["a.py", "b.py", ".github/workflows/claude.yml", "c.py"], pages=3,
                        comments=[("x", "a"), ("github-actions[bot]", MARKER), ("y", "b")])
check("15.8 counts are summed across pages: the marker on page 2 is seen",
      posted == [] and "not posting again" in p.stdout, (p.stdout, posted))

def step(path, name):
    text = path.read_text().splitlines()
    if f"      - name: {name}" not in text:
        return -1, -1, [], ""
    at = text.index(f"      - name: {name}")
    stop = next(i for i in range(at + 1, len(text)) if text[i].startswith("      - name:"))
    body = text[at:stop]
    run_from = body.index("        run: |")
    return at, stop, body, "\n".join(body[run_from + 1:]).rstrip()


install_at, install_end, install, install_run = step(WORKFLOW, "Install GitHub CLI")
check("50.1 gh is installed directly before the notice", install_end == start, (install_at, install_end, start))
install_if = next((l.strip() for l in install if l.strip().startswith("if:")), None)
check("50.2 the install runs only when the notice does", install_if == IF, (install_if, IF))
_, _, _, gemini_run = step(WORKFLOW.parent / "gemini-review.yml", "Install GitHub CLI")
check("50.3 the install script is the Gemini workflow's, text for text",
      install_run == gemini_run and "dnf install -y gh" in install_run, install_run[:200])

# Runs the real install script with PATH holding only stubs, which log each call
# instead of touching the machine. `dnf install -y gh` and `apt-get install -y gh`
# create a gh stub, as a real install would.
INSTALL = "\n".join(l[10:] for l in install_run.splitlines()) + "\n"
LOGGER = '#!/bin/bash\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'
MAKES_GH = LOGGER + 'if [ "$*" = "install -y gh" ]; then printf "#!/bin/bash\\n" > "$STUB_BIN/gh"; chmod +x "$STUB_BIN/gh"; fi\n'


def run_install(gh=False, dnf=False, apt=False, dnf5=False, curl_fails=False):
    work = pathlib.Path(tempfile.mkdtemp())
    bin_ = work / "bin"
    bin_.mkdir()
    stubs = {
        "sudo": LOGGER + 'exec "$@"\n',
        "install": LOGGER, "tee": LOGGER + "cat > /dev/null\n", "dpkg": LOGGER + "echo amd64\n",
        "curl": LOGGER + ("exit 22\n" if curl_fails else 'while [ $# -gt 1 ]; do [ "$1" = -o ] && echo key > "$2"; shift; done\n'),
    }
    if gh:
        stubs["gh"] = "#!/bin/bash\n"
    if dnf:
        stubs["dnf"] = MAKES_GH + ('if [ "$1 $2" = "config-manager --add-repo" ]; then exit 2; fi\n' if dnf5 else "")
    if apt:
        stubs["apt-get"] = MAKES_GH
    for name, body in stubs.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    for tool in ("mktemp", "rm", "basename", "cat", "chmod"):
        (bin_ / tool).symlink_to(shutil.which(tool))
    log = work / "log"
    log.touch()
    env = {"PATH": str(bin_), "STUB_LOG": str(log), "STUB_BIN": str(bin_), "HOME": str(work)}
    p = subprocess.run([shutil.which("bash"), "-e", "-c", INSTALL], cwd=work, env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines()
    shutil.rmtree(work)
    return p, calls


p, calls = run_install(gh=True, dnf=True, apt=True)
check("50.4 gh already there: no package manager is touched", p.returncode == 0 and calls == [], (p.returncode, calls))
p, calls = run_install(dnf=True)
check("50.5 gh missing, dnf: gh installed, step green", p.returncode == 0 and "dnf install -y gh" in calls, (p.returncode, calls, p.stderr[-300:]))
p, calls = run_install(dnf=True, dnf5=True)
check("50.6 dnf5 without --add-repo: falls back to addrepo, gh installed",
      p.returncode == 0 and any("addrepo --from-repofile=" in c for c in calls) and "dnf install -y gh" in calls, (p.returncode, calls))
p, calls = run_install(apt=True)
check("50.7 gh missing, apt: keyring installed from the checked download, gh installed",
      p.returncode == 0 and any(c.startswith("install -m 0644") for c in calls) and "apt-get install -y gh" in calls, (p.returncode, calls, p.stderr[-300:]))
p, calls = run_install(apt=True, curl_fails=True)
check("50.8 keyring download fails: step red, no keyring or apt source written",
      p.returncode != 0 and not any(c.startswith(("install ", "tee ")) for c in calls), (p.returncode, calls))
p, calls = run_install()
check("50.9 no gh and no dnf or apt-get: step red and says why", p.returncode != 0 and "::error::gh is not installed" in p.stdout, (p.returncode, p.stdout))

import re
filters = re.findall(r"--jq (\"[^\n]*\"|'[^\n]*')", SCRIPT)
if shutil.which("jq") and len(filters) == 1:
    marker_filter = filters[0][1:-1].replace('\\"', '"').replace("$marker", MARKER)
    def jq(f, data):
        return subprocess.run(["jq", f], input=json.dumps(data), capture_output=True, text=True).stdout.strip()
    check("15.7 the real comment filter counts marker comments",
          jq(marker_filter, [{"body": "x", "user": {"login": "github-actions[bot]"}}, {"body": MARKER + " old", "user": {"login": "github-actions[bot]"}},
                             {"body": MARKER, "user": {"login": "someone"}}, {"body": OLD_MARKER, "user": {"login": "github-actions[bot]"}},
                             {"body": keyed(pr="8"), "user": {"login": "github-actions[bot]"}}]) == "1", marker_filter)
else:
    check("15.7 jq available and the comment filter found", False, (shutil.which("jq"), filters))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
