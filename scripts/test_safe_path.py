#!/usr/bin/env python3
"""Cases for safe_path.sh and the steps that read the checkout (sandbox#62).

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


def build_checkout(root):
    """A checkout with regular files, hostile symlinks and the prompts repo."""
    ws = os.path.join(root, "repo")
    outside = os.path.join(root, "outside")
    evil = os.path.join(root, "repo-evil")  # shares the checkout's path as a prefix
    for d in (ws, outside, evil, os.path.join(ws, "src")):
        os.makedirs(d)
    for path in (os.path.join(outside, "secret.txt"), os.path.join(evil, "f.txt"), os.path.join(outside, "extras.md")):
        open(path, "w").write(SECRET + "\n")
    open(os.path.join(ws, "normal.py"), "w").write("print('normal-file-body')\n")
    open(os.path.join(ws, "with space.py"), "w").write("print('spaced-file-body')\n")
    open(os.path.join(ws, "notes.md"), "w").write("in-repo-extras\n")
    os.symlink(os.path.join(outside, "secret.txt"), os.path.join(ws, "leak.py"))  # case 1
    os.symlink("normal.py", os.path.join(ws, "inner.py"))                         # case 2
    os.symlink("src", os.path.join(ws, "linkdir"))                                # case 3
    os.symlink(outside, os.path.join(ws, "dirlink"))                              # case 9
    os.symlink(evil, os.path.join(ws, "evil"))                                    # case 10
    os.symlink(os.path.join(outside, "extras.md"), os.path.join(ws, "extras.md"))  # case 8
    prompts = os.path.join(ws, ".claude-review-action", "prompts")
    os.makedirs(prompts)
    for name in ("base.md", "python.md", "_gemini-tail.md"):
        open(os.path.join(prompts, name), "w").write(f"[{name}]\n")
    scripts = os.path.join(ws, ".claude-review-action", "scripts")
    os.makedirs(scripts)
    shutil.copy(os.path.join(HERE, "safe_path.sh"), scripts)
    return ws


def run_step(ws, tmp, script, env):
    script = script.replace("/tmp/", tmp + "/")
    full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), **env}
    return subprocess.run(["bash", "-c", script], cwd=ws, env=full_env, capture_output=True, text=True)


def safe(ws, path):
    script = '. .claude-review-action/scripts/safe_path.sh; safe_to_read "$1"'
    return subprocess.run(["bash", "-c", script, "_", path], cwd=ws).returncode == 0


root = os.path.realpath(tempfile.mkdtemp())
try:
    ws = build_checkout(root)
    tmp = os.path.join(root, "tmp")
    os.makedirs(tmp)

    print("safe_to_read")
    check("4 regular file", safe(ws, "normal.py"))
    check("  regular file with a space", safe(ws, "with space.py"))
    check("1 symlink to a file outside the checkout", not safe(ws, "leak.py"))
    check("2 symlink to a file inside the checkout", not safe(ws, "inner.py"))
    check("3 symlink to a directory", not safe(ws, "linkdir"))
    check("9 regular file under a symlinked directory outside", not safe(ws, "dirlink/secret.txt"))
    check("10 path that only shares a prefix with the checkout", not safe(ws, "evil/f.txt"))
    check("  missing file", not safe(ws, "deleted.py"))
    check("  absolute path outside", not safe(ws, os.path.join(root, "outside", "secret.txt")))

    print("gemini: Build review payload")
    changed = ["normal.py", "with space.py", "leak.py", "inner.py", "linkdir", "dirlink/secret.txt",
               "evil/f.txt", "deleted.py"]
    open(os.path.join(tmp, "changed-files.txt"), "w").write("\n".join(changed) + "\n")
    open(os.path.join(tmp, "pr-diff.txt"), "w").write("diff --git a/normal.py b/normal.py\n")
    env = {"MAX_TOTAL_LINES": "5000", "MAX_PER_FILE": "1000", "MAX_FILES": "20", "STACK": "python",
           "EXTRA_PROMPT": "", "EXTRA_PROMPT_PATH": "extras.md"}
    result = run_step(ws, tmp, step_script("gemini-review.yml", "Build review payload"), env)
    check("  step exits 0", result.returncode == 0, result.stderr[-500:])
    code = open(os.path.join(tmp, "review-code.txt")).read() if os.path.exists(os.path.join(tmp, "review-code.txt")) else ""
    prompt = open(os.path.join(tmp, "review-prompt.txt")).read() if os.path.exists(os.path.join(tmp, "review-prompt.txt")) else ""
    check("4 regular files are sent", "normal-file-body" in code and "spaced-file-body" in code, code[:300])
    check("1,9,10 nothing from outside the checkout is sent", SECRET not in code, code[:300])
    check("2 the in-repo symlink is not sent under its own name", "--- inner.py ---" not in code, code[:300])
    check("  each refused path gets a notice", all(f"'{p}'" in result.stdout for p in
          ["leak.py", "inner.py", "linkdir", "dirlink/secret.txt", "evil/f.txt"]), result.stdout[-800:])
    check("  a deleted file gets no notice", "'deleted.py'" not in result.stdout, result.stdout[-800:])
    check("8 extra_prompt_path symlinked outside is not read", SECRET not in prompt, prompt[:300])

    env["EXTRA_PROMPT_PATH"] = "notes.md"
    result = run_step(ws, tmp, step_script("gemini-review.yml", "Build review payload"), env)
    prompt = open(os.path.join(tmp, "review-prompt.txt")).read()
    check("  a regular extra_prompt_path is still read", "in-repo-extras" in prompt, prompt[:300])

    print("claude: Compose prompt")
    out = os.path.join(tmp, "github_output")
    env = {"STACK": "python", "EXTRA_PROMPT": "", "EXTRA_PROMPT_PATH": "extras.md", "REPO_FULL_NAME": "o/r",
           "PR_NUMBER": "1", "GITHUB_OUTPUT": out}
    result = run_step(ws, tmp, step_script("claude-review.yml", "Compose prompt"), env)
    check("  step exits 0", result.returncode == 0, result.stderr[-500:])
    composed = open(os.path.join(tmp, "prompt.md")).read() if os.path.exists(os.path.join(tmp, "prompt.md")) else ""
    check("8 extra_prompt_path symlinked outside is not read", SECRET not in composed, composed[:300])
    env["EXTRA_PROMPT_PATH"] = "notes.md"
    run_step(ws, tmp, step_script("claude-review.yml", "Compose prompt"), env)
    composed = open(os.path.join(tmp, "prompt.md")).read()
    check("  a regular extra_prompt_path is still read", "in-repo-extras" in composed, composed[:300])
finally:
    shutil.rmtree(root)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
