#!/usr/bin/env python3
"""Cases for setup_vertex_wif.sh's arguments and condition (#66). Run: python3 scripts/test_setup_vertex_wif.py

Runs the script with stub gcloud and gh first on PATH. The stubs log every
call and answer as a project with nothing set up yet, so a dry run shows the
condition the script would set. Standard library only.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parent / "setup_vertex_wif.sh"
WORKFLOW = "jadedm/pr-review-workflows/.github/workflows/gemini-review-vertex.yml"
SHA_A = "19710e1" + "a" * 33
SHA_B = "b" * 40

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
case "$*" in
  "projects describe"*) echo 281419067788 ;;
  *" describe "*|*"get-iam-policy"*) exit 1 ;;
esac
exit 0
"""
STUB_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
case "$2" in
  repos/owner/one) echo 111 ;;
  repos/owner/two) echo 222 ;;
  *) exit 1 ;;
esac
"""

with tempfile.TemporaryDirectory() as tmp:
    bin_dir = pathlib.Path(tmp, "bin")
    bin_dir.mkdir()
    for name, body in [("gcloud", STUB_GCLOUD), ("gh", STUB_GH)]:
        f = bin_dir / name
        f.write_text(body)
        f.chmod(0o755)
    log = pathlib.Path(tmp, "calls.log")

    def run(*args):
        log.write_text("")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log)}
        r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, log.read_text()

    def wanted(out):
        lines = out.splitlines()
        return lines[lines.index("Wanted condition:") + 1].strip() if "Wanted condition:" in lines else ""

    base = ["--project", "p1", "--repos", "owner/one"]

    code, out, calls = run(*base, "--release-sha", SHA_A)
    expect = (f'(assertion.job_workflow_ref == "{WORKFLOW}@refs/tags/v1"'
              f' || assertion.job_workflow_ref == "{WORKFLOW}@{SHA_A}")'
              f' && assertion.job_workflow_sha in ["{SHA_A}"]'
              f' && assertion.repository_id in ["111"]')
    check("V1 one SHA: the condition admits @v1 and @<sha>, requires that workflow SHA and the repository id",
          code == 0 and wanted(out) == expect, (code, wanted(out), out[-400:]))
    check("V6 a dry run calls no create, update, enable or binding command",
          code == 0 and not any(w in calls for w in ("create", "update", "enable", "add-iam-policy-binding")), calls)

    code, out, calls = run("--project", "p1", "--repos", "owner/one,owner/two", "--release-sha", SHA_A, "--release-sha", SHA_B)
    cond = wanted(out)
    check("V2 two SHAs: both refs admitted and both in the SHA list, both repositories listed",
          code == 0 and f'@{SHA_A}"' in cond and f'@{SHA_B}"' in cond
          and f'assertion.job_workflow_sha in ["{SHA_A}", "{SHA_B}"]' in cond
          and 'assertion.repository_id in ["111", "222"]' in cond, (code, cond))

    code, out, calls = run(*base)
    check("V3 no --release-sha: usage, exit 2, no gcloud or gh call", code == 2 and "usage:" in out and calls == "", (code, out, calls))

    for label, bad in [("short", SHA_A[:7]), ("upper case", SHA_A.upper()), ("not hex", "g" * 40),
                       ("41 characters", SHA_A + "a"), ("empty", "")]:
        code, out, calls = run(*base, "--release-sha", bad)
        check(f"V4 a {label} SHA: exit 2, no gcloud or gh call", code == 2 and calls == "", (code, out, calls))

    code, out, calls = run(*base, "--release-sha", SHA_A, "--allow-ref", "refs/heads/ci/66-test")
    cond = wanted(out)
    check("V5 --allow-ref is admitted as a ref and the SHA check still applies to it",
          code == 0 and f'assertion.job_workflow_ref == "{WORKFLOW}@refs/heads/ci/66-test"' in cond
          and cond.endswith(f'&& assertion.job_workflow_sha in ["{SHA_A}"] && assertion.repository_id in ["111"]')
          and cond.count(" && ") == 2 and cond.startswith("("), (code, cond))

    code, out, calls = run(*base, "--release-sha", SHA_A, "--apply")
    update = next((l for l in calls.splitlines() if "create-oidc" in l or "update-oidc" in l), "")
    check("V1 --apply sets the same condition and maps job_workflow_sha",
          code == 0 and f"--attribute-condition {expect}" in update
          and "attribute.job_workflow_sha=assertion.job_workflow_sha" in update, (code, update, out[-400:]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
