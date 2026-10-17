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
CHUNKS = pathlib.Path("/tmp/review-chunks")


def read_chunks(name):
    """One file from every call's directory, in call order."""
    dirs = sorted(CHUNKS.iterdir(), key=lambda d: int(d.name)) if CHUNKS.exists() else []
    return [(d / name).read_text(errors="surrogateescape") for d in dirs]


DEFAULTS = {"MAX_TOTAL_LINES": "20000", "MAX_PER_FILE": "3000", "MAX_FILES": "50",
            "MAX_DIFF_LINES": "5000", "MAX_PAYLOAD_BYTES": "2000000", "MAX_CHUNKS": "1", "STACK": "python",
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
    shutil.rmtree(CHUNKS, ignore_errors=True)
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
        if isinstance(content, Link):
            target.symlink_to(content.to)
            continue
        target.write_bytes(content.encode() if isinstance(content, str) else content)
    pathlib.Path("/tmp/pr-diff.txt").write_text(diff)
    out = work / "_out"
    base_env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out),
                    GH_TOKEN="x", PR_NUMBER="1", REPO="o/r", RUNNER_TEMP=str(work), **DEFAULTS)
    base_env.update(env or {})
    collect = subprocess.run(["bash", "-e", "-c", COLLECT], cwd=work, env=base_env, capture_output=True, text=True)
    build = None
    coverage = code = None
    if collect.returncode == 0 and "count=0" not in out.read_text():
        build = subprocess.run(["bash", "-e", "-c", BUILD], cwd=work, env=base_env, capture_output=True, text=True)
        cov_path = pathlib.Path("/tmp/review-coverage.json")
        coverage = json.loads(cov_path.read_text()) if cov_path.exists() else None
        code = "".join(read_chunks("code.txt"))
    shutil.rmtree(work)
    return collect, build, coverage, code


class Link:
    """A fixture file that is a symlink to `to`."""
    def __init__(self, to):
        self.to = to


def lines(n, tag="x"):
    return "".join(f"{tag}{i}\n" for i in range(n))


def reasons(coverage):
    return {o["file"]: o["reason"] for o in coverage["omitted"]}


def sent(code, name):
    return f"--- {name} ---" in code


print("caps")
c, b, cov, code = run_steps({"a.py": lines(10)}, ["a.py"], "diff --git a/a.py b/a.py\n+x\n")
check("1 under every cap: sent, nothing left out",
      b.returncode == 0 and sent(code, "a.py") and cov == {"omitted": [], "filtered": 0, "diff_sent": 2, "diff_total": 2, "diff_cut": False},
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
      reasons(cov) == {"gone2.py": "not in the checkout, and past the diff cut"}, cov)

c, b, cov, code = run_steps({"min.py": "x" * 3_000_000, "a.py": lines(5)}, ["min.py", "a.py"], "")
check("19 a 3 MB single-line file is left out by the byte cap; the next file is sent",
      not sent(code, "min.py") and sent(code, "a.py") and reasons(cov) == {"min.py": "over the 2000000-byte total"}, cov)

c, b, cov, code = run_steps({"t\tab.py": lines(1), "q\"uote.py": lines(4000)}, ["t\tab.py", "q\"uote.py"], "")
check("20 names with a tab or quote stay one record each",
      sent(code, "t\tab.py") and reasons(cov) == {"q\"uote.py": "over 3000 lines"}, cov)

c, b, cov, code = run_steps({"a.py": lines(1), "big.py": lines(10), "c.py": lines(1), "d.py": lines(4), "e.py": lines(1)},
                            ["a.py", "big.py", "c.py", "d.py", "e.py"], "",
                            env={"MAX_PER_FILE": "5", "MAX_FILES": "2", "MAX_TOTAL_LINES": "100"})
check("10 caller overrides of max_per_file and max_files are used",
      reasons(cov) == {"big.py": "over 5 lines", "d.py": "over the 2-file limit", "e.py": "over the 2-file limit"}, cov)
c, b, cov, code = run_steps({"a.py": lines(3), "b.py": lines(3)}, ["a.py", "b.py"], "", env={"MAX_TOTAL_LINES": "5"})
check("10 a max_total_lines override is used", reasons(cov) == {"b.py": "over the 5-line total"}, cov)
c, b, cov, code = run_steps({"a.py": "a" * 50, "b.py": "b" * 60}, ["a.py", "b.py"], "", env={"MAX_PAYLOAD_BYTES": "100"})
check("10 a max_payload_bytes override is used", reasons(cov) == {"b.py": "over the 100-byte total"}, cov)

print("hostile file names and links")
c, b, cov, code = run_steps({"-": "dash\n", "after.py": lines(1)}, ["-", "after.py"], "")
check("22 a file named - is read as a file, and the files after it are still sent",
      b.returncode == 0 and "--- - ---\ndash" in code and sent(code, "after.py"), (b.stderr[-300:], code[:300]))
c, b, cov, code = run_steps({"-n.py": "x\n", "after.py": lines(1)}, ["-n.py", "after.py"], "")
check("22 a file named -n.py does not kill the step", b.returncode == 0 and sent(code, "-n.py"), b.stderr[-300:])
c, b, cov, code = run_steps({".git/config": "extraheader = AUTHORIZATION: basic SECRETTOKEN\n",
                             "leak.py": Link(".git/config"), "out.py": Link("/etc/hosts"), "a.py": lines(1)},
                            ["leak.py", "out.py", "a.py"], "")
check("23 symlinks are not followed: no link target reaches the payload",
      "SECRETTOKEN" not in code and not sent(code, "leak.py") and not sent(code, "out.py") and sent(code, "a.py")
      and reasons(cov) == {"leak.py": "a symlink, not read", "out.py": "a symlink, not read"}, (cov, code[:300]))

print("diff byte cap")
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "diff --git a/a.py b/a.py\n+" + "y" * 200, env={"MAX_PAYLOAD_BYTES": "100"})
check("24 a diff cut inside its last line is recorded as cut", cov.get("diff_cut") is True, cov)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "+" + "é" * 100, env={"MAX_PAYLOAD_BYTES": "50"})
sent_diff = b"".join((d / "diff.txt").read_bytes() for d in sorted(CHUNKS.iterdir())) if CHUNKS.exists() else b""
try:
    sent_diff.decode("utf-8")
    valid = True
except UnicodeDecodeError:
    valid = False
check("25 a byte cut through a two-byte character leaves valid UTF-8", valid and sent_diff, sent_diff[-8:])
check("25 and stays within the byte cap", 0 < len(sent_diff) <= 50, len(sent_diff))
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "+short\n")
check("24 an uncut diff is not recorded as cut", cov.get("diff_cut") is False, cov)

print("filtering")
c, b, cov, code = run_steps({}, ["pnpm-lock.yaml", "logo.png"], "")
check("16 a PR of only filtered files: count=0, no payload, step exits 0",
      c.returncode == 0 and b is None, (c.returncode, c.stderr))
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py", "yarn.lock", "dist/x.js"], "")
check("17 filtered files beside a reviewed one are counted", cov["filtered"] == 2 and cov["omitted"] == [], cov)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "", gh_fails=True)
check("  a failing files API call fails the step instead of looking empty", c.returncode != 0, c.returncode)

print("chunking (#30)")


def chunk_files():
    return [json.loads(t) for t in read_chunks("files.json")]


def hunk(name, n=1):
    return f"diff --git a/{name} b/{name}\n" + "".join(f"+{name} {i}\n" for i in range(n))


names = [f"f{i:03}.py" for i in range(120)]
c, b, cov, code = run_steps({n: lines(1) for n in names}, names, "", env={"MAX_CHUNKS": "3"})
check("30.3 120 files, 50 per call, 3 calls: 50, 50, 20 and nothing listed",
      [len(f) for f in chunk_files()] == [50, 50, 20] and cov["omitted"] == [], ([len(f) for f in chunk_files()], cov))
names = [f"f{i:03}.py" for i in range(200)]
c, b, cov, code = run_steps({n: lines(1) for n in names}, names, "", env={"MAX_CHUNKS": "3"})
check("30.4 200 files, 3 calls: 150 sent, 50 listed as over the 3-call limit",
      sum(len(f) for f in chunk_files()) == 150 and len(cov["omitted"]) == 50
      and {o["reason"] for o in cov["omitted"]} == {"over the 3-call limit"}, cov["omitted"][:2])
c, b, cov, code = run_steps({"a/1.py": lines(1), "b/1.py": lines(1), "a/2.py": lines(1)}, ["a/1.py", "b/1.py", "a/2.py"], "",
                            env={"MAX_CHUNKS": "2", "MAX_FILES": "2"})
check("30.5 files sorted by path: both a/ files share a call", chunk_files() == [["a/1.py", "a/2.py"], ["b/1.py"]], chunk_files())
names = ["a.py", "b.py", "c.py"]
diff = "".join(hunk(n, 3) for n in names)
c, b, cov, code = run_steps({n: lines(1) for n in names}, names, diff, env={"MAX_CHUNKS": "3", "MAX_FILES": "1"})
diffs = read_chunks("diff.txt")
check("30.6 each call's diff holds only its own files' hunks",
      [chunk_files(), diffs] == [[["a.py"], ["b.py"], ["c.py"]], [hunk("a.py", 3), hunk("b.py", 3), hunk("c.py", 3)]], diffs)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py", "gone.py"], hunk("a.py") + hunk("gone.py", 2), env={"MAX_CHUNKS": "2"})
check("30.7 a deleted file's hunk is sent with the call that owns it",
      "gone.py" in sum(chunk_files(), []) and hunk("gone.py", 2) in "".join(read_chunks("diff.txt")) and cov["omitted"] == [], cov)
c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py", "gone.py"], hunk("a.py") + hunk("gone.py", 9),
                            env={"MAX_CHUNKS": "1", "MAX_DIFF_LINES": "2"})
check("30.7 a deleted file whose hunk fits nowhere is listed",
      reasons(cov) == {"gone.py": "not in the checkout, and past the diff cut"}, cov)
c, b, cov, code = run_steps({"big.py": lines(15000), "small.py": lines(100), "z.py": lines(6000)},
                            ["big.py", "small.py", "z.py"], "", env={"MAX_PER_FILE": "20000", "MAX_CHUNKS": "1"})
check("30.18 one call: a file over the remaining room is listed and a later small file still sent",
      reasons(cov) == {"z.py": "over the 20000-line total"} and sent(code, "small.py"), cov)
c, b, cov, code = run_steps({"a.py": lines(15000), "b.py": lines(6000), "c.py": lines(100)},
                            ["a.py", "b.py", "c.py"], "", env={"MAX_PER_FILE": "20000", "MAX_CHUNKS": "3"})
check("30.18 three calls: the big file opens call 2 and the small one backfills call 1",
      chunk_files() == [["a.py", "c.py"], ["b.py"]] and cov["omitted"] == [], chunk_files())
c, b, cov, code = run_steps({"a.py": lines(1), "b.py": lines(1)}, ["a.py", "b.py"], hunk("a.py", 50) + hunk("b.py", 3),
                            env={"MAX_CHUNKS": "2", "MAX_DIFF_LINES": "10"})
diffs = read_chunks("diff.txt")
check("30.19 a hunk past the diff cap is cut and flagged; the next file's hunk is sent whole",
      cov["diff_cut"] is True and hunk("b.py", 3) in "".join(diffs) and len(diffs[0].splitlines()) == 10, (chunk_files(), cov))
odd = ["dir with space/x.py", "q\"uote.py", "ünï.py", "new.py"]
diff = "".join(hunk(n) for n in odd[:3]) + "diff --git a/old.py b/new.py\nsimilarity index 90%\n"
c, b, cov, code = run_steps({n: lines(1) for n in odd}, odd, diff, env={"MAX_CHUNKS": "4", "MAX_FILES": "1"})
pairs = {f[0]: d for f, d in zip(chunk_files(), read_chunks("diff.txt"))}
check("30.20 rename, space, quote and Unicode hunks go with their own files",
      all(f"b/{n}" in pairs.get(n, "") for n in odd) and all(len(f) == 1 for f in chunk_files()), pairs)
for bad in ["0", "-1", "1.5", "6", "x"]:
    c, b, cov, code = run_steps({"a.py": lines(1)}, ["a.py"], "", env={"MAX_CHUNKS": bad})
    check(f"30.24 max_chunks {bad!r} fails the step before any call",
          b.returncode != 0 and "max_chunks must be a whole number" in b.stdout + b.stderr and not CHUNKS.exists(),
          (b.returncode, b.stderr[-200:]))

print("round 1 review fixes (#30)")
c, b, cov, code = run_steps({"big.py": lines(50), "a.py": lines(1)}, ["big.py", "a.py"], hunk("big.py", 2) + hunk("a.py"),
                            env={"MAX_PER_FILE": "3"})
check("30.27 a file over max_per_file still has its diff sent, and says so",
      hunk("big.py", 2) in "".join(read_chunks("diff.txt"))
      and reasons(cov) == {"big.py": "over 3 lines, only its diff was sent"}, (cov, read_chunks("diff.txt")))
c, b, cov, code = run_steps({".git/config": "SECRETTOKEN\n", "leak.py": Link(".git/config"), "a.py": lines(1)},
                            ["leak.py", "a.py"], hunk("leak.py") + hunk("a.py"))
check("30.27 a symlink's diff is sent but its target is not read",
      hunk("leak.py") in "".join(read_chunks("diff.txt")) and "SECRETTOKEN" not in code
      and reasons(cov) == {"leak.py": "a symlink, not read, only its diff was sent"}, cov)
c, b, cov, code = run_steps({"big.py": lines(50)}, ["big.py"], "", env={"MAX_PER_FILE": "3"})
check("30.28 every file skipped and no diff: zero calls, step exits 0, coverage written",
      b.returncode == 0 and read_chunks("files.json") == [] and reasons(cov) == {"big.py": "over 3 lines"}, (b.returncode, b.stderr[-300:], cov))


def real_git_diff(files_before, files_after):
    """A diff written by git itself, so quoting is git's, not ours."""
    repo = pathlib.Path(tempfile.mkdtemp())
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git[:3] + ["init", "-q"], check=True)
    for name, content in files_before.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    for name in files_before:
        (repo / name).unlink()
    for name, content in files_after.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
    subprocess.run(git + ["add", "-A"], check=True)
    out = subprocess.run(git + ["diff", "--cached", "-M"], check=True, capture_output=True).stdout
    shutil.rmtree(repo)
    return out.decode("utf-8")


odd = {"q\"uote.py": "a\n", "ünï.py": "b\n", "a b/c.py": "c\n", "gone \"x\".py": "d\n", "old name.py": "same\n" * 20}
after = {"q\"uote.py": "a2\n", "ünï.py": "b2\n", "a b/c.py": "c2\n", "new name.py": "same\n" * 20 + "more\n"}
diff = real_git_diff(odd, after)
present = {n: v for n, v in after.items()}
changed = ["q\"uote.py", "ünï.py", "a b/c.py", "gone \"x\".py", "new name.py"]
c, b, cov, code = run_steps(present, changed, diff, env={"MAX_CHUNKS": "5", "MAX_FILES": "1"})
pairs = {}
for f, d in zip(read_chunks("files.json"), read_chunks("diff.txt")):
    for name in json.loads(f):
        pairs[name] = d
check("30.29 git-quoted, spaced, Unicode, deleted and renamed paths each get their own real hunk",
      set(pairs) == set(changed)
      and all(mark in pairs[n] for n, mark in [("q\"uote.py", "+a2"), ("ünï.py", "+b2"), ("a b/c.py", "+c2"),
                                                ("gone \"x\".py", "-d"), ("new name.py", "+more")])
      and cov["omitted"] == [] and cov["diff_sent"] == cov["diff_total"] > 0, (sorted(pairs), cov, diff[:300]))

for name in TMP_FILES:
    pathlib.Path("/tmp", name).unlink(missing_ok=True)
shutil.rmtree(CHUNKS, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
