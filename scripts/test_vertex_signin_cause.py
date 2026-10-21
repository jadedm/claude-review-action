#!/usr/bin/env python3
"""Cases for naming a refused Vertex sign-in on the pull request (#70, item 2).

Run: python3 scripts/test_vertex_signin_cause.py

Runs scripts/vertex_signin_cause.py against a local HTTP server that plays
GitHub's OIDC token endpoint and Google's token service, and runs the
generated workflow's "Explain a refused sign-in" and "Post the sign-in
cause" steps in bash with a stub `gh`. Case numbers match the plan on #70.
Standard library only.
"""
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = HERE / "vertex_signin_cause.py"
VERTEX = (REPO / ".github/workflows/gemini-review-vertex.yml").read_text()
LINES = VERTEX.splitlines()
JWT = "eyJ-FAKE-OIDC-JWT-70"
FEDERATED = "ya29.FAKE-FEDERATED-70"
PROVIDER = "projects/123/locations/global/workloadIdentityPools/github-pr-review/providers/github"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


STATE = {"sts": None, "oidc": 200, "requests": []}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        STATE["requests"].append(("GET", self.path, self.headers.get("Authorization"), None))
        if STATE["oidc"] != 200:
            return self.send(STATE["oidc"], {"message": "no"})
        return self.send(200, {"value": JWT})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        STATE["requests"].append(("POST", self.path, None, body))
        code, reply = STATE["sts"]
        if code == "hang":
            import time
            time.sleep(3)
            return self.send(500, {})
        return self.send(code, reply)


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"


def classify(sts, oidc=200, env=None, sts_url=None):
    STATE.update(sts=sts, oidc=oidc, requests=[])
    e = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONS_ID_TOKEN")}
    e.update({"ACTIONS_ID_TOKEN_REQUEST_URL": f"{BASE}/oidc?api-version=2.0", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "gh-request-token",
              "WORKLOAD_IDENTITY_PROVIDER": PROVIDER} if env is None else env)
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "classify", "--sts-url", sts_url or f"{BASE}/v1/token"],
                       env=e, capture_output=True, text=True, timeout=60)
    return p


REFUSED = (401, {"error": "unauthorized_client", "error_description": "The given credential is rejected by the attribute condition."})
p = classify(REFUSED)
check("70.1 refused by the attribute condition: attribute_condition", p.returncode == 0 and p.stdout.strip() == "attribute_condition",
      (p.returncode, p.stdout, p.stderr[-300:]))
get = [r for r in STATE["requests"] if r[0] == "GET"]
post = [r for r in STATE["requests"] if r[0] == "POST"]
check("70.1 the OIDC request asks for the auth action's audience with the request token",
      len(get) == 1 and get[0][1] == "/oidc?api-version=2.0&audience=https%3A%2F%2Fiam.googleapis.com%2F" + PROVIDER.replace("/", "%2F")
      and get[0][2] == "bearer gh-request-token", get)
check("70.1 the exchange offers that token for the same provider",
      len(post) == 1 and post[0][3]["audience"] == "//iam.googleapis.com/" + PROVIDER and post[0][3]["subjectToken"] == JWT
      and post[0][3]["subjectTokenType"] == "urn:ietf:params:oauth:token-type:jwt", post)
check("70.6 the OIDC token is not in the output", JWT not in p.stdout + p.stderr)

p = classify((400, {"error": "invalid_target", "error_description": "The target service indicated by the \"audience\" parameters is invalid."}))
check("70.2 refused for another reason: other", p.stdout.strip() == "other", p.stdout)
p = classify((400, b"<html>not json</html>"))
check("70.4 a reply that is not JSON: other, no traceback", p.stdout.strip() == "other" and "Traceback" not in p.stderr, p.stderr[-200:])
p = classify((200, {"access_token": FEDERATED, "token_type": "Bearer"}))
check("70.5 the exchange is accepted (the failure was later): other, and the token is not printed",
      p.stdout.strip() == "other" and FEDERATED not in p.stdout + p.stderr, p.stdout)
p = classify(REFUSED, sts_url="http://127.0.0.1:9/v1/token")
check("70.4 the token service unreachable: other", p.returncode == 0 and p.stdout.strip() == "other", (p.stdout, p.stderr[-200:]))
p = classify(REFUSED, oidc=500)
check("70.4 GitHub's OIDC endpoint fails: other", p.stdout.strip() == "other" and not [r for r in STATE["requests"] if r[0] == "POST"], p.stdout)
p = classify(REFUSED, env={"WORKLOAD_IDENTITY_PROVIDER": PROVIDER})
check("70.3 no OIDC request URL (a fork's pull_request run): no_token, nothing requested",
      p.stdout.strip() == "no_token" and STATE["requests"] == [], (p.stdout, STATE["requests"]))
p = classify(REFUSED, env={"ACTIONS_ID_TOKEN_REQUEST_URL": f"{BASE}/oidc", "WORKLOAD_IDENTITY_PROVIDER": PROVIDER})
check("70.3 a request URL but no request token: no_token", p.stdout.strip() == "no_token", p.stdout)
p = classify(("hang", None))
check("70.4 a token service that answers 500 slowly: other", p.stdout.strip() == "other", p.stdout)

print("messages")


def message(code):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "message", code], capture_output=True, text=True)


m = message("attribute_condition")
check("70.1 the attribute_condition line names the setup script, the v1 SHAs and the repository list",
      m.returncode == 0 and "setup_vertex_wif.sh" in m.stdout and "v1 tag object and commit SHAs" in m.stdout
      and "this repository in --repos" in m.stdout
      and len(m.stdout.strip().splitlines()) == 1, m.stdout)
m = message("other")
check("70.2 the other line points at the sign-in step log", "Sign in to Google Cloud step log" in m.stdout, m.stdout)
for code in ("no_token", "", "attribute_condition\nx", "../x"):
    m = message(code)
    check(f"70.3/8 code {code!r}: no line", m.returncode == 0 and m.stdout == "", m.stdout)
check("70 a bad call prints usage and exits 2", subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True).returncode == 2)
for text in (message("attribute_condition").stdout, message("other").stdout):
    check("70 the fixed lines carry no mention, link or markup", not any(c in text for c in "@<>[]`*") and "http" not in text, text)

print("workflow steps")


def step(name):
    if f"      - name: {name}" not in LINES:
        return []
    s = LINES.index(f"      - name: {name}")
    e = next((i for i in range(s + 1, len(LINES)) if LINES[i].startswith("      - name:") or LINES[i].startswith("  post:")), len(LINES))
    return LINES[s:e]


def body(lines):
    at = lines.index("        run: |")
    return "\n".join(l[10:] for l in lines[at + 1:]) + "\n"


auth, explain, post_cause = step("Sign in to Google Cloud"), step("Explain a refused sign-in"), step("Post the sign-in cause")
check("70.9 the sign-in step continues on error so the cause step can run", "        continue-on-error: true" in auth, auth[:10])
check("70.9 the cause step runs only when the sign-in step failed", "        if: steps.auth.outcome == 'failure'" in explain, explain[:6])
check("70.7 the cause step is in the gemini job, the post step in the post job",
      explain and post_cause and LINES.index(explain[0]) < LINES.index("  post:") < LINES.index(post_cause[0]))
check("70 the gemini job outputs the cause", "      signin_cause: ${{ steps.signin-cause.outputs.cause }}" in LINES)
check("70 the post step reads the code from the gemini job's output", "          CAUSE: ${{ needs.gemini.outputs.signin_cause }}" in post_cause
      and "        if: needs.gemini.outputs.signin_cause != ''" in post_cause, post_cause)
check("70 gemini-review.yml (AI Studio) has neither step", "signin" not in (REPO / ".github/workflows/gemini-review.yml").read_text())

work = pathlib.Path(tempfile.mkdtemp())
(work / ".claude-review-action").symlink_to(REPO)
out = work / "_out"
env = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONS_ID_TOKEN")}
p = subprocess.run(["bash", "-e", "-c", body(explain)], cwd=work, env=dict(env, GITHUB_OUTPUT=str(out), WORKLOAD_IDENTITY_PROVIDER=PROVIDER),
                   capture_output=True, text=True)
check("70.9 the cause step writes its code and fails the job", p.returncode == 1 and out.read_text() == "cause=no_token\n"
      and "refused the sign-in (no_token)" in p.stdout, (p.returncode, out.read_text() if out.exists() else None, p.stdout))

(work / "_bin").mkdir()
(work / "_bin/gh").write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$GH_ARGS\"\n")
(work / "_bin/gh").chmod(0o755)
for code, posts in (("attribute_condition", True), ("other", True), ("no_token", False), ("bogus", False)):
    args = work / f"_args_{code}"
    p = subprocess.run(["bash", "-e", "-c", body(post_cause)], cwd=work, capture_output=True, text=True,
                       env=dict(os.environ, PATH=f"{work / '_bin'}:{os.environ['PATH']}", GH_ARGS=str(args), CAUSE=code, PR_NUMBER="9",
                                GH_TOKEN="x", GH_REPO="o/r"))
    sent = args.read_text().splitlines() if args.exists() else []
    want = ["pr", "comment", "9", "--body", message(code).stdout.strip()] if posts else []
    check(f"70.1/2/3/8 post step, code {code}: {'posts its line' if posts else 'posts nothing'}, exits 0",
          p.returncode == 0 and sent == want, (p.returncode, sent, p.stderr[-200:]))
shutil.rmtree(work)
SERVER.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
