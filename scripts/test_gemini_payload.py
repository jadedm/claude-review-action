#!/usr/bin/env python3
"""Cases for the Gemini payload caps (#29). Run: python3 scripts/test_gemini_payload.py

Runs the real "Collect changed files" and "Build review payload" steps, cut
out of gemini-review.yml, in bash against fixture files. `gh` is a stub that
prints a fixed file list. The steps write fixed /tmp paths, so this restores
nothing it did not create and must not run beside a real review.

Standard library only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
WORKFLOW = REPO / ".github/workflows/gemini-review.yml"
TMP_FILES = ["all-changed-files.txt", "changed-files.txt", "pr-diff.txt", "pr-diff-lines.txt", "pr-diff-sent.txt",
             "review-omitted.jsonl", "review-coverage.json", "review-prompt.txt", "review-code.txt",
             "review-diff.txt"]
DEFAULTS = {"MAX_TOTAL_LINES": "20000", "MAX_PER_FILE": "3000", "MAX_FILES": "50",
            "MAX_DIFF_LINES": "5000", "MAX_PAYLOAD_BYTES": "2000000", "STACK": "python",
            "EXTRA_PROMPT": "", "EXTRA_PROMPT_PATH": ""}

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
    """The `run: |` body of the named step, dedented, as Actions runs it."""
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    indent = None
    for line in lines[run + 1:]:
        if line.strip() and indent is None:
            indent = len(line) - len(line.lstrip())
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    assert body, f"step {name!r} has no script"
    return "\n".join(body) + "\n"


COLLECT = step_script("Collect changed files")
BUILD = step_script("Build review payload")


def run_steps(files, changed, diff, env=None, gh_fails=False):
    """Lay out a checkout, run both steps, return (collect, build, coverage, code)."""
    for name in TMP_FILES:
        pathlib.Path("/tmp", name).unlink(missing_ok=True)
    work = pathlib.Path(tempfile.mkdtemp())
    bin_dir = work / "_bin"
    bin_dir.mkdir()
    listing = work / "_listing.txt"
    listing.write_text("".join(f + "\n" for f in changed))
    (bin_dir / "gh").write_text("#!/usr/bin/env bash\n" + ("exit 1\n" if gh_fails else f"cat '{listing}'\n"))
    (bin_dir / "gh").chmod(0o755)
    (work / ".claude-review-action").symlink_to(REPO)
    for path, content in files.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)
    pathlib.Path("/tmp/pr-diff.txt").write_text(diff)
    out = work / "_out"
    base_env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out),
                    GH_TOKEN="x", PR_NUMBER="1", REPO="o/r", **DEFAULTS)
    base_env.update(env or {})
    collect = subprocess.run(["bash", "-e", "-c", COLLECT], cwd=work, env=base_env, capture_output=True, text=True)
    build = None
    coverage = code = None
    if collect.returncode == 0 and "count=0" not in out.read_text():
        build = subprocess.run(["bash", "-e", "-c", BUILD], cwd=work, env=base_env, capture_output=True, text=True)
        cov_path = pathlib.Path("/tmp/review-coverage.json")
        coverage = json.loads(cov_path.read_text()) if cov_path.exists() else None
        code_path = pathlib.Path("/tmp/review-code.txt")
        code = code_path.read_text() if code_path.exists() else ""
    shutil.rmtree(work)
    return collect, build, coverage, code


def lines(n, tag="x"):
    return "".join(f"{tag}{i}\n" for i in range(n))


def reasons(coverage):
    return {o["file"]: o["reason"] for o in coverage["omitted"]}


def sent(code, name):
    return f"--- {name} ---" in code


print("caps")
c, b, cov, code = run_steps({"a.py": lines(10)}, ["a.py"], "diff --git a/a.py b/a.py\n+x\n")
check("1 under every cap: sent, nothing left out",
      b.returncode == 0 and sent(code, "a.py") and cov == {"omitted": [], "filtered": 0, "diff_sent": 2, "diff_total": 2},
      (b.stderr, cov))

c, b, cov, code = run_steps({"big.py": lines(3001), "ok.py": lines(3000)}, ["big.py", "ok.py"], "")
check("2 a file over max_per_file is left out and named; one at exactly the cap is sent",
      not sent(code, "big.py") and sent(code, "ok.py") and reasons(cov) == {"big.py": "over 3000 lines"}, cov)

names = [f"f{i:02}.py" for i in range(55)]
c, b, cov, code = run_steps({n: lines(1) for n in names}, names, "")
check("3 55 files: first 50 sent, last 5 named as over the file limit",
      all(sent(code, n) for n in names[:50]) and not any(sent(code, n) for n in names[50:])
      and list(reasons(cov)) == names[50:] and set(reasons(cov).values()) == {"over the 50-file limit"}, cov)

names = [f"f{i:02}.py" for i in range(50)]
c, b, cov, code = run_steps({n: lines(1) for n in names}, names, "")
check("15 exactly 50 files: all sent, nothing named", cov["omitted"] == [], cov)

c, b, cov, code = run_steps({"a.py": lines(15000), "b.py": lines(6000), "c.py": lines(100)},
                            ["a.py", "b.py", "c.py"], "", env={"MAX_PER_FILE": "20000"})
check("4 15,000 then 6,000 then 100 lines: the middle file is skipped and the loop goes on",
      sent(code, "a.py") and not sent(code, "b.py") and sent(code, "c.py")
      and reasons(cov) == {"b.py": "over the 20000-line total"}, cov)

c, b, cov, code = run_steps({"a.py": lines(15000), "b.py": lines(5000)}, ["a.py", "b.py"], "",
                            env={"MAX_PER_FILE": "20000"})
check("15 exactly 20,000 lines in total: both sent", sent(code, "a.py") and sent(code, "b.py") and cov["omitted"] == [], cov)

diff = lines(5001, "+d")
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], diff)
check("5 a 5,001-line diff: the first 5,000 lines are sent and the cut is recorded",
      cov["diff_sent"] == 5000 and cov["diff_total"] == 5001, cov)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], lines(5000, "+d"))
check("15 a diff of exactly 5,000 lines is not cut", cov["diff_sent"] == cov["diff_total"] == 5000, cov)

c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py", "gone.py", "gone2.py"],
                            "diff --git a/gone.py b/gone.py\n-x\n" + lines(10, "+d") + "diff --git a/gone2.py b/gone2.py\n-y\n",
                            env={"MAX_DIFF_LINES": "5"})
check("6/18 a deleted file inside the sent diff is not named; one past the cut is",
      reasons(cov) == {"gone2.py": "deleted, and past the diff cut"}, cov)

c, b, cov, code = run_steps({"min.py": "x" * 3_000_000, "a.py": lines(5)}, ["min.py", "a.py"], "")
check("19 a 3 MB single-line file is left out by the byte cap; the next file is sent",
      not sent(code, "min.py") and sent(code, "a.py") and reasons(cov) == {"min.py": "over the 2000000-byte total"}, cov)

c, b, cov, code = run_steps({"t\tab.py": lines(1), "q\"uote.py": lines(4000)}, ["t\tab.py", "q\"uote.py"], "")
check("20 names with a tab or quote stay one record each",
      sent(code, "t\tab.py") and reasons(cov) == {"q\"uote.py": "over 3000 lines"}, cov)

c, b, cov, code = run_steps({"a.py": lines(1), "big.py": lines(10)}, ["a.py", "big.py"], "",
                            env={"MAX_PER_FILE": "5", "MAX_FILES": "7"})
check("10 caller overrides are used", reasons(cov) == {"big.py": "over 5 lines"}, cov)

print("filtering")
c, b, cov, code = run_steps({}, ["pnpm-lock.yaml", "logo.png"], "")
check("16 a PR of only filtered files: count=0, no payload, step exits 0",
      c.returncode == 0 and b is None, (c.returncode, c.stderr))
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py", "yarn.lock", "dist/x.js"], "")
check("17 filtered files beside a reviewed one are counted", cov["filtered"] == 2 and cov["omitted"] == [], cov)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "", gh_fails=True)
check("  a failing files API call fails the step instead of looking empty", c.returncode != 0, c.returncode)

for name in TMP_FILES:
    pathlib.Path("/tmp", name).unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
