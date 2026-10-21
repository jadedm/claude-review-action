#!/usr/bin/env python3
"""Cases for the Gemini workflows' two jobs (#63) and per-run folder (#61).

Run: python3 scripts/test_gemini_jobs.py

The gemini job calls the model and cannot write to the pull request; the post
job writes and holds no model credential. Reads both workflow files and runs
the real "Make the run folder", payload and "Post review comment" steps, cut
out of gemini-review.yml, in bash with stub `gh` commands. Case numbers match
the plans on #63 and #61. Standard library only.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
WF = REPO / ".github/workflows"
FILES = {name: (WF / name).read_text() for name in ("gemini-review.yml", "gemini-review-vertex.yml")}
UPLOAD = "        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1"
DOWNLOAD = "        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def jobs(text):
    """{job id: its lines}."""
    lines = text.split("\njobs:\n", 1)[1].splitlines()
    at = [i for i, l in enumerate(lines) if re.match(r"^  [a-z][a-z0-9_-]*:$", l)]
    return {lines[s].strip(" :"): lines[s:e] for s, e in zip(at, at[1:] + [len(lines)])}


def steps(job_lines):
    """{step name: its lines}, in order."""
    at = [i for i, l in enumerate(job_lines) if l.startswith("      - name: ")]
    return {job_lines[s][14:]: job_lines[s:e] for s, e in zip(at, at[1:] + [len(job_lines)])}


def mapping(job_lines, key):
    """The key: value lines under a job-level key such as permissions or outputs."""
    if f"    {key}:" not in job_lines:
        return {}
    out = {}
    for l in job_lines[job_lines.index(f"    {key}:") + 1:]:
        if not l.startswith("      ") or l.startswith("      - "):
            break
        if l.strip().startswith("#"):
            continue
        k, _, v = l.strip().partition(": ")
        out[k] = v
    return out


def script(step_lines):
    """The run: body of a step, dedented, as Actions runs it."""
    inline = [l.strip()[5:] for l in step_lines if l.strip().startswith("run: ") and l.strip() != "run: |"]
    if inline:
        return inline[0] + "\n"
    at = step_lines.index("        run: |")
    return "\n".join(l[10:] for l in step_lines[at + 1:]) + "\n"


def code(lines):
    return [l for l in lines if not l.strip().startswith("#")]


print("two jobs (#63)")
for name, text in FILES.items():
    j = jobs(text)
    check(f"63 {name}: a gemini job and a post job", list(j) == ["gemini", "post"], list(j))
    gem, post = j.get("gemini", []), j.get("post", [])
    want = {"contents": "read", "pull-requests": "read"}
    if "vertex" in name:
        want["id-token"] = "write"
    check(f"63.1 {name}: the gemini job's permissions are exactly {sorted(want.items())}", mapping(gem, "permissions") == want,
          mapping(gem, "permissions"))
    check(f"63.2 {name}: the post job's permissions are exactly contents read and pull-requests write",
          mapping(post, "permissions") == {"contents": "read", "pull-requests": "write"}, mapping(post, "permissions"))
    check(f"63.2 {name}: no id-token anywhere in the post job", not [l for l in code(post) if "id-token" in l])
    check(f"63.4 {name}: nothing in the gemini job posts a comment or asks for a write",
          not [l for l in code(gem) if "gh pr comment" in l or ": write" in l and "id-token" not in l],
          [l for l in code(gem) if "gh pr comment" in l or ": write" in l])
    check(f"63.5 {name}: the post job reads no model credential",
          not [l for l in code(post) if "steps.auth" in l or "gemini_api_key" in l or "GEMINI_API_KEY" in l or "access_token" in l])
    secrets = sorted(set(re.findall(r"secrets\.([a-z_]+)", "\n".join(code(post)))))
    check(f"63.3 {name}: the post job's only secret is the Slack webhook", secrets == ["slack_webhook_url"], secrets)
    post_if = next((l.strip() for l in post if l.startswith("    if:")), "")
    check(f"63.11/12 {name}: the post job needs gemini and runs unless it was skipped or cancelled",
          "    needs: gemini" in post and post_if == "if: always() && needs.gemini.result != 'skipped' && needs.gemini.result != 'cancelled'",
          post_if)
    groups = [[l.strip() for l in job if l.startswith("      group: ")] for job in (gem, post)]
    check(f"63.16 {name}: the post job is in the gemini job's concurrency group, cancelled by a newer run",
          groups[0] and groups[0] == groups[1] and "      cancel-in-progress: true" in post, groups)
    out = mapping(gem, "outputs")
    check(f"63.6 {name}: the gemini job outputs the uploaded artifact's id",
          out.get("artifact_id") == "${{ steps.upload.outputs.artifact-id }}", out)

    gs, ps = steps(gem), steps(post)
    up = gs.get("Hand the review to the post job", [])
    check(f"63.13 {name}: upload-artifact pinned to its v7.0.1 commit", UPLOAD in up, up[:8])
    check(f"63.6 {name}: the upload holds only the review and coverage files, fails if missing, kept 1 day",
          "            ${{ steps.work.outputs.dir }}/gemini-review.json" in up
          and "            ${{ steps.work.outputs.dir }}/review-coverage.json" in up
          and len([l for l in up if l.startswith("            ")]) == 2
          and "          if-no-files-found: error" in up and "          retention-days: 1" in up
          and "        if: steps.files.outputs.count > 0" in up and "        id: upload" in up, up)
    check(f"63.6 {name}: the artifact name is the run folder's own random name", "          name: ${{ steps.work.outputs.name }}-${{ github.run_attempt }}" in up, up)
    order = list(gs)
    check(f"63.6 {name}: the upload follows the call and precedes the folder's removal",
          "Call Gemini API" in order and order.index("Call Gemini API") < order.index("Hand the review to the post job") == len(order) - 2
          and order[-1] == "Remove the run folder", order)
    dl = ps.get("Download the review", [])
    check(f"63.13 {name}: download-artifact pinned to its v8.0.1 commit", DOWNLOAD in dl, dl[:8])
    check(f"63.7 {name}: the download is by artifact id into the post job's own run folder, not the workspace",
          "          artifact-ids: ${{ needs.gemini.outputs.artifact_id }}" in dl and "          path: ${{ steps.work.outputs.dir }}" in dl
          and "          merge-multiple: true" in dl and "        if: needs.gemini.outputs.artifact_id != ''" in dl, dl)
    po = ps.get("Post review comment", [])
    check(f"63.8 {name}: the post step runs only when there is a review and reads it from its run folder",
          "        if: needs.gemini.outputs.artifact_id != ''" in po and "          WORK: ${{ steps.work.outputs.dir }}" in po
          and "          GH_REPO: ${{ github.repository }}" in po, po[:10])
    check(f"63.14 {name}: Slack is in the post job only, after its clear and action checkout",
          "Notify Slack" in ps and "Notify Slack" not in gs
          and list(ps).index("Clear the action folder") < list(ps).index("Checkout review action repo (prompts)") < list(ps).index("Notify Slack"),
          list(ps))
    check(f"63 {name}: the post job installs gh with the gemini job's own step",
          ps.get("Install GitHub CLI", [None])[1:] == gs.get("Install GitHub CLI", [])[1:] and gs.get("Install GitHub CLI"))
    check(f"63 {name}: the post job checks out the action repo as the gemini job does, with no consumer checkout",
          ps.get("Checkout review action repo (prompts)") == gs.get("Checkout review action repo (prompts)")
          and "Checkout consumer repo" not in ps)

top = FILES["gemini-review-vertex.yml"].split("\npermissions:\n", 1)[1].split("\njobs:", 1)[0]
check("63.15 vertex: id-token is only at the top level and in the gemini job",
      [l.strip() for l in code(FILES["gemini-review-vertex.yml"].splitlines()) if "id-token" in l] == ["id-token: write", "id-token: write"]
      and "id-token: write" in top, top)

print("run folder (#61)")
for name, text in FILES.items():
    j = jobs(text)
    tmp = [l.strip() for job in j.values() for l in code(job) if "/tmp" in l]
    check(f"61.2 {name}: no step names a fixed /tmp path", tmp == [], tmp)
    for job in ("gemini", "post"):
        s = steps(j[job])
        work = s.get("Make the run folder", [])
        check(f"61.1 {name} {job}: a Make the run folder step with id work", "        id: work" in work, work[:4])
        rm = s.get("Remove the run folder", [])
        check(f"61.7 {name} {job}: the last step removes the folder, always",
              list(s)[-1] == "Remove the run folder" and "        if: always() && steps.work.outputs.dir != ''" in rm, rm)
        users = [n for n, b in s.items() if "$WORK" in "\n".join(b) and n != "Remove the run folder"]
        unguarded = [n for n in users if ': "${WORK:?no run folder}"' not in "\n".join(s[n])]
        check(f"61.6 {name} {job}: every step that uses WORK fails when it is empty", users and not unguarded, (users, unguarded))
gem_steps = list(steps(jobs(FILES["gemini-review.yml"])["gemini"]))
check("61.1 the gemini job makes the folder right after the provider check", gem_steps[:2] == ["Check the provider", "Make the run folder"], gem_steps[:3])

MAIN = jobs(FILES["gemini-review.yml"])
G, P = steps(MAIN["gemini"]), steps(MAIN["post"])


def run_step(step_lines, env, cwd):
    out = pathlib.Path(cwd) / "_github_output"
    out.write_text("")
    e = dict(os.environ, GITHUB_OUTPUT=str(out), **env)
    p = subprocess.run(["bash", "-e", "-c", script(step_lines)], cwd=cwd, env=e, capture_output=True, text=True)
    outputs = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l)
    return p, outputs


base = pathlib.Path(tempfile.mkdtemp())
rt = base / "rt"
rt.mkdir()
p, o = run_step(G["Make the run folder"], {"RUNNER_TEMP": str(rt)}, base)
made = pathlib.Path(o.get("dir", "/nonexistent"))
check("61.1 the folder is new, under RUNNER_TEMP, mode 700, and its name is output",
      p.returncode == 0 and made.is_dir() and made.parent == rt and (made.stat().st_mode & 0o777) == 0o700
      and o.get("name") == made.name and made.name.startswith("gemini-review."), (p.stderr, o))
p2, o2 = run_step(G["Make the run folder"], {"RUNNER_TEMP": str(rt)}, base)
check("61.4 a second run gets a different folder", p2.returncode == 0 and o2.get("dir") not in (None, o.get("dir")), (o, o2))
env = dict(os.environ)
env.pop("RUNNER_TEMP", None)
p3 = subprocess.run(["bash", "-e", "-c", script(G["Make the run folder"])], cwd=base, env=dict(env, GITHUB_OUTPUT=str(base / "o3")),
                    capture_output=True, text=True)
check("61.6 no RUNNER_TEMP: the step fails rather than making a folder elsewhere", p3.returncode != 0, p3.returncode)
(made / "x").write_text("x")
p, _ = run_step(G["Remove the run folder"], {"WORK": str(made)}, base)
check("61.7 the remove step deletes the folder and what is in it", p.returncode == 0 and not made.exists(), p.stderr)

# Each step that uses WORK, run with it empty: fails, and writes nothing into the checkout or /tmp.
for name in ("Get PR diff", "Collect changed files", "Build review payload", "Call Gemini API"):
    cwd = base / ("empty-" + name.replace(" ", "-"))
    cwd.mkdir()
    (cwd / ".claude-review-action").symlink_to(REPO)
    p, _ = run_step(G[name], {"WORK": "", "STACK": "python", "API_URL": "http://127.0.0.1:9", "AUTH_SCHEME": "key"}, cwd)
    left = sorted(x.name for x in cwd.iterdir() if x.name not in (".claude-review-action", "_github_output"))
    check(f"61.6 {name} with WORK empty: fails before writing anything", p.returncode != 0 and "no run folder" in p.stderr and left == [],
          (p.returncode, p.stderr[-200:], left))

# Two runs side by side (61.4): the collect and payload steps, in parallel, each with its own folder.
GH_STUB = "#!/usr/bin/env bash\ncat \"$LISTING\"\n"


def payload_run(tag):
    work = base / f"side-{tag}"
    run_dir = work / "_run"
    (work / "_bin").mkdir(parents=True)
    run_dir.mkdir()
    (work / "_bin/gh").write_text(GH_STUB)
    (work / "_bin/gh").chmod(0o755)
    (work / f"{tag}.py").write_text(f"print('{tag}')\n")
    (work / "_listing").write_text(f"{tag}.py\n")
    (run_dir / "pr-diff.txt").write_text(f"diff --git a/{tag}.py b/{tag}.py\n+print('{tag}')\n")
    (work / ".claude-review-action").symlink_to(REPO)
    env = dict(os.environ, PATH=f"{work / '_bin'}:{os.environ['PATH']}", LISTING=str(work / "_listing"), WORK=str(run_dir),
               GITHUB_OUTPUT=str(work / "_out"), GH_TOKEN="x", PR_NUMBER="1", REPO="o/r", REPO_FULL_NAME="o/r", RUNNER_TEMP=str(work),
               MAX_TOTAL_LINES="20000", MAX_PER_FILE="3000", MAX_FILES="50", MAX_DIFF_LINES="5000",
               MAX_PAYLOAD_BYTES="2000000", MAX_CHUNKS="1", STACK="python", EXTRA_PROMPT="", EXTRA_PROMPT_PATH="")
    both = script(G["Collect changed files"]) + script(G["Build review payload"])
    return work, run_dir, subprocess.Popen(["bash", "-e", "-c", both], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


runs = [payload_run(tag) for tag in ("alpha", "beta")]
results = []
for work, run_dir, proc in runs:
    out, err = proc.communicate()
    code_txt = (run_dir / "review-chunks/1/code.txt").read_text() if (run_dir / "review-chunks/1/code.txt").exists() else ""
    results.append((proc.returncode, code_txt, (run_dir / "review-prompt.txt").exists(), err[-200:]))
check("61.4 two payload runs side by side: each payload holds only its own file, and each writes its own prompt",
      [r[0] for r in results] == [0, 0] and "--- alpha.py ---" in results[0][1] and "beta" not in results[0][1]
      and "--- beta.py ---" in results[1][1] and "alpha" not in results[1][1] and all(r[2] for r in results), results)
check("61.5 the payload step wrote coverage inside the run folder only",
      all((run_dir / "review-coverage.json").exists() for _, run_dir, _ in runs) and not any((w / "review-coverage.json").exists() for w, _, _ in runs))

print("the post step (#63)")
GH_RECORD = "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$GH_ARGS\"\nprintf '%s' \"$GH_REPO\" > \"$GH_ARGS.repo\"\n"


def post(review, coverage=None, link=False):
    work = pathlib.Path(tempfile.mkdtemp(dir=base))
    run_dir = work / "_run"
    run_dir.mkdir()
    (work / "_bin").mkdir()
    (work / "_bin/gh").write_text(GH_RECORD)
    (work / "_bin/gh").chmod(0o755)
    (work / ".claude-review-action").symlink_to(REPO)
    if review is not None:
        (run_dir / "gemini-review.json").write_text(json.dumps(review))
    cov = run_dir / "review-coverage.json"
    if link:
        (work / "elsewhere.json").write_text(json.dumps({"omitted": [], "filtered": 0, "diff_sent": 1, "diff_total": 1}))
        cov.symlink_to(work / "elsewhere.json")
    elif coverage is not False:
        cov.write_text(json.dumps(coverage or {"omitted": [], "filtered": 0, "diff_sent": 1, "diff_total": 1}))
    args = work / "_gh_args"
    p, outputs = run_step(P["Post review comment"], {"PATH": f"{work / '_bin'}:{os.environ['PATH']}", "GH_ARGS": str(args),
                                                     "GH_TOKEN": "x", "GH_REPO": "o/r", "PR_NUMBER": "7", "WORK": str(run_dir)}, work)
    posted = args.read_text().splitlines() if args.exists() else []
    repo = pathlib.Path(str(args) + ".repo").read_text() if pathlib.Path(str(args) + ".repo").exists() else None
    return p, outputs, posted, repo


REVIEW = {"summary": "Looks fine @someone", "issues": [{"file": "a.py", "line": 3, "description": "Off by one", "severity": "high",
                                                        "category": "bug"}], "positives": [], "overall_assessment": "request_changes"}
p, o, posted, repo = post(REVIEW)
body = "\n".join(posted[3:]) if posted[:3] == ["pr", "comment", "7"] else ""
check("63.8 a review: posts to the pull request with the built comment, repo from GH_REPO, verdict and count written",
      p.returncode == 0 and posted[:4] == ["pr", "comment", "7", "--body"] and "Off by one" in body and repo == "o/r"
      and o.get("assessment") == "request_changes" and o.get("issue_count") == "1", (p.stderr[-300:], posted[:5], o))
check("63.8 the comment is made safe in the post job: the mention is broken", "@someone" not in body and "someone" in body, body[:300])
p, o, posted, _ = post({"unparsed": True})
check("63.9 an unreadable reply: the notice is posted and the step fails", p.returncode != 0 and posted[:3] == ["pr", "comment", "7"]
      and o.get("assessment") == "unparsed", (p.returncode, o))
p, o, posted, _ = post({"nothing_sent": True})
check("63.10 nothing sent: posted, not_reviewed, exits 0", p.returncode == 0 and o.get("assessment") == "not_reviewed", (p.returncode, o))
p, o, posted, _ = post(None)
check("63.7 the review file is missing: fails and posts nothing", p.returncode != 0 and posted == [] and "no plain file gemini-review.json" in p.stdout,
      (p.returncode, p.stdout[-200:]))
p, o, posted, _ = post(REVIEW, link=True)
check("63.7 a link in place of the coverage file: refused, nothing posted", p.returncode != 0 and posted == [], (p.returncode, posted[:3]))
verdict = dict(REVIEW, overall_assessment="approve\nissue_count=999")
p, o, posted, _ = post(verdict)
check("63.8 a verdict carrying a newline cannot write an extra output", o.get("assessment") == "other" and o.get("issue_count") == "1", o)

shutil.rmtree(base)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
