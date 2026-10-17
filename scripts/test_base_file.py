#!/usr/bin/env python3
"""Cases for base_file.py (#37). Run: python3 scripts/test_base_file.py

A local HTTP server plays the GitHub API, so the script makes real requests.
Standard library only.
"""
import base64
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "base_file.py"
BASE = "b" * 40

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


STATE = {"files": {}, "status": None, "requests": []}


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        STATE["requests"].append((self.path, self.headers.get("Authorization")))
        if STATE["status"]:
            return self.send(STATE["status"], {"message": "boom"})
        if self.path == "/repos/o/r/pulls/7" and not STATE.get("no_pr"):
            return self.send(200, {"base": {"sha": BASE}, "head": {"sha": "h" * 40}})
        prefix = "/repos/o/r/contents/"
        if self.path.startswith(prefix) and self.path.endswith(f"?ref={BASE}"):
            key = self.path[len(prefix):-len(f"?ref={BASE}")]
            item = STATE["files"].get(key)
            if item is None:
                return self.send(404, {"message": "Not Found"})
            return self.send(200, item)
        return self.send(404, {"message": "Not Found"})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
threading.Thread(target=server.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{server.server_address[1]}"


def file_item(text):
    raw = text.encode()
    return {"type": "file", "encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()}


def run(path, files=None, status=None, no_pr=False, out=None):
    STATE.update(files=files or {}, status=status, requests=[], no_pr=no_pr)
    out = out or pathlib.Path(tempfile.mkdtemp()) / "extras.md"
    env = dict(os.environ, GH_TOKEN="tok", GITHUB_API_URL=API)
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "o/r", "7", path, str(out)], capture_output=True, text=True, env=env)
    text = out.read_text() if out.exists() else None
    return p, text


print("base_file.py")
p, text = run(".github/review-extras.md", {".github/review-extras.md": file_item("BASE RULES\n")})
check("37.1 the base commit's text is written, and the log names the base commit and size",
      p.returncode == 0 and text == "BASE RULES\n" and f"from the base commit {BASE} (11 bytes)" in p.stdout, (p.stdout, p.stderr, text))
check("  it asked for the PR, then the file at the base sha, with the token",
      [r[0] for r in STATE["requests"]] == ["/repos/o/r/pulls/7", f"/repos/o/r/contents/.github/review-extras.md?ref={BASE}"]
      and all(r[1] == "Bearer tok" for r in STATE["requests"]), STATE["requests"])
p, text = run(".github/review-extras.md")
check("37.2 not on the base branch: warning, nothing written, exit 0",
      p.returncode == 0 and text is None and "::warning::" in p.stdout and "not on the base branch" in p.stdout, (p.returncode, p.stdout))
p, text = run(".github/review-extras.md", {".github/review-extras.md": {"type": "symlink", "target": "../.git/config"}})
check("37.3 a link that is not a file on the base branch: the step fails", p.returncode == 1 and text is None and "symlink on the base branch" in p.stdout, p.stdout)
p, text = run(".github/rules", {".github/rules": [{"type": "file", "name": "a.md"}]})
check("37.3 a folder on the base branch: the step fails", p.returncode == 1 and text is None and "a folder on the base branch" in p.stdout, p.stdout)
big = {"type": "file", "encoding": "none", "size": 5_000_000, "content": ""}
p, text = run("big.md", {"big.md": big})
check("37.3 too large: the step fails", p.returncode == 1 and text is None and "too large" in p.stdout, p.stdout)
for code in (500, 401):
    p, text = run(".github/review-extras.md", status=code)
    check(f"37.4 API {code}: the step fails with the error", p.returncode == 1 and "::error::could not read the review rules" in p.stdout and text is None, (p.returncode, p.stdout))
p, text = run("docs/house rules.md", {"docs/house%20rules.md": file_item("SPACED\n")})
check("37.5 a path with a space is encoded", p.returncode == 0 and text == "SPACED\n", (p.stdout, STATE["requests"]))
for bad in ("../secret.md", "/etc/passwd", "a//b.md", "./a.md"):
    p, text = run(bad)
    check(f"37.5 {bad!r} refused before any call, and the step fails", p.returncode == 1 and text is None and STATE["requests"] == [] and "not a plain path" in p.stdout, (STATE["requests"], p.stdout))
p, text = run(".github/review-extras.md", {".github/review-extras.md": file_item("x\n")}, no_pr=True)
check("37.8 the pull request lookup returning 404 fails the step; it is not read as no file",
      p.returncode == 1 and "could not read the review rules" in p.stdout and text is None, (p.returncode, p.stdout))
stale = pathlib.Path(tempfile.mkdtemp()) / "extras.md"
stale.write_text("STALE RULES FROM AN EARLIER RUN\n")
p, text = run(".github/review-extras.md", out=stale)
check("37.9 an earlier run's output file is removed when the base has none", p.returncode == 0 and text is None and not stale.exists(), (p.stdout, text))

server.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
