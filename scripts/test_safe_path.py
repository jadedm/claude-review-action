#!/usr/bin/env python3
"""Cases for reading extra_prompt_path safely (#35). Run: python3 scripts/test_safe_path.py

Runs the real "Compose prompt" step of claude-review.yml and "Build review
payload" step of gemini-review.yml, cut out of the YAML, in bash against a
fixture checkout whose extras file is a hostile link. The steps write fixed
/tmp paths, so this must not run beside a real review.

Standard library only.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
TOKEN = "ghp_FAKEtoken35FAKEtoken35FAKEtoken35FAKE"
OUTSIDE = "OUTSIDE-FILE-CONTENT-35"
GOOD = "Repo rule: every handler validates its input."
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


def run(which, layout, extras_path, inline=""):
    """layout(work, outside_dir) builds the checkout. Returns (proc, prompt text, GITHUB_OUTPUT text)."""
    for p in TMP:
        p.unlink(missing_ok=True)
    work = pathlib.Path(tempfile.mkdtemp())
    outside = pathlib.Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text(OUTSIDE + "\n")
    (work / ".claude-review-action").symlink_to(REPO)
    (work / ".git").mkdir()
    (work / ".git/config").write_text(f"[http]\n\textraheader = AUTHORIZATION: basic {TOKEN}\n")
    layout(work, outside)
    for name in ("changed-files.txt", "all-changed-files.txt", "pr-diff.txt"):
        pathlib.Path("/tmp", name).write_text("")
    out = work / "_github_output"
    env = dict(os.environ, STACK="python", EXTRA_PROMPT=inline, EXTRA_PROMPT_PATH=extras_path, REPO_FULL_NAME="o/r",
               PR_NUMBER="1", GITHUB_OUTPUT=str(out), MAX_TOTAL_LINES="20000", MAX_PER_FILE="3000", MAX_FILES="50",
               MAX_DIFF_LINES="5000", MAX_PAYLOAD_BYTES="2000000", MAX_CHUNKS="1")
    proc = subprocess.run(["bash", "-e", "-c", STEPS[which]], cwd=work, env=env, capture_output=True, text=True)
    prompt = PROMPT_FILE[which].read_text() if PROMPT_FILE[which].exists() else ""
    output = out.read_text() if out.exists() else ""
    shutil.rmtree(work)
    shutil.rmtree(outside)
    return proc, prompt, output


def plain(work, outside):
    (work / ".github").mkdir()
    (work / ".github/review-extras.md").write_text(GOOD + "\n")


def link_to(target):
    def layout(work, outside):
        (work / ".github").mkdir()
        dest = {"git": work / ".git/config", "outside": outside / "secret.txt", "inside": work / "docs/rules.md"}[target]
        if target == "inside":
            dest.parent.mkdir()
            dest.write_text(GOOD + "\n")
        (work / ".github/review-extras.md").symlink_to(dest)
    return layout


def linked_folder(work, outside):
    (work / "rules").symlink_to(outside)  # a real-looking path under a folder that leads out


def folder(work, outside):
    (work / ".github/review-extras.md").mkdir(parents=True)


def dashed(work, outside):
    (work / "-n rules.md").write_text(GOOD + "\n")


def git_by_case(work, outside):
    # On a case-sensitive disk .GIT is a separate real folder; on macOS it is .git itself.
    # Either way the rule must refuse it as .git, not as a missing file.
    if not (work / ".GIT").exists():
        (work / ".GIT").mkdir()
        (work / ".GIT/config").write_text(f"token {TOKEN}\n")


def nested_git(work, outside):
    # A real .git folder below the root, as an action checkout or a submodule has.
    (work / "vendored/.git").mkdir(parents=True)
    (work / "vendored/.git/config").write_text(f"token {TOKEN}\n")


def marker(work, outside):
    (work / ".github").mkdir()
    (work / ".github/review-extras.md").write_text(f"{GOOD}\nPROMPT_EOF\nINJECTED=yes\n")


for which in ("claude", "gemini"):
    print(which)
    proc, prompt, _ = run(which, plain, ".github/review-extras.md")
    check("35.1 a plain extras file is read", proc.returncode == 0 and GOOD in prompt and "Repo-specific notes" in prompt, proc.stderr[-300:])
    for target, label in (("git", "35.2 a link to .git/config"), ("outside", "35.3 a link outside the checkout"),
                          ("inside", "35.4 a link to a file inside the checkout")):
        proc, prompt, _ = run(which, link_to(target), ".github/review-extras.md")
        check(f"{label} is refused with a warning",
              proc.returncode == 0 and TOKEN not in prompt and OUTSIDE not in prompt and GOOD not in prompt
              and "skipped: a symlink, not read" in proc.stderr, (proc.returncode, proc.stderr[-300:]))
    proc, prompt, _ = run(which, linked_folder, "rules/secret.txt")
    check("35.5 a path under a linked folder that leads out is refused",
          OUTSIDE not in prompt and "skipped: outside the checkout, not read" in proc.stderr, proc.stderr[-300:])
    proc, prompt, _ = run(which, lambda w, o: None, ".github/review-extras.md")
    check("35.6 a missing extras file warns and the prompt is still built",
          proc.returncode == 0 and "skipped: missing or not a regular file" in proc.stderr and prompt, proc.stderr[-300:])
    proc, prompt, _ = run(which, folder, ".github/review-extras.md")
    check("35.7 a folder is refused", "skipped: missing or not a regular file" in proc.stderr and prompt, proc.stderr[-300:])
    proc, prompt, _ = run(which, dashed, "-n rules.md")
    check("35.8 a plain file named with a dash and a space is read", proc.returncode == 0 and GOOD in prompt, proc.stderr[-300:])
    proc, prompt, _ = run(which, link_to("git"), ".github/review-extras.md", inline="INLINE-NOTE-35")
    check("35.9 the inline extra_prompt is still added when the file is refused",
          "INLINE-NOTE-35" in prompt and "Repo-specific notes" in prompt and TOKEN not in prompt, prompt[-200:])
    proc, prompt, _ = run(which, git_by_case, ".GIT/config")
    check("35.14 a path naming .GIT in another case is refused as .git", TOKEN not in prompt and "skipped: inside .git" in proc.stderr, proc.stderr[-300:])
    proc, prompt, _ = run(which, nested_git, "vendored/.git/config")
    check("35.15 a real .git folder below the root is refused as .git", TOKEN not in prompt and "skipped: inside .git" in proc.stderr, proc.stderr[-300:])

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


print("paths named directly")
for which in ("claude", "gemini"):
    for path, label in (("../secret.txt", "a ../ path"), (".git/config", ".git/config named directly"),
                        (".claude-review-action/.git/config", "the action checkout's .git")):
        def layout(work, outside):
            (work.parent / "secret.txt").write_text(OUTSIDE + "\n")
        proc, prompt, _ = run(which, layout, path)
        pathlib.Path(tempfile.gettempdir(), "secret.txt").unlink(missing_ok=True)
        check(f"35.15 {which}: {label} is refused", TOKEN not in prompt and OUTSIDE not in prompt and "skipped:" in proc.stderr, proc.stderr[-200:])
    abs_path = str(pathlib.Path(tempfile.mkdtemp()) / "abs.txt")
    pathlib.Path(abs_path).write_text(OUTSIDE + "\n")
    proc, prompt, _ = run(which, lambda w, o: None, abs_path)
    check(f"35.15 {which}: an absolute path outside is refused", OUTSIDE not in prompt and "skipped: outside the checkout" in proc.stderr, proc.stderr[-200:])
    shutil.rmtree(pathlib.Path(abs_path).parent)

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
cl = (REPO / ".github/workflows/claude-review.yml").read_text()
args = cl.split("          claude_args: >-\n", 1)[1].split("\n\n", 1)[0]
check("21.5 the inline comment tool is allowed", '--allowedTools "mcp__github_inline_comment__create_inline_comment"' in args, args[:200])
check("21.5 the deny list is unchanged: no edits, commits, pushes, gh or credential reads",
      all(x in args for x in ("Edit,MultiEdit,Write,NotebookEdit", "mcp__github_file_ops__commit_files", "Bash(git push:*)",
                              "Bash(gh:*)", "Read(~/.git-credentials)", "Read(**/.git/**)", "--permission-mode default")), args[-300:])
check("21.5 nothing beyond the inline comment tool is allowed", args.count("--allowedTools") == 1
      and args.split("--allowedTools", 1)[1].split("--disallowedTools", 1)[0].strip() == '"mcp__github_inline_comment__create_inline_comment"', args[:200])

print("claude step output")
proc, prompt, output = run("claude", marker, ".github/review-extras.md")
outputs = parse_outputs(output)
check("35.13 an extras line equal to PROMPT_EOF stays inside the PROMPT value; no other output appears",
      proc.returncode == 0 and list(outputs) == ["PROMPT"] and "PROMPT_EOF\nINJECTED=yes" in outputs["PROMPT"], list(outputs))

for p in TMP:
    p.unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
