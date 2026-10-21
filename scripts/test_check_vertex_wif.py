#!/usr/bin/env python3
"""Cases for check_vertex_wif.sh (#70). Run: python3 scripts/test_check_vertex_wif.py

Runs the script with stub gcloud and git first on PATH. gcloud prints the
condition in STUB_COND, or fails when STUB_GCLOUD_FAIL is set; git prints
STUB_TAGS, or fails when STUB_GIT_FAIL is set. Both log every call. Case
numbers match the test plan on #70. Standard library only.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parent / "check_vertex_wif.sh"
WORKFLOW = "jadedm/pr-review-workflows/.github/workflows/gemini-review-vertex.yml"
TAG = "e85bc76b53cc8b1e24ebe500341fc3ef6a0519e9"
COMMIT = "19710e1889c845281503765715ed64be47088c99"
NEW_TAG = "a" * 40
NEW_COMMIT = "b" * 40

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:600]}")


STUB_GCLOUD = """#!/usr/bin/env bash
echo "gcloud $*" >> "$STUB_LOG"
[ -z "${STUB_GCLOUD_FAIL:-}" ] || { echo "ERROR: (gcloud) $STUB_GCLOUD_FAIL" >&2; exit 1; }
printf '%s\\n' "$STUB_COND"
"""
STUB_GIT = """#!/usr/bin/env bash
echo "git $*" >> "$STUB_LOG"
[ -z "${STUB_GIT_FAIL:-}" ] || { echo "fatal: $STUB_GIT_FAIL" >&2; exit 128; }
[ -z "$STUB_TAGS" ] || printf '%s\\n' "$STUB_TAGS"
"""


def condition(shas, refs, ids=("111",), quote='"'):
    q = quote
    ref_cel = " || ".join(f"assertion.job_workflow_ref == {q}{WORKFLOW}@{r}{q}" for r in refs)
    sha_cel = ", ".join(f"{q}{s}{q}" for s in shas)
    id_cel = ", ".join(f"{q}{i}{q}" for i in ids)
    return f"({ref_cel}) && assertion.job_workflow_sha in [{sha_cel}] && assertion.repository_id in [{id_cel}]"


def tags(tag, commit):
    lines = [f"{tag}\trefs/tags/v1"]
    if commit:
        lines.append(f"{commit}\trefs/tags/v1^{{}}")
    return "\n".join(lines)


BOTH = condition([TAG, COMMIT], ["refs/tags/v1", COMMIT])
LIVE_TAGS = tags(TAG, COMMIT)

with tempfile.TemporaryDirectory() as tmp:
    bin_dir = pathlib.Path(tmp, "bin")
    bin_dir.mkdir()
    for name, body in [("gcloud", STUB_GCLOUD), ("git", STUB_GIT)]:
        f = bin_dir / name
        f.write_text(body)
        f.chmod(0o755)
    log = pathlib.Path(tmp, "calls.log")

    def run(cond=BOTH, tag_lines=LIVE_TAGS, args=("--project", "hyphn-studio-1013"), **env_extra):
        log.write_text("")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log),
               "STUB_COND": cond, "STUB_TAGS": tag_lines, **env_extra}
        r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, log.read_text()

    def missing(out):
        return [l.split("MISSING", 1)[1].strip() for l in out.splitlines() if l.startswith("  MISSING")]

    code, out, calls = run()
    check("1 both current SHAs and both refs listed: exit 0, nothing missing",
          code == 0 and missing(out) == [] and "admits the current v1" in out, (code, out))
    check("12 only read calls: one providers describe, one ls-remote",
          [l.split()[:4] for l in calls.splitlines()] == [["git", "ls-remote", "https://github.com/jadedm/pr-review-workflows",
                                                          "refs/tags/v1"],
                                                         ["gcloud", "iam", "workload-identity-pools", "providers"]]
          and " describe github " in calls, calls)

    code, out, calls = run(cond=condition([COMMIT], ["refs/tags/v1", COMMIT]))
    check("2 only the commit listed: exit 1, names the tag object",
          code == 1 and missing(out) == [f"tag object {TAG} in the SHA list (callers at @v1)"], (code, out))

    code, out, calls = run(cond=condition([TAG], ["refs/tags/v1"]))
    check("3 only the tag object listed: exit 1, names the commit SHA and the @<commit> ref",
          code == 1 and missing(out) == [f"commit {COMMIT} in the SHA list (callers pinned by commit)",
                                         f"ref @{COMMIT} (callers pinned by commit)"], (code, out))

    code, out, calls = run(tag_lines=tags(NEW_TAG, NEW_COMMIT))
    m = missing(out)
    check("4 v1 moved, condition holds the old pair: exit 1, names both new SHAs",
          code == 1 and len(m) == 3 and NEW_TAG in m[0] and NEW_COMMIT in m[1] and NEW_COMMIT in m[2]
          and f"--release-sha {NEW_TAG} --release-sha {NEW_COMMIT}" in out, (code, out))

    code, out, calls = run(STUB_GCLOUD_FAIL="You do not currently have an active account selected.")
    check("5 gcloud not signed in: exit 3 with gcloud's reason, no pass",
          code == 3 and "active account" in out and "admits" not in out and "listed" not in out, (code, out))

    code, out, calls = run(cond="")
    check("5b empty condition: exit 3", code == 3 and "has no attribute condition" in out, (code, out))

    code, out, calls = run(STUB_GIT_FAIL="unable to access")
    check("7 git ls-remote fails: exit 3 with the reason, no gcloud call",
          code == 3 and "unable to access" in out and "gcloud" not in calls, (code, out, calls))

    code, out, calls = run(tag_lines="")
    check("7 ls-remote prints nothing for refs/tags/v1: exit 3", code == 3 and "no refs/tags/v1" in out, (code, out))

    code, out, calls = run(cond=condition([COMMIT], ["refs/tags/v1", COMMIT]), tag_lines=tags(COMMIT, None))
    check("8 lightweight v1: one SHA, checked once in the list, exit 0 when listed",
          code == 0 and out.count("in the SHA list") == 1, (code, out))

    code, out, calls = run(cond=f'assertion.job_workflow_ref == "{WORKFLOW}@refs/tags/v1" || '
                                f'assertion.job_workflow_ref == "{WORKFLOW}@{COMMIT}"')
    m = missing(out)
    check("9 no job_workflow_sha list: exit 1, says so",
          code == 1 and m[0] == "a job_workflow_sha list in the condition" and len(m) == 3, (code, out))

    code, out, calls = run(cond=condition([TAG, COMMIT], [COMMIT]))
    check("10 @refs/tags/v1 not admitted: exit 1, names it",
          code == 1 and missing(out) == ["ref @refs/tags/v1 (callers at @v1)"], (code, out))

    code, out, calls = run(cond=condition([TAG + "0", "0" + COMMIT], ["refs/tags/v1", COMMIT]))
    check("11 SHAs present only inside longer strings: not counted, exit 1",
          code == 1 and len(missing(out)) == 2, (code, out))

    code, out, calls = run(cond=condition([TAG, COMMIT], ["refs/tags/v1", COMMIT], quote="'").replace(", ", ","))
    check("13 single quotes and no spaces (a hand-edited condition): exit 0", code == 0, (code, out))

    for label, args in [("no --project", ()), ("bad project id", ("--project", "Bad_Id")),
                        ("unknown flag", ("--project", "hyphn-studio-1013", "--apply"))]:
        code, out, calls = run(args=args)
        check(f"14 {label}: usage, exit 2, no call", code == 2 and "usage:" in out and calls == "", (code, out, calls))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
