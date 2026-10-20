#!/usr/bin/env python3
"""Cases for the generated Vertex workflow, gemini-review-vertex.yml (#55).

Run: python3 scripts/test_gemini_vertex_workflow.py

Checks that the committed file is the render of gemini-review.yml, that the
render fails when an anchor is missing or doubled, that the sign-in step is
pinned, wired and placed as intended, and runs the generated file's own
"Check the provider" step. Standard library only.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render_vertex_workflow as rv  # noqa: E402

REPO = HERE.parent
MAIN = (REPO / ".github/workflows/gemini-review.yml").read_text()
VERTEX = (REPO / ".github/workflows/gemini-review-vertex.yml").read_text()
LINES = VERTEX.splitlines()

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def block(text, start, stop):
    return text.split(start, 1)[1].split(stop, 1)[0]


def step(lines, name):
    """(index, lines) of a step, or (-1, []) when there is none, so a missing step fails its cases instead of crashing."""
    if f"      - name: {name}" not in lines:
        return -1, []
    s = lines.index(f"      - name: {name}")
    e = next(i for i in range(s + 1, len(lines)) if lines[i].startswith("      - name:"))
    return s, lines[s:e]


print("render")
check("B1 the committed file is the render of gemini-review.yml", rv.render(MAIN) == VERTEX)
r = subprocess.run([sys.executable, str(HERE / "render_vertex_workflow.py"), "--check"], capture_output=True, text=True)
check("B1 --check exits 0 on the committed file", r.returncode == 0, r.stdout)
target = REPO / ".github/workflows/gemini-review-vertex.yml"
try:
    target.write_text(VERTEX.replace("token_format: access_token", "token_format: id_token"))
    r = subprocess.run([sys.executable, str(HERE / "render_vertex_workflow.py"), "--check"], capture_output=True, text=True)
    check("B1 --check exits 1 after a hand edit to the generated file", r.returncode == 1 and "differs" in r.stdout, r.stdout)
finally:
    target.write_text(VERTEX)
for old, new, times in rv.CHANGES:
    first = old.splitlines()[0][:50]
    for label, text in (("missing", MAIN.replace(old, "")), ("doubled", MAIN.replace(old, old + old))):
        try:
            rv.render(text)
            ok = False
        except SystemExit as e:
            ok = "expected" in str(e)
        check(f"B17 render fails when {first!r} is {label}", ok)

print("gemini-review.yml unchanged where callers see it")
check("B2 no id-token in gemini-review.yml", "id-token" not in MAIN)
check("B2 gemini_api_key is still a required secret", "      gemini_api_key:\n        required: true\n" in MAIN)
check("B3 its call step sends the key header", "          AUTH_SCHEME: key\n" in MAIN)

print("the Vertex workflow")
inputs = block(VERTEX, "    inputs:\n", "    secrets:\n")
secrets = block(VERTEX, "    secrets:\n", "\npermissions:")
def input_lines(name):
    """The lines under one input, up to the next input."""
    lines = inputs.splitlines()
    if f"      {name}:" not in lines:
        return []
    at = lines.index(f"      {name}:")
    out = []
    for line in lines[at + 1:]:
        if not line.startswith("        "):
            break
        out.append(line.strip())
    return out


for name in ("vertex_project", "workload_identity_provider", "service_account"):
    check(f"B6 {name} is a required input", "required: true" in input_lines(name), input_lines(name))
check("B6 vertex_location keeps its global default", "default: 'global'" in input_lines("vertex_location"), input_lines("vertex_location"))
check("B6 no provider input", "      provider:\n" not in inputs)
check("B6 no gemini_api_key secret, only the optional Slack one", "gemini_api_key" not in secrets and "slack_webhook_url" in secrets, secrets)
perms = block(VERTEX, "\npermissions:\n", "\njobs:")
check("B6 the workflow asks for id-token: write", "  id-token: write" in perms, perms)
check("S3 the access token lasts 30 minutes, not the default hour", "          access_token_lifetime: 1800s" in LINES)
for label, text in (("gemini-review.yml", MAIN), ("gemini-review-vertex.yml", VERTEX)):
    job_if = text.split("\n    if: >\n", 1)[1].split("\n    runs-on:", 1)[0]
    check(f"S6 {label}: a comment counts only when created", "github.event.action == 'created'" in job_if
          and job_if.strip().startswith("github.event_name == 'pull_request' ||"), job_if[:200])
    calls = [l.strip() for l in text.splitlines() if "python3 " in l and "scripts/" in l and not l.strip().startswith("#")]
    loose = [c for c in calls if "python3 -I " not in c and "python3 -s -E " not in c]
    check(f"S5 {label}: every script runs isolated from user site-packages and PYTHON* settings", calls and not loose, loose)
code = [l for l in LINES if not l.strip().startswith("#")]
check("B20 no ref input, and the action checkout uses only the workflow's own commit",
      "      ref:" not in inputs and "inputs.ref" not in VERTEX and "          ref: ${{ job.workflow_sha || 'v1' }}" in LINES)
check("B21 no inputs.provider and no gemini_api_key outside comments",
      not [l for l in code if "inputs.provider" in l or "gemini_api_key" in l], [l for l in code if "inputs.provider" in l or "gemini_api_key" in l])
check("B19 its concurrency group differs from gemini-review.yml's, so neither cancels the other",
      "      group: gemini-review-vertex-${{" in VERTEX and "      group: gemini-review-${{" in MAIN and "      group: gemini-review-${{" not in VERTEX)

auth_at, auth = step(LINES, "Sign in to Google Cloud")
text = "\n".join(auth)
check("B5 the sign-in step is pinned to the v3.0.0 commit",
      "        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3.0.0" in auth, text)
for line in ("token_format: access_token", "create_credentials_file: false", "export_environment_variables: false",
             "workload_identity_provider: ${{ inputs.workload_identity_provider }}", "service_account: ${{ inputs.service_account }}"):
    check(f"B5/B15 sign-in sets {line}", f"          {line}" in auth, text)
check("B15 sign-in runs only when there is something to send", "        if: steps.files.outputs.count > 0" in auth, text)
call_at, call = step(LINES, "Call Gemini API")
checkouts = [i for i, l in enumerate(LINES) if l.startswith("      - name: Checkout")]
check("B15 sign-in comes after both checkouts and directly before the call", auth_at > 0 and checkouts and max(checkouts) < auth_at and auth_at + len(auth) == call_at,
      (checkouts, auth_at, call_at))
check("B15 the call step's credential is the sign-in step's token, sent as a bearer",
      "          GEMINI_API_KEY: ${{ steps.auth.outputs.access_token }}" in call and "          AUTH_SCHEME: bearer" in call
      and "          PROVIDER: vertex" in call)
check("B15 the access token is read only by the call step",
      [i for i, l in enumerate(LINES) if "steps.auth.outputs.access_token" in l] == [i for i, l in enumerate(LINES)
       if "steps.auth.outputs.access_token" in l and call_at <= i < call_at + len(call)] != [])
check("B15 no secret is read in the Vertex workflow except the Slack webhook",
      [l.strip() for l in LINES if "secrets." in l] == ["webhook_url: ${{ secrets.slack_webhook_url }}"],
      [l.strip() for l in LINES if "secrets." in l])

print("its check step")
_, chk = step(LINES, "Check the provider")
check("B7 the check step's provider is fixed to vertex", "          PROVIDER: vertex" in chk)
script = "\n".join(l[10:] for l in chk[chk.index("        run: |") + 1:]) + "\n" if "        run: |" in chk else "exit 1\n"


def run(project="hyphn-studio-1013", location="global", model="gemini-4-argon", thinking="high"):
    out = pathlib.Path(tempfile.mkstemp()[1])
    env = dict(os.environ, PROVIDER="vertex", VERTEX_PROJECT=project, VERTEX_LOCATION=location, GEMINI_MODEL=model,
               THINKING_LEVEL=thinking, GITHUB_OUTPUT=str(out))
    p = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    url = out.read_text().partition("api_url=")[2].strip() or None
    out.unlink()
    return p, url


p, url = run()
check("B7 a valid project gives the Vertex URL", p.returncode == 0 and url ==
      "https://aiplatform.googleapis.com/v1/projects/hyphn-studio-1013/locations/global/publishers/google/models/gemini-4-argon:generateContent", url)
for label, kw in (("no project", dict(project="")), ("a bad project", dict(project="a/b")), ("a bad location", dict(location="us")),
                  ("minimal on argon", dict(thinking="minimal")), ("a bad model", dict(model="x?y"))):
    p, url = run(**kw)
    check(f"B7 {label}: refused, no URL", p.returncode != 0 and url is None, (p.returncode, p.stdout[-200:]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
