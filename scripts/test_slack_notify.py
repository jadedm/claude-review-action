#!/usr/bin/env python3
"""Cases for the Slack action's request (#66). Run: python3 scripts/test_slack_notify.py

Runs the action's own script with bash and the real curl against a local HTTP
server, and checks that the webhook URL never appears in a process's arguments.
Needs bash, curl, jq and ps. Standard library only.
"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

ACTION = pathlib.Path(__file__).resolve().parent.parent / "actions/slack-notify/action.yml"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:400]}")


def script():
    """The run: block of the action's only step, unindented."""
    lines = ACTION.read_text().splitlines()
    at = lines.index("      run: |")
    return "\n".join(l[8:] for l in lines[at + 1:]) + "\n"


class Server(http.server.BaseHTTPRequestHandler):
    received = []
    status = 200
    delay = 0.0

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Server.received.append((self.path, self.headers.get("Content-Type"), body))
        time.sleep(Server.delay)
        self.send_response(Server.status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"

with tempfile.TemporaryDirectory() as tmp:
    run_file = pathlib.Path(tmp, "run.sh")
    run_file.write_text(script())

    def start(url, message="Gemini reviewed PR #1"):
        env = {**os.environ, "SLACK_WEBHOOK_URL": url, "INPUT_MESSAGE": message}
        return subprocess.Popen(["bash", "-e", "-o", "pipefail", str(run_file)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def send(url, message="Gemini reviewed PR #1"):
        Server.received.clear()
        p = start(url, message)
        out, _ = p.communicate(timeout=30)
        return p.returncode, out

    secret_path = "/services/T000/B000/s3cretTOKENvalue"
    message = 'Gemini reviewed PR #7: a "quoted" title\nsecond line'
    code, out = send(base + secret_path, message)
    check("S1 a valid URL gets one POST with the message as JSON text",
          code == 0 and len(Server.received) == 1 and Server.received[0][0] == secret_path
          and Server.received[0][1] == "application/json"
          and json.loads(Server.received[0][2]) == {"text": message},
          (code, out, Server.received))

    Server.received.clear()
    Server.delay = 2.0
    p = start(base + secret_path)
    seen, listed = [], 0
    deadline = time.time() + 1.5
    while time.time() < deadline:
        ps = subprocess.run(["ps", "-axww", "-o", "args="], capture_output=True, text=True).stdout
        listed += ps.count("\n")
        seen += [l for l in ps.splitlines() if "s3cretTOKENvalue" in l]
        if Server.received:
            break
        time.sleep(0.05)
    time.sleep(0.2)
    ps = subprocess.run(["ps", "-axww", "-o", "args="], capture_output=True, text=True).stdout
    seen += [l for l in ps.splitlines() if "s3cretTOKENvalue" in l]
    curl_seen = any("curl" in l for l in ps.splitlines())
    out, _ = p.communicate(timeout=30)
    Server.delay = 0.0
    check("S2 while the request is open, curl is running and no process's arguments hold the URL",
          Server.received and curl_seen and listed > 0 and not seen and p.returncode == 0,
          (bool(Server.received), curl_seen, listed, seen, p.returncode, out))

    code, out = send("")
    check("S3 an empty URL warns, sends nothing and exits 0",
          code == 0 and not Server.received and "SLACK_WEBHOOK_URL is empty" in out, (code, out))

    for label, bad in [("a quote", '"'), ("a backslash", "\\"), ("a space", " "), ("a newline", "\n"), ("a carriage return", "\r"), ("a tab", "\t")]:
        code, out = send(base + secret_path + bad + "x")
        check(f"S4 a URL with {label} warns, sends nothing and exits 0",
              code == 0 and not Server.received and "holds a quote" in out, (code, out))

    Server.status = 500
    code, out = send(base + secret_path)
    Server.status = 200
    check("S5 a 500 from Slack fails the step", code != 0 and len(Server.received) == 1, (code, out))

server.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
