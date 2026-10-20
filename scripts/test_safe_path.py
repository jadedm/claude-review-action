#!/usr/bin/env python3
"""Cases for how the review steps read the caller's files (#35, #37) and related hardening.

Run: python3 scripts/test_safe_path.py

Runs the real "Compose prompt" step of claude-review.yml and "Build review
payload" step of gemini-review.yml, cut out of the YAML, in bash against a
fixture checkout, with a local HTTP server playing the GitHub API. Since #37
the extras file comes from the pull request's base commit through the API;
the checkout's copy, which the pull request controls, is never read. The
steps write fixed /tmp paths, so this must not run beside a real review.

Standard library only.
"""
import base64
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
TOKEN = "ghp_FAKEtoken35FAKEtoken35FAKEtoken35FAKE"
OUTSIDE = "OUTSIDE-FILE-CONTENT-35"
GOOD = "PR COPY: every handler may skip validation."
BASE_RULES = "BASE RULES: every handler validates its input."
TMP = [pathlib.Path("/tmp", n) for n in ("prompt.md", "review-prompt.txt", "changed-files.txt", "all-changed-files.txt",
                                         "pr-diff.txt", "review-coverage.json")]

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def step_script(workflow, name):
    lines = (REPO / ".github/workflows" / workflow).read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {name}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- name:")), len(lines))
    inline = [l.strip()[len("run: "):] for l in lines[start:end] if l.strip().startswith("run: ") and l.strip() != "run: |"]
    if inline:
        return inline[0] + "\n"  # a one-line run:
    run = next(i for i in range(start, end) if lines[i].strip() == "run: |")
    body, indent = [], None
    for line in lines[run + 1:]:
        if line.strip() and indent is None:
            indent = len(line) - len(line.lstrip())
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    return "\n".join(body) + "\n"


STEPS = {"claude": step_script("claude-review.yml", "Compose prompt"),
         "gemini": step_script("gemini-review.yml", "Build review payload")}
PROMPT_FILE = {"claude": pathlib.Path("/tmp/prompt.md"), "gemini": pathlib.Path("/tmp/review-prompt.txt")}
BASE_SHA = "b" * 40
API_STATE = {"files": {}, "status": None, "requests": []}


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        API_STATE["requests"].append(self.path)
        if API_STATE["status"]:
            return self.send(API_STATE["status"], {"message": "boom"})
        if self.path == "/repos/o/r/pulls/1":
            return self.send(200, {"base": {"sha": BASE_SHA}})
        if self.path == "/repos/o/r":
            return self.send(200, {"default_branch": "main"})  # its contents (?ref=main) answer 404 below
        prefix, suffix = "/repos/o/r/contents/", f"?ref={BASE_SHA}"
        if self.path.startswith(prefix) and self.path.endswith(suffix):
            text = API_STATE["files"].get(self.path[len(prefix):-len(suffix)])
            if text is not None:
                raw = text.encode()
                return self.send(200, {"type": "file", "encoding": "base64", "size": len(raw),
                                       "content": base64.b64encode(raw).decode()})
        return self.send(404, {"message": "Not Found"})


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{SERVER.server_address[1]}"


def run(which, layout, extras_path, inline="", base_files=None, status=None):
    """layout(work, outside_dir) builds the checkout; base_files is what the base commit holds.
    Returns (proc, prompt text, GITHUB_OUTPUT text)."""
    for p in TMP:
        p.unlink(missing_ok=True)
    API_STATE.update(files={".github/review-extras.md": BASE_RULES + "\n"} if base_files is None else base_files,
                     status=status, requests=[])
    work = pathlib.Path(tempfile.mkdtemp())
    outside = pathlib.Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text(OUTSIDE + "\n")
    (work / ".claude-review-action").symlink_to(REPO)
    (work / ".git").mkdir()
    (work / ".git/config").write_text(f"[http]\n\textraheader = AUTHORIZATION: basic {TOKEN}\n")
    (work / "_tmp").mkdir()
    layout(work, outside)
    for name in ("changed-files.txt", "all-changed-files.txt", "pr-diff.txt"):
        pathlib.Path("/tmp", name).write_text("")
    out = work / "_github_output"
    env = dict(os.environ, STACK="python", EXTRA_PROMPT=inline, EXTRA_PROMPT_PATH=extras_path, REPO_FULL_NAME="o/r",
               PR_NUMBER="1", GITHUB_OUTPUT=str(out), MAX_TOTAL_LINES="20000", MAX_PER_FILE="3000", MAX_FILES="50",
               MAX_DIFF_LINES="5000", MAX_PAYLOAD_BYTES="2000000", MAX_CHUNKS="1", GH_TOKEN="tok",
               GITHUB_API_URL=API, RUNNER_TEMP=str(work / "_tmp"))
    proc = subprocess.run(["bash", "-e", "-c", STEPS[which]], cwd=work, env=env, capture_output=True, text=True)
    prompt = PROMPT_FILE[which].read_text() if PROMPT_FILE[which].exists() else ""
    output = out.read_text() if out.exists() else ""
    shutil.rmtree(work)
    shutil.rmtree(outside)
    return proc, prompt, output


def pr_copy(work, outside):
    (work / ".github").mkdir()
    (work / ".github/review-extras.md").write_text(GOOD + "\n")


def pr_link_to_git(work, outside):
    (work / ".github").mkdir()
    (work / ".github/review-extras.md").symlink_to(work / ".git/config")


def marker(work, outside):
    pass


plain = pr_copy  # the planted-module cases below build on a checkout with a PR copy

for which in ("claude", "gemini"):
    print(which)
    proc, prompt, _ = run(which, pr_copy, ".github/review-extras.md")
    check("37.6 the base commit's rules are used and the pull request's copy is not",
          proc.returncode == 0 and BASE_RULES in prompt and GOOD not in prompt and "## Repo-specific notes" in prompt,
          (proc.returncode, proc.stdout[-200:], proc.stderr[-200:]))
    proc, prompt, _ = run(which, pr_link_to_git, ".github/review-extras.md")
    check("37.6 a pull request's copy that is a link to .git/config is never read",
          proc.returncode == 0 and TOKEN not in prompt and BASE_RULES in prompt, proc.stderr[-200:])
    proc, prompt, _ = run(which, pr_copy, ".github/review-extras.md", base_files={})
    check("37.2 the base has no rules file: warned, prompt built without it, the PR's copy still not read",
          proc.returncode == 0 and "not on the base branch" in proc.stdout + proc.stderr and GOOD not in prompt and prompt, proc.stdout[-200:])
    proc, prompt, _ = run(which, pr_copy, ".github/review-extras.md", inline="INLINE-NOTE-35", base_files={})
    check("35.9 the inline extra_prompt is still added when there is no rules file",
          "INLINE-NOTE-35" in prompt and "## Repo-specific notes" in prompt, prompt[-200:])
    proc, prompt, _ = run(which, pr_copy, ".github/review-extras.md", base_files={})
    check("37.10 no rules file and no inline note: no empty Repo-specific notes heading",
          proc.returncode == 0 and "## Repo-specific notes" not in prompt and prompt, prompt[-200:])
    proc, prompt, _ = run(which, pr_copy, ".github/review-extras.md", status=500)
    check("37.4 the API failing fails the step rather than reviewing without the rules",
          proc.returncode != 0 and "could not read the review rules" in proc.stdout + proc.stderr, (proc.returncode, proc.stdout[-200:]))
    for path, label in (("../secret.txt", "a ../ path"), ("/etc/hosts", "an absolute path")):
        proc, prompt, _ = run(which, pr_copy, path)
        check(f"37.5 {label} is refused before any request and fails the step",
              proc.returncode != 0 and OUTSIDE not in prompt and "not a plain path" in proc.stdout + proc.stderr
              and not any("/contents/" in r for r in API_STATE["requests"]), (API_STATE["requests"], proc.stdout[-200:]))

def parse_outputs(text):
    """GITHUB_OUTPUT read as the runner reads it: name=value, or name<<END ... END."""
    outputs, lines, i = {}, text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            name, end = line.split("<<", 1)
            j = lines.index(end, i + 1)
            outputs[name] = "\n".join(lines[i + 1:j])
            i = j + 1
            continue
        if "=" in line:
            name, value = line.split("=", 1)
            outputs[name] = value
        i += 1
    return outputs


print("the checkout path rule gemini_chunks.py reads files through (safe_path.readable)")
sys.path.insert(0, str(HERE))
import safe_path  # noqa: E402

def rule(layout, path):
    work = pathlib.Path(tempfile.mkdtemp()); outside = pathlib.Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text(OUTSIDE)
    (work / ".git").mkdir(); (work / ".git/config").write_text(TOKEN)
    layout(work, outside)
    here = os.getcwd(); os.chdir(work)
    try:
        return safe_path.readable(path), safe_path.main(path)
    finally:
        os.chdir(here); shutil.rmtree(work); shutil.rmtree(outside)

def mk(*files):
    def layout(work, outside):
        for f in files:
            (work / f).parent.mkdir(parents=True, exist_ok=True); (work / f).write_text("x")
    return layout

cases = [
    ("35.1 a plain file", mk("a.py"), "a.py", None, 0),
    ("35.2 a link to .git/config", lambda w, o: (w / "l.py").symlink_to(w / ".git/config"), "l.py", "a symlink, not read", 1),
    ("35.3 a link outside", lambda w, o: (w / "l.py").symlink_to(o / "secret.txt"), "l.py", "a symlink, not read", 1),
    ("35.5 a path under a linked folder that leads out", lambda w, o: (w / "rules").symlink_to(o), "rules/secret.txt", "outside the checkout, not read", 1),
    ("35.7 a folder", lambda w, o: (w / "d").mkdir(), "d", None, 1),
    ("35.14 .GIT in another case", lambda w, o: (w / ".GIT").mkdir(exist_ok=True) or (w / ".GIT/config").write_text("t"), ".GIT/config", "inside .git, not read", 1),
    ("35.15 a nested .git", mk("vendored/.git/config"), "vendored/.git/config", "inside .git, not read", 1),
    ("35.15 .git/config named directly", lambda w, o: None, ".git/config", "inside .git, not read", 1),
    ("35.15 a ../ path", lambda w, o: None, "../secret.txt", "outside the checkout, not read", 1),
    ("35.15 an absolute path outside", lambda w, o: None, "/etc/hosts", "outside the checkout, not read", 1),
]
for label, layout, path, want_reason, want_rc in cases:
    reason, rc = rule(layout, path)
    check(f"{label}: {want_reason or ('readable' if want_rc == 0 else 'not a regular file')}", reason == want_reason and rc == want_rc, (reason, rc))

print("job guards")
for wf in ("claude-review.yml", "gemini-review.yml"):
    text = (REPO / ".github/workflows" / wf).read_text()
    guard = text.split("    if: >\n", 1)[1].split("    runs-on:", 1)[0]
    check(f"35.16 {wf}: the job guard admits pull_request and comments only, never pull_request_target",
          "github.event_name == 'pull_request' ||" in guard and "pull_request_target" not in guard, guard[:200])

print("modules planted in the checkout (#35)")
PLANT = "import os\nopen(os.environ['PLANT_MARK'], 'a').write(__name__ + '\\n')\n"


def planted(work, outside):
    plain(work, outside)
    for name in ("json.py", "secrets.py", "sys.py", "io.py"):
        (work / name).write_text(PLANT)


for which in ("claude", "gemini"):
    mark = pathlib.Path(tempfile.mkdtemp()) / "imported"
    os.environ["PLANT_MARK"] = str(mark)
    proc, prompt, _ = run(which, planted, ".github/review-extras.md")
    check(f"35.17 {which}: a json.py or secrets.py in the checkout is never imported by the step",
          proc.returncode == 0 and not mark.exists(), mark.read_text() if mark.exists() else proc.stderr[-200:])
    shutil.rmtree(mark.parent)


def inline_calls():
    """Every inline python3 call in both workflows."""
    calls = []
    for wf in ("claude-review.yml", "gemini-review.yml"):
        for n, line in enumerate((REPO / ".github/workflows" / wf).read_text().splitlines(), 1):
            if "python3 -c" in line or "python3 -I -c" in line or "python3 - " in line:
                calls.append((wf, n, line.strip()))
    return calls


check("35.18 every inline python3 call runs isolated (-I), so the checkout is not on its import path",
      inline_calls() and all("python3 -I " in line for _, _, line in inline_calls()),
      [(wf, n) for wf, n, line in inline_calls() if "python3 -I " not in line])

print("comment runs check out the pull request (#16)")
gem = (REPO / ".github/workflows/gemini-review.yml").read_text()
step = gem.split("      - name: Checkout consumer repo\n", 1)[1].split("      - name:", 1)[0]
check("16.1 an issue comment checks out refs/pull/<n>/head; other events keep their own ref",
      "ref: ${{ github.event_name == 'issue_comment' && format('refs/pull/{0}/head', github.event.issue.number) || '' }}" in step, step)
check("16.7 the step still keeps no credentials and full history",
      "persist-credentials: false" in step and "fetch-depth: 0" in step, step)
for wf in ("claude-review.yml", "gemini-review.yml"):
    guard = (REPO / ".github/workflows" / wf).read_text().split("    if: >\n", 1)[1].split("    runs-on:", 1)[0]
    check(f"16.9 {wf}: a comment needs a pull request and, on a public repo, a role in it",
          "github.event.issue.pull_request" in guard and '["OWNER","MEMBER","COLLABORATOR"]' in guard
          and "github.event.repository.private" in guard and "author_association" in guard, guard)

print("a .claude-review-action in the pull request (#16)")
for wf in ("claude-review.yml", "gemini-review.yml"):
    try:
        clear = step_script(wf, "Clear the action folder")
    except StopIteration:
        check(f"16.10 {wf}: a step clears .claude-review-action before the action checkout", False, "no such step")
        continue
    text = (REPO / ".github/workflows" / wf).read_text()
    check(f"16.10 {wf}: the clearing step runs before the action checkout",
          text.index("- name: Clear the action folder") < text.index("- name: Checkout review action repo"), "order")
    work = pathlib.Path(tempfile.mkdtemp()); outside = pathlib.Path(tempfile.mkdtemp())
    (outside / "keep.txt").write_text("runner file\n")
    (work / ".claude-review-action").symlink_to(outside)
    p = subprocess.run(["bash", "-e", "-c", clear], cwd=work, capture_output=True, text=True)
    check(f"16.10 {wf}: a symlinked .claude-review-action is removed, its target left intact",
          p.returncode == 0 and not os.path.lexists(work / ".claude-review-action") and (outside / "keep.txt").exists(), p.stderr)
    (work / ".claude-review-action").mkdir(); (work / ".claude-review-action/planted.py").write_text("x = 1\n")
    p = subprocess.run(["bash", "-e", "-c", clear], cwd=work, capture_output=True, text=True)
    check(f"16.10 {wf}: a real .claude-review-action folder from the pull request is removed",
          p.returncode == 0 and not os.path.lexists(work / ".claude-review-action"), p.stderr)
    shutil.rmtree(work); shutil.rmtree(outside)

print("claude tools (#21)")
DENY = '--disallowedTools "Edit,MultiEdit,Write,NotebookEdit,mcp__github_file_ops__commit_files,mcp__github_file_ops__delete_files,Bash(git commit:*),Bash(git push:*),Bash(git tag:*),Bash(gh:*),Read(//proc/**),Read(~/.claude/**),Read(~/.config/**),Read(~/.ssh/**),Read(~/.docker/**),Read(~/.git-credentials),Read(~/.gitconfig),Read(~/.aws/**),Read(~/.kube/**),Read(~/.azure/**),Read(~/.gnupg/**),Read(~/.npmrc),Read(~/.netrc),Read(~/.pypirc),Read(//**/_temp/_runner_file_commands/**),Read(//**/_temp/git-credentials-*),Read(//**/.credentials),Read(//**/.credentials_rsaparams),Read(//**/.runner),Read(//**/_diag/**),Read(**/.git/**)" --permission-mode default'  # main's deny list before #21, verbatim
cl = (REPO / ".github/workflows/claude-review.yml").read_text().splitlines()
at = next(i for i, l in enumerate(cl) if l.strip() == "claude_args: >-")
block = []
for l in cl[at + 1:]:
    if not l.startswith("            "):  # the folded value is indented 12; anything less ends it
        break
    block.append(l.strip())
args = " ".join(block)  # how YAML folds >- lines of equal indent
check("21.5 the inline comment tool is allowed", args.startswith('--allowedTools "mcp__github_inline_comment__create_inline_comment" '), args[:120])
check("21.5 the deny list is byte for byte what it was", DENY in args, args[-200:])
check("21.5 nothing beyond the inline comment tool is allowed, in either spelling",
      args.count("--allowedTools") == 1 and "--allowed-tools" not in args
      and args == '--allowedTools "mcp__github_inline_comment__create_inline_comment" ' + DENY, args[:200])

print("action checkout ref (#25)")
for wf in ("claude-review.yml", "gemini-review.yml"):
    text = (REPO / ".github/workflows" / wf).read_text()
    step = text.split("      - name: Checkout review action repo (prompts)\n", 1)[1].split("      - name:", 1)[0]
    check(f"25.4 {wf} (text only): the action checkout takes the caller's ref, else the workflow's own commit, else v1, from the workflow's own repo",
          "ref: ${{ inputs.ref || job.workflow_sha || 'v1' }}" in step
          and "repository: ${{ job.workflow_repository || 'jadedm/pr-review-workflows' }}" in step
          and "persist-credentials: false" in step, step)
    check(f"25.5 {wf} (text only): the ref input defaults to empty", "        default: ''" in text.split("      ref:\n", 1)[1].split("      slack_notify:", 1)[0].split("      model:", 1)[0], text.split("      ref:\n", 1)[1][:300])

print("claude step output")
proc, prompt, output = run("claude", marker, ".github/review-extras.md",
                          base_files={".github/review-extras.md": f"{BASE_RULES}\nPROMPT_EOF\nINJECTED=yes\n"})
outputs = parse_outputs(output)
check("35.13 an extras line equal to PROMPT_EOF stays inside the PROMPT value; no other output appears",
      proc.returncode == 0 and list(outputs) == ["PROMPT"] and "PROMPT_EOF\nINJECTED=yes" in outputs["PROMPT"], list(outputs))

for p in TMP:
    p.unlink(missing_ok=True)
SERVER.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
