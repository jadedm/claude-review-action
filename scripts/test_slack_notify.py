#!/usr/bin/env python3
"""Cases for the Slack action's request (#66, #62). Run: python3 scripts/test_slack_notify.py

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

    def start(url, message="Gemini reviewed PR #1", title=""):
        env = {**os.environ, "SLACK_WEBHOOK_URL": url, "INPUT_MESSAGE": message, "INPUT_TITLE": title}
        return subprocess.Popen(["bash", "-e", "-o", "pipefail", str(run_file)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def send(url, message="Gemini reviewed PR #1", title=""):
        Server.received.clear()
        p = start(url, message, title)
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

    def sent_text(message, title):
        """The text field Slack would get, or None if the request failed."""
        code, out = send(base + secret_path, message, title)
        if code != 0 or len(Server.received) != 1:
            return None, (code, out, Server.received)
        text = json.loads(Server.received[0][2])["text"]
        return text, text

    def title_case(label, title, expect, message="PR #1: {title}"):
        text, detail = sent_text(message, title)
        check(label, text == expect, (detail, "expected", expect))

    # The title as plain text (#62). Plan on the ticket, cases T1 to T17.
    title_case("T1 a plain title replaces {title}", "Fix the parser", "PR #1: Fix the parser")
    title_case("T2 &, < and > in the title are escaped", "a < b && c > d",
               "PR #1: a &lt; b &amp;&amp; c &gt; d")
    title_case("T3 mention tokens in the title are sent as text",
               "<!channel> <!here> <@U123>", "PR #1: &lt;!channel&gt; &lt;!here&gt; &lt;@U123&gt;")
    title_case("T4 link syntax in the title is sent as text",
               "<https://example.com|click>", "PR #1: &lt;https://example.com|click&gt;")
    title_case("T5 an entity in the title reads back as literal text", "&amp;", "PR #1: &amp;amp;")
    title_case("T6 * _ ~ ` pass through unchanged", "*bold* _it_ ~st~ `code`",
               "PR #1: *bold* _it_ ~st~ `code`")
    title_case("T7 bare @channel and @here pass through unchanged", "@channel @here",
               "PR #1: @channel @here")
    title_case("T8 newline, carriage return and tab become spaces", "a\nb\rc\td",
               "PR #1: a b c d")
    title_case("T9 a 300 character title is cut to 200 plus ...", "x" * 300,
               "PR #1: " + "x" * 200 + "...")
    title_case("T10 a 200 character title is sent whole", "y" * 200, "PR #1: " + "y" * 200)
    title_case("T11 the cut comes before escaping, so no escape is cut in half", "<" * 300,
               "PR #1: " + "&lt;" * 200 + "...")
    uni = "Caf\u00e9 \u4fee\u6b63 \U0001f680 na\u00efve"
    title_case("T12a a unicode title is sent unchanged", uni, "PR #1: " + uni)
    title_case("T12b the cut counts characters, not bytes", "\u00e9" * 250,
               "PR #1: " + "\u00e9" * 200 + "...")
    title_case("T13 an empty title leaves the rest of the message", "", "PR #1: ")
    title_case("T14 a title holding {title} is not expanded again", "see {title}",
               "PR #1: see {title}")
    fixed = "*Claude* on PR #3: {title}\nRepo: o/r\n<https://github.com/o/r/pull/3|View PR>"
    title_case("T15 the message's own formatting is sent unchanged, only the title is escaped",
               "<!here>", fixed.replace("{title}", "&lt;!here&gt;"), message=fixed)
    plain = "*Gemini* reviewed PR #9: raw <title> & more\n<https://x/9|View PR>"
    title_case("T16 with no title input and no {title}, the message is sent as is", "", plain,
               message=plain)
    title_case("T17 every {title} is replaced", "a&b", "a&amp;b / a&amp;b", message="{title} / {title}")

server.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
