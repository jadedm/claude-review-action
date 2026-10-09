#!/usr/bin/env python3
"""Cases for the checkout-read guard in both review workflows (sandbox#62).

Runs the real step scripts, cut out of the workflow files, against a temporary
checkout full of hostile symlinks. Only /tmp is redirected to a scratch folder.

    python3 scripts/test_safe_path.py
    WORKFLOWS=<dir> python3 scripts/test_safe_path.py   # run against other workflow files
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.environ.get("WORKFLOWS", os.path.join(HERE, "..", ".github", "workflows"))
SECRET = "OUTSIDE-THE-CHECKOUT-7f3a"
GIT_SECRET = "INSIDE-DOT-GIT-51c2"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {detail}")


def step_script(workflow, step_name):
    """The `run: |` body of the named step, dedented."""
    lines = open(os.path.join(WORKFLOWS, workflow)).read().split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {step_name}")
    run = next(i for i in range(start, len(lines)) if re.match(r"\s+run: \|$", lines[i]))
    run_indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        body.append(line)
    indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
    return "\n".join(l[indent:] for l in body)


def guard(script):
    """The WORKSPACE line and safe_to_read function from a step script, or ''."""
    m = re.search(r"^WORKSPACE=.*?\nsafe_to_read\(\) \{\n.*?\n\}\n", script, re.S | re.M)
    return m.group(0) if m else ""


GEMINI_STEP = step_script("gemini-review.yml", "Build review payload")
CLAUDE_STEP = step_script("claude-review.yml", "Compose prompt")


def build_checkout(root):
    """A checkout with regular files, hostile symlinks and the prompts repo."""
    ws = os.path.join(root, "repo")
    outside = os.path.join(root, "outside")
    evil = os.path.join(root, "repo-evil")  # shares the checkout's path as a prefix
    for d in (ws, outside, evil, os.path.join(ws, "src"), os.path.join(ws, ".git")):
        os.makedirs(d)
    for path in (os.path.join(outside, "secret.txt"), os.path.join(evil, "f.txt"), os.path.join(outside, "extras.md")):
        open(path, "w").write(SECRET + "\n")
    open(os.path.join(ws, ".git", "config"), "w").write(GIT_SECRET + "\n")
    open(os.path.join(ws, "normal.py"), "w").write("print('normal-file-body')\n")
    open(os.path.join(ws, "with space.py"), "w").write("print('spaced-file-body')\n")
    open(os.path.join(ws, "--bogus"), "w").write("dash-file-body\n")
    open(os.path.join(ws, "-"), "w").write("lone-dash-body\n")
    open(os.path.join(ws, "notes.md"), "w").write("in-repo-extras\n")
    open(os.path.join(ws, "src", "deep.py"), "w").write("deep-file-body\n")
    os.symlink(os.path.join(outside, "secret.txt"), os.path.join(ws, "leak.py"))  # 1
    os.symlink("normal.py", os.path.join(ws, "inner.py"))                         # 2
    os.symlink("src", os.path.join(ws, "linkdir"))                                # 3
    os.symlink(outside, os.path.join(ws, "dirlink"))                              # 9
    os.symlink(evil, os.path.join(ws, "evil"))                                    # 10
    os.symlink(os.path.join(outside, "extras.md"), os.path.join(ws, "extras.md"))  # 8
    os.symlink(".git/config", os.path.join(ws, "gitcfg.py"))                      # 11
    os.symlink("notes.md", os.path.join(ws, "extras-link.md"))                    # in-repo extras link
    os.symlink("missing.py", os.path.join(ws, "broken.py"))                       # dangling link
    prompts = os.path.join(ws, ".claude-review-action", "prompts")
    os.makedirs(prompts)
    for name in ("base.md", "python.md", "_gemini-tail.md"):
        open(os.path.join(prompts, name), "w").write(f"[{name}]\n")
    return ws


def run_step(ws, tmp, script, env):
    script = script.replace("/tmp/", tmp + "/")
    full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), **env}
    return subprocess.run(["bash", "-c", script], cwd=ws, env=full_env, capture_output=True, text=True)


def safe(ws, path):
    script = guard(GEMINI_STEP) + 'safe_to_read "$1"'
    return subprocess.run(["bash", "-c", script, "_", path], cwd=ws).returncode == 0


def read(path):
    return open(path).read() if os.path.exists(path) else ""


print("one guard, two workflows")
check("  gemini step defines safe_to_read", guard(GEMINI_STEP) != "")
check("  claude step defines the same safe_to_read", guard(CLAUDE_STEP) == guard(GEMINI_STEP) != "",
      "the two copies differ or one is missing")

root = os.path.realpath(tempfile.mkdtemp())
try:
    ws = build_checkout(root)
    tmp = os.path.join(root, "tmp")
    os.makedirs(tmp)

    print("safe_to_read")
    check("4 regular file", safe(ws, "normal.py"))
    check("  regular file with a space", safe(ws, "with space.py"))
    check("  file in a subfolder", safe(ws, "src/deep.py"))
    check("2 symlink to a file inside the checkout is allowed", safe(ws, "inner.py"))
    check("1 symlink to a file outside the checkout", not safe(ws, "leak.py"))
    check("3 symlink to a directory", not safe(ws, "linkdir"))
    check("9 regular file under a symlinked directory outside", not safe(ws, "dirlink/secret.txt"))
    check("10 path that only shares a prefix with the checkout", not safe(ws, "evil/f.txt"))
    check("11 symlink into .git", not safe(ws, "gitcfg.py"))
    check("  .git file named directly", not safe(ws, ".git/config"))
    check("  missing file", not safe(ws, "deleted.py"))
    check("  broken link", not safe(ws, "broken.py"))
    check("  absolute path outside", not safe(ws, os.path.join(root, "outside", "secret.txt")))
    check("  .. out of the checkout", not safe(ws, "src/../../outside/secret.txt"))

    print("gemini: Build review payload")
    changed = ["normal.py", "with space.py", "--bogus", "inner.py", "leak.py", "linkdir", "dirlink/secret.txt",
               "evil/f.txt", "gitcfg.py", "broken.py", "deleted.py", "src", os.path.join(root, "outside", "secret.txt"),
               "-", "src/deep.py"]
    open(os.path.join(tmp, "changed-files.txt"), "w").write("\n".join(changed) + "\n")
    open(os.path.join(tmp, "pr-diff.txt"), "w").write("diff --git a/normal.py b/normal.py\n")
    env = {"MAX_TOTAL_LINES": "5000", "MAX_PER_FILE": "1000", "MAX_FILES": "20", "STACK": "python",
           "EXTRA_PROMPT": "", "EXTRA_PROMPT_PATH": "extras.md"}
    result = run_step(ws, tmp, GEMINI_STEP, env)
    check("  step exits 0", result.returncode == 0, result.stderr[-500:])
    code = read(os.path.join(tmp, "review-code.txt"))
    prompt = read(os.path.join(tmp, "review-prompt.txt"))
    check("4 regular files are sent", all(s in code for s in ["normal-file-body", "spaced-file-body", "deep-file-body"]), code[:300])
    check("  files named --bogus and - are sent as files", "dash-file-body" in code and "lone-dash-body" in code, code[:400])
    check("  a file after '-' in the list is still sent", "--- src/deep.py ---" in code, code[-300:])
    check("2 the in-repo symlink is sent", "--- inner.py ---" in code, code[:300])
    check("1,9,10 nothing from outside the checkout is sent", SECRET not in code, code[:300])
    check("11 nothing from .git is sent", GIT_SECRET not in code, code[:300])
    check("  each refused path gets a notice", all(f"'{p}'" in result.stdout for p in
          ["leak.py", "dirlink/secret.txt", "evil/f.txt", "gitcfg.py", "broken.py"]), result.stdout[-800:])
    check("  deleted files and folders get no notice", not any(f"'{p}'" in result.stdout for p in
          ["deleted.py", "src", "linkdir"]), result.stdout[-800:])
    check("8 extra_prompt_path symlinked outside is not read", SECRET not in prompt, prompt[:300])
    check("  and says so in a warning", "extra_prompt_path 'extras.md'" in result.stderr, result.stderr[-300:])

    env["EXTRA_PROMPT_PATH"] = "extras-link.md"
    result = run_step(ws, tmp, GEMINI_STEP, env)
    prompt = read(os.path.join(tmp, "review-prompt.txt"))
    check("  an in-repo symlinked extra_prompt_path is read", result.returncode == 0 and "in-repo-extras" in prompt,
          result.stderr[-300:] + prompt[:200])

    print("claude: Compose prompt")
    out = os.path.join(tmp, "github_output")
    env = {"STACK": "python", "EXTRA_PROMPT": "", "EXTRA_PROMPT_PATH": "extras.md", "REPO_FULL_NAME": "o/r",
           "PR_NUMBER": "1", "GITHUB_OUTPUT": out}
    result = run_step(ws, tmp, CLAUDE_STEP, env)
    check("  step exits 0", result.returncode == 0, result.stderr[-500:])
    composed = read(os.path.join(tmp, "prompt.md"))
    check("8 extra_prompt_path symlinked outside is not read", SECRET not in composed, composed[:300])
    env["EXTRA_PROMPT_PATH"] = "notes.md"
    result = run_step(ws, tmp, CLAUDE_STEP, env)
    composed = read(os.path.join(tmp, "prompt.md"))
    check("  a regular extra_prompt_path is still read", result.returncode == 0 and "in-repo-extras" in composed,
          result.stderr[-300:] + composed[:200])
finally:
    shutil.rmtree(root)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
