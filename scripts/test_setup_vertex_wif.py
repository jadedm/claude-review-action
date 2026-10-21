#!/usr/bin/env python3
"""Cases for setup_vertex_wif.sh's arguments and condition (#66) and its grant check (#65).
Run: python3 scripts/test_setup_vertex_wif.py

Runs the script with stub gcloud and gh first on PATH. The stubs log every
call and answer as a project with nothing set up yet, so a dry run shows the
condition the script would set. For the grant cases (G), gcloud answers the
role list, role permissions, service account list and both IAM policies from
files in STUB_FIX, fails a read when NAME.fail exists there, and applies
remove-iam-policy-binding and roles update to those files. Standard library only.
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


STUB_GCLOUD = r"""#!/usr/bin/env bash
echo "gcloud $*" >> "$STUB_LOG"
fix=${STUB_FIX:-/nonexistent}
f=""
case "$*" in
  "projects describe"*) echo 281419067788; exit 0 ;;
  "iam roles list"*) f=roles_list ;;
  "iam roles describe"*) f=role_perms ;;
  "iam service-accounts list"*) f=sa_list ;;
  "projects get-iam-policy"*) f=project_policy ;;
  "iam service-accounts get-iam-policy"*) f=sa_policy ;;
  "projects remove-iam-policy-binding"*) f=project_policy; op=remove ;;
  "iam service-accounts remove-iam-policy-binding"*) f=sa_policy; op=remove ;;
  "iam roles update"*) echo aiplatform.endpoints.predict > "$fix/role_perms"; exit 0 ;;
  *" describe "*) exit 1 ;;
esac
if [ "${op:-}" = remove ]; then
  while [ $# -gt 0 ]; do
    case "$1" in --member) m=$2 ;; --role) r=$2 ;; esac
    shift
  done
  grep -vxF "${r},${m}" "$fix/$f" > "$fix/$f.new" || true
  mv "$fix/$f.new" "$fix/$f"
  exit 0
fi
[ -n "$f" ] || exit 0
[ -e "$fix/$f.fail" ] && { echo "ERROR: (gcloud) stub refused $f" >&2; exit 1; }
[ -e "$fix/$f" ] && { cat "$fix/$f"; exit 0; }
case "$f" in *policy) exit 1 ;; esac
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

    def run(*args, fix=None):
        log.write_text("")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log)}
        if fix:
            env["STUB_FIX"] = str(fix)
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

    # Grant check (#65).
    SA = "pr-review-vertex@p1.iam.gserviceaccount.com"
    OUR_ROLE = "projects/p1/roles/prReviewVertexPredict"
    WIU = "roles/iam.workloadIdentityUser"
    POOL = "principalSet://iam.googleapis.com/projects/281419067788/locations/global/workloadIdentityPools/github-pr-review"
    M111, M222 = f"{POOL}/attribute.repository_id/111", f"{POOL}/attribute.repository_id/222"
    CLEAN = {
        "roles_list": OUR_ROLE,
        "role_perms": "aiplatform.endpoints.predict",
        "sa_list": SA,
        # Another account's grant of our role, and someone else's owner grant: not ours, not listed (G12).
        "project_policy": f"{OUR_ROLE},serviceAccount:{SA}\n{OUR_ROLE},serviceAccount:other@p1.iam.gserviceaccount.com\nroles/owner,user:admin@example.com",
        "sa_policy": f"{WIU},{M111}",
    }
    case_no = [0]

    def fixture(**over):
        case_no[0] += 1
        d = pathlib.Path(tmp, f"fix{case_no[0]}")
        d.mkdir()
        for name, body in {**CLEAN, **over}.items():
            if body is not None:
                (d / name).write_text(body + "\n")
        return d

    def beyond(out):
        lines = out.splitlines()
        if "Beyond the expected set:" not in lines:
            return None
        i = len(lines) - 1 - lines[::-1].index("Beyond the expected set:")
        rest = []
        for line in lines[i + 1:]:
            if not line.startswith("  "):
                break
            rest.append(line.strip())
        return rest

    removes = ("remove-iam-policy-binding", "roles update")
    sha = ["--release-sha", SHA_A]

    code, out, calls = run(*base, *sha)
    check("G1 fresh project: nothing beyond the expected set, exit 0", code == 0 and beyond(out) == ["none"], (code, out[-400:]))

    code, out, calls = run(*base, *sha, fix=fixture())
    check("G2/G12 only the expected grants (others' grants ignored): none, exit 0",
          code == 0 and beyond(out) == ["none"], (code, out[-600:]))

    EXTRA_PROJECT = f"{CLEAN['project_policy']}\nroles/editor,serviceAccount:{SA}"
    code, out, calls = run(*base, *sha, fix=fixture(project_policy=EXTRA_PROJECT))
    check("G3 the service account also holds roles/editor: exit 1, named, no remove call",
          code == 1 and beyond(out) == [f"{SA} holds roles/editor on project p1"]
          and not any(w in calls for w in removes), (code, beyond(out), calls))

    code, out, calls = run(*base, *sha, fix=fixture(sa_policy=f"{WIU},{M111}\n{WIU},{M222}"))
    check("G4 a binding for a repository not in --repos: exit 1, member named",
          code == 1 and beyond(out) == [f"{M222} has {WIU} on {SA}"], (code, beyond(out)))

    code, out, calls = run(*base, "--repos", "owner/one,owner/two", *sha, fix=fixture(sa_policy=f"{WIU},{M111}\n{WIU},{M222}"))
    check("G4 the same binding with that repository listed: none, exit 0", code == 0 and beyond(out) == ["none"], (code, beyond(out)))

    TOKEN = "roles/iam.serviceAccountTokenCreator,user:someone@example.com"
    code, out, calls = run(*base, *sha, fix=fixture(sa_policy=f"{WIU},{M111}\n{TOKEN}"))
    check("G5 another role on the service account's policy: exit 1, role and member named",
          code == 1 and beyond(out) == [f"user:someone@example.com has roles/iam.serviceAccountTokenCreator on {SA}"], (code, beyond(out)))

    code, out, calls = run(*base, *sha, fix=fixture(role_perms="aiplatform.endpoints.predict;storage.objects.get"))
    check("G6 the custom role has an extra permission: exit 1, permission named",
          code == 1 and beyond(out) == ["role prReviewVertexPredict has permission storage.objects.get"], (code, beyond(out)))

    DELETED = f"{WIU},deleted:principalSet://x?uid=1"
    code, out, calls = run(*base, *sha, fix=fixture(sa_policy=f"{WIU},{M111}\n{DELETED}"))
    check("G13 a deleted: member is listed", code == 1 and beyond(out) == [f"deleted:principalSet://x?uid=1 has {WIU} on {SA}"], (code, beyond(out)))

    ALL = dict(project_policy=EXTRA_PROJECT, sa_policy=f"{WIU},{M111}\n{WIU},{M222}\n{TOKEN}",
               role_perms="aiplatform.endpoints.predict;storage.objects.get")
    code, out, calls = run(*base, *sha, "--prune", fix=fixture(**ALL))
    check("G7 --prune without --apply: says it would remove, exit 1, no remove or update call",
          code == 1 and "--prune with --apply would remove these." in out and not any(w in calls for w in removes)
          and len(beyond(out)) == 4, (code, out[-600:], calls))

    fx = fixture(**ALL)
    code, out, calls = run(*base, *sha, "--apply", "--prune", fix=fx)
    rm = [l for l in calls.splitlines() if any(w in l for w in removes)]
    want = [
        f"gcloud projects remove-iam-policy-binding p1 --member serviceAccount:{SA} --role roles/editor --all",
        f"gcloud iam service-accounts remove-iam-policy-binding {SA} --project p1 --member {M222} --role {WIU} --all",
        f"gcloud iam service-accounts remove-iam-policy-binding {SA} --project p1 --member user:someone@example.com --role roles/iam.serviceAccountTokenCreator --all",
        "gcloud iam roles update prReviewVertexPredict --project p1 --permissions aiplatform.endpoints.predict",
    ]
    check("G8 --prune --apply: one remove per extra, the role reset, listed repository kept, then none and exit 0",
          code == 0 and sorted(rm) == sorted(want) and beyond(out) == ["none"]
          and (fx / "sa_policy").read_text() == f"{WIU},{M111}\n", (code, rm, out[-600:]))

    code, out, calls = run(*base, *sha, "--apply", fix=fixture(**ALL))
    check("G9 --apply without --prune: bindings added, nothing removed, exit 1 naming the extras",
          code == 1 and "add-iam-policy-binding" in calls and not any(w in calls for w in removes)
          and len(beyond(out)) == 4 and "Rerun with --prune --apply" in out, (code, out[-600:]))

    for label, name in [("project policy", "project_policy"), ("service account policy", "sa_policy"),
                        ("role list", "roles_list"), ("service account list", "sa_list")]:
        fx = fixture()
        (fx / f"{name}.fail").write_text("")
        code, out, calls = run(*base, *sha, fix=fx)
        check(f"G10/G11 the {label} cannot be read: exit 3 with the reason, no clean report",
              code == 3 and "cannot check the service account's grants" in out and "stub refused" in out
              and beyond(out) is None, (code, out[-400:]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
