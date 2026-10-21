#!/usr/bin/env python3
"""Cases for the parallel Gemini calls (#30). Run: python3 scripts/test_gemini_calls.py

Runs the real "Call Gemini API" step, cut out of gemini-review.yml, against a
stub `curl` whose reply depends on the file names in the request: fail500,
timeout, flaky429 (429 once, then a review), unreadable, and slow (3 s).
Each run gets its own run folder (WORK, #61), as the workflow gives each job.

Standard library only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
WORKFLOW = REPO / ".github/workflows/gemini-review.yml"

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
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body, indent = [], None
    for line in lines[run + 1:]:
        if line.strip() and indent is None:
            indent = len(line) - len(line.lstrip())
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    return "\n".join(body) + "\n"


CALL = step_script("Call Gemini API")

STUB = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
out = args[args.index("-o") + 1]
body = sys.stdin.read()
state = os.environ["STUB_STATE"]
with open(os.path.join(state, "argv.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
# -H @file: curl reads headers from a file; record what it would send.
headers = [open(args[i + 1][1:]).read().strip() for i, a in enumerate(args[:-1]) if a == "-H" and args[i + 1].startswith("@")]
headers += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-H" and not args[i + 1].startswith("@")]
with open(os.path.join(state, "headers.jsonl"), "a") as f:
    f.write(json.dumps(headers) + "\n")
with open(os.path.join(state, "bodies.jsonl"), "a") as f:
    f.write(json.dumps(json.loads(body)) + "\n")  # json.loads: an invalid body fails the call
def reply(code, payload):
    with open(out, "w") as f:
        f.write(payload)
    sys.stdout.write(str(code))
    sys.exit(0)
def review(summary):
    text = json.dumps({"summary": summary, "issues": [{"file": summary, "line": 1, "description": "d",
                       "severity": "low", "category": "c"}], "positives": [], "overall_assessment": "comment"})
    n = sum(map(ord, summary)) % 1000  # distinct per file, so each call's log line can be told apart
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                       "usageMetadata": {"promptTokenCount": n, "thoughtsTokenCount": n + 1, "candidatesTokenCount": n + 2}})
name = next((n for n in ("fail500", "timeout", "flaky429", "flaky503", "fail403", "unreadable", "notjson", "slow", "fast", "plain") if f"--- {n}.py ---" in body), "plain")
if name == "fail500":
    reply(500, '{"error": {"message": "boom"}}')
if name == "timeout":
    sys.exit(28)
if name == "flaky429":
    marker = os.path.join(state, "flaky")
    if not os.path.exists(marker):
        open(marker, "w").close()
        reply(429, '{"error": {"message": "slow down"}}')
if name == "flaky503":
    marker = os.path.join(state, "flaky503")
    if not os.path.exists(marker):
        open(marker, "w").close()
        reply(503, '{"error": {"message": "unavailable"}}')
if name == "fail403":
    reply(403, '{"error": {"code": 403, "status": "PERMISSION_DENIED"}}')
if name == "unreadable":
    reply(200, json.dumps({"candidates": [{"content": {"parts": [{"text": "not a review"}]}, "finishReason": "STOP"}]}))
if name == "notjson":
    reply(200, "<html>not json</html>")
if name == "slow":
    time.sleep(3)
reply(200, review(name))
'''


AI_STUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
VERTEX_URL = "https://aiplatform.googleapis.com/v1/projects/proj-55/locations/global/publishers/google/models/m:generateContent"


def run_calls(chunk_names, thinking="high", model="m", plant=None, provider="ai_studio", api_url=AI_STUDIO_URL, auth="key"):
    """chunk_names: one list of file names per call. Returns (proc, review, coverage, seconds).
    The request bodies the stub received are in BODIES after the call."""
    work = pathlib.Path(tempfile.mkdtemp())
    run_dir = work / "_run"  # the run folder, under RUNNER_TEMP in the workflow
    run_dir.mkdir()
    chunks = run_dir / "review-chunks"
    (work / ".claude-review-action").symlink_to(REPO)
    for name in ("json.py", "secrets.py", "sys.py"):
        if plant:
            (work / name).write_text(f"open({str(plant)!r}, 'a').write(__name__ + '\\n')\n")
    bin_dir = work / "_bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(STUB)
    (bin_dir / "curl").chmod(0o755)
    state = work / "_state"
    state.mkdir()
    (work / "_rt").mkdir()  # RUNNER_TEMP, which the runner always provides
    if chunk_names is None:
        chunk_names = []  # the planner made no calls and wrote no chunk directory
    for n, names in enumerate(chunk_names, 1):
        d = chunks / str(n)
        d.mkdir(parents=True)
        (d / "code.txt").write_text("".join(f"--- {name} ---\nx = 1\n\n" for name in names))
        (d / "diff.txt").write_text("")
        (d / "files.json").write_text(json.dumps(names))
    (run_dir / "review-prompt.txt").write_text("Review this.\n")
    (run_dir / "review-coverage.json").write_text(json.dumps({"omitted": [], "filtered": 0, "diff_sent": 0, "diff_total": 0}))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GEMINI_API_KEY="k-SECRET-55", GEMINI_MODEL=model, STUB_STATE=str(state), RUNNER_TEMP=str(work / "_rt"),
               THINKING_LEVEL=thinking, PROVIDER=provider, API_URL=api_url, AUTH_SCHEME=auth, WORK=str(run_dir))
    start = time.monotonic()
    proc = subprocess.run(["bash", "-e", "-c", CALL], cwd=work, env=env, capture_output=True, text=True)
    seconds = time.monotonic() - start
    out = run_dir / "gemini-review.json"
    review = json.loads(out.read_text()) if out.exists() else None
    coverage = json.loads((run_dir / "review-coverage.json").read_text())
    bodies = state / "bodies.jsonl"
    BODIES[:] = [json.loads(l) for l in bodies.read_text().splitlines()] if bodies.exists() else []
    argv = state / "argv.jsonl"
    ARGV[:] = [json.loads(l) for l in argv.read_text().splitlines()] if argv.exists() else []
    hdr = state / "headers.jsonl"
    HEADERS[:] = [json.loads(l) for l in hdr.read_text().splitlines()] if hdr.exists() else []
    LEFT[:] = [p.name for d in (work / "_rt", run_dir) for p in d.glob("gemini-auth.*")]
    shutil.rmtree(work)
    return proc, review, coverage, seconds


BODIES = []
HEADERS = []
LEFT = []
ARGV = []


def reasons(coverage):
    return {o["file"]: o["reason"] for o in coverage["omitted"]}


print("parallel calls")
proc, review, cov, secs = run_calls([["plain.py"]])
check("30.1 one call: its review is written as it came", proc.returncode == 0 and review["summary"] == "plain" and "parts" not in review,
      (proc.stdout[-400:], review))
proc, review, cov, secs = run_calls([["fail500.py"], ["plain.py"]])
check("30.10 one call gets HTTP 500: the other's review posts and the step exits 0",
      proc.returncode == 0 and [p["summary"] for p in review["parts"]] == ["plain"], (proc.stdout[-600:], review))
check("  the failed call's files are listed, and its error is in the log",
      reasons(cov) == {"fail500.py": "review call failed"} and "HTTP 500" in proc.stdout, (cov, proc.stdout[-400:]))
proc, review, cov, secs = run_calls([["fail500.py"], ["timeout.py"]])
check("30.12 every call fails: the step fails with each error printed",
      proc.returncode != 0 and "HTTP 500" in proc.stdout and "curl exit 28" in proc.stdout and review is None,
      (proc.returncode, proc.stdout[-600:]))
proc, review, cov, secs = run_calls([["slow.py"], ["slow.py"], ["slow.py"]])
check("30.13 three 3-second calls finish in under 6 s, so they ran in parallel", proc.returncode == 0 and secs < 6, secs)
proc, review, cov, secs = run_calls([["slow.py"], ["fast.py"]])
check("30.21 a slow call 1 and a fast call 2: merged in call order",
      [p["label"] for p in review["parts"]] == ["Part 1 of 2", "Part 2 of 2"]
      and [p["summary"] for p in review["parts"]] == ["slow", "fast"], review)
proc, review, cov, secs = run_calls([["flaky429.py"], ["timeout.py"], ["fail500.py"]])
check("30.22 a 429 then success, a timeout and a 500 in one run: the first posts, the others are listed",
      proc.returncode == 0 and [p["summary"] for p in review["parts"]] == ["flaky429"]
      and reasons(cov) == {"timeout.py": "review call failed", "fail500.py": "review call failed"}, (proc.stdout[-600:], cov))
proc, review, cov, secs = run_calls([["unreadable.py"], ["plain.py"]])
check("30.11 an unreadable reply beside a good one: posts, the unreadable call's files listed",
      proc.returncode == 0 and reasons(cov) == {"unreadable.py": "reply unreadable"}, cov)

proc, review, cov, secs = run_calls(None)
check("30.28 no planned calls: the step exits 0 and writes a nothing-sent review",
      proc.returncode == 0 and review == {"nothing_sent": True}, (proc.returncode, proc.stdout[-300:], proc.stderr[-300:]))

print("thinking (#33)")
proc, review, cov, secs = run_calls([["plain.py"]])
check("33.1 default: thinkingLevel high is sent",
      proc.returncode == 0 and [b["generationConfig"].get("thinkingConfig") for b in BODIES] == [{"thinkingLevel": "high"}], BODIES)
check("  and the rest of generationConfig is unchanged",
      {k: v for k, v in BODIES[0]["generationConfig"].items() if k != "thinkingConfig"}
      == {"maxOutputTokens": 65536, "temperature": 1.0, "responseMimeType": "application/json"}, BODIES)
check("33.8 the log names the call's tokens", "Tokens: prompt" in proc.stdout, proc.stdout[-300:])
for level in ("none", ""):
    proc, review, cov, secs = run_calls([["plain.py"]], thinking=level)
    check(f"33.2 thinking_level {level!r}: no thinkingConfig",
          proc.returncode == 0 and BODIES and "thinkingConfig" not in BODIES[0]["generationConfig"], BODIES)
for level in ("minimal", "low", "medium"):
    proc, review, cov, secs = run_calls([["plain.py"]], thinking=level)
    check(f"33.4 thinking_level {level}: sent as given",
          BODIES and BODIES[0]["generationConfig"].get("thinkingConfig") == {"thinkingLevel": level}, BODIES)
for level in ("max", 'high"}, "x": {"', "HIGH"):
    proc, review, cov, secs = run_calls([["plain.py"]], thinking=level)
    check(f"33.5 thinking_level {level!r} fails before any call",
          proc.returncode != 0 and "thinking_level must be" in proc.stdout + proc.stderr and BODIES == [], (proc.returncode, BODIES))
proc, review, cov, secs = run_calls([["plain.py"]])
check("33.6 curl is given --max-time 420", ARGV and ARGV[0][ARGV[0].index("--max-time") + 1] == "420", ARGV[:1])
proc, review, cov, secs = run_calls([["timeout.py"], ["plain.py"]])
check("33.6 a timeout says 420 s", "curl exit 28; 28 means no reply within 420 s" in proc.stdout, proc.stdout[-300:])
proc, review, cov, secs = run_calls([["slow.py"], ["fast.py"]])
expect = {name: sum(map(ord, name)) % 1000 for name in ("slow", "fast")}
check("33.8 two calls each log their own tokens",
      all(f"Tokens: prompt {n}, thinking {n + 1}, output {n + 2}" in proc.stdout for n in expect.values()), proc.stdout[-500:])
proc, review, cov, secs = run_calls([["unreadable.py"]])
check("33.8 a reply with no usage says not reported rather than 0",
      "Tokens: prompt None, thinking not reported, output None" in proc.stdout, proc.stdout[-300:])
proc, review, cov, secs = run_calls([["notjson.py"], ["plain.py"]])
check("33.8 a 200 that is not JSON: the token line says unreadable, no traceback, and the call still records its status",
      "Tokens: unreadable" in proc.stdout and reasons(cov) == {"notjson.py": "review call failed"} and "Traceback" not in proc.stdout
      and "Call 1: failed" in proc.stdout,
      (cov, proc.stdout[-400:]))
for model in ("gemini-2.5-flash", "gemini-1.5-pro"):
    proc, review, cov, secs = run_calls([["plain.py"]], model=model)
    check(f"33.11 {model}: no thinkingLevel sent, with a notice",
          BODIES and "thinkingConfig" not in BODIES[0]["generationConfig"] and "does not take thinking_level" in proc.stdout, BODIES)
proc, review, cov, secs = run_calls([["plain.py"]], model="gemini-3.8-flash")
check("33.11 gemini-3.8-flash still gets thinkingLevel", BODIES and BODIES[0]["generationConfig"].get("thinkingConfig") == {"thinkingLevel": "high"}, BODIES)
mark = pathlib.Path(tempfile.mkdtemp()) / "imported"
proc, review, cov, secs = run_calls([["plain.py"]], plant=mark)
check("35.17 a json.py in the checkout is never imported by the Call step", proc.returncode == 0 and not mark.exists(),
      mark.read_text() if mark.exists() else proc.stdout[-200:])
shutil.rmtree(mark.parent)
wf = WORKFLOW.read_text().splitlines()
at = wf.index("      thinking_level:")
block = wf[at + 1:at + 4]  # description, type, default
check("33.1 the workflow's own default is high, and the step reads the input",
      "        default: 'high'" in block and "          THINKING_LEVEL: ${{ inputs.thinking_level }}" in wf, block)

print("vertex (#55)")
proc, review, cov, secs = run_calls([["plain.py"]])
check("55.1 ai_studio: URL, key header and body as before (no role, lowercase level)",
      ARGV and AI_STUDIO_URL in ARGV[0] and "X-goog-api-key: k-SECRET-55" in HEADERS[0]
      and BODIES[0]["contents"] == [{"parts": [{"text": BODIES[0]["contents"][0]["parts"][0]["text"]}]}]
      and BODIES[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}, (ARGV[:1], BODIES[:1]))
text = BODIES[0]["contents"][0]["parts"][0]["text"]
check("55.1 ai_studio: the whole body is the old shape, key for key",
      BODIES[0] == {"contents": [{"parts": [{"text": text}]}], "generationConfig": {"maxOutputTokens": 65536, "temperature": 1.0,
                    "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "high"}}}, BODIES[:1])
proc, review, cov, secs = run_calls([["plain.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.2 vertex: the Vertex URL, the key header, role user, level HIGH, and a review",
      proc.returncode == 0 and review["summary"] == "plain" and ARGV and VERTEX_URL in ARGV[0] and AI_STUDIO_URL not in ARGV[0]
      and "X-goog-api-key: k-SECRET-55" in HEADERS[0] and BODIES[0]["contents"][0].get("role") == "user"
      and BODIES[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH"}, (proc.stdout[-300:], ARGV[:1], BODIES[:1]))
for level, want in (("none", None), ("", None), ("low", "LOW"), ("medium", "MEDIUM"), ("high", "HIGH")):
    proc, review, cov, secs = run_calls([["plain.py"]], thinking=level, provider="vertex", api_url=VERTEX_URL)
    got = BODIES[0]["generationConfig"].get("thinkingConfig") if BODIES else "no call"
    check(f"55.14 vertex thinking_level {level!r}: {want or 'no thinkingConfig'}",
          got == ({"thinkingLevel": want} if want else None), got)
proc, review, cov, secs = run_calls([["flaky429.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.6 vertex 429 then success: retried and posted", proc.returncode == 0 and review["summary"] == "flaky429" and len(BODIES) == 2,
      (proc.stdout[-300:], len(BODIES)))
proc, review, cov, secs = run_calls([["flaky503.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.15 vertex 503 then success: retried and posted", proc.returncode == 0 and review["summary"] == "flaky503" and len(BODIES) == 2,
      (proc.stdout[-300:], len(BODIES)))
proc, review, cov, secs = run_calls([["fail403.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.6b vertex 403: the step fails and the log names Vertex AI and the status",
      proc.returncode != 0 and "Vertex AI error (HTTP 403)" in proc.stdout and "PERMISSION_DENIED" in proc.stdout and "Gemini API" not in proc.stdout,
      (proc.returncode, proc.stdout[-400:]))
proc, review, cov, secs = run_calls([["plain.py"], ["slow.py"], ["fast.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.7 three chunks on vertex: every request goes to the Vertex URL",
      len(ARGV) == 3 and all(VERTEX_URL in a and AI_STUDIO_URL not in a for a in ARGV), [a[:3] for a in ARGV])
proc, review, cov, secs = run_calls([["fail403.py"], ["timeout.py"]], provider="vertex", api_url=VERTEX_URL)
check("55.8 the key is never in the log, on success or error", "k-SECRET-55" not in proc.stdout + proc.stderr, proc.stdout[-300:])
proc, review, cov, secs = run_calls([["plain.py"]], provider="vertex", api_url=VERTEX_URL, auth="bearer")
check("B4 bearer: Authorization Bearer is sent and no X-goog-api-key",
      proc.returncode == 0 and HEADERS and "Authorization: Bearer k-SECRET-55" in HEADERS[0] and not any("X-goog-api-key" in h for h in HEADERS[0]),
      ARGV[:1])
for bad in ("typo", "", "Bearer"):
    proc, review, cov, secs = run_calls([["plain.py"]], auth=bad)
    check(f"B16 AUTH_SCHEME {bad!r}: fails before any call", proc.returncode != 0 and BODIES == [] and "AUTH_SCHEME must be" in proc.stdout,
          (proc.returncode, proc.stdout[-200:]))
proc, review, cov, secs = run_calls([["fail403.py"], ["plain.py"]], provider="vertex", api_url=VERTEX_URL, auth="bearer")
check("B8 a bearer token is never in the log", "k-SECRET-55" not in proc.stdout + proc.stderr, proc.stdout[-300:])
proc, review, cov, secs = run_calls([["plain.py"], ["fast.py"]])
check("S4 the credential reaches curl through a file, never on its command line",
      len(ARGV) == 2 and not any("k-SECRET-55" in a for argv in ARGV for a in argv) and all(any("k-SECRET-55" in h for h in hs) for hs in HEADERS),
      (ARGV[:1], HEADERS))
check("S4 the header file is gone when the step ends", proc.returncode == 0 and LEFT == [], LEFT)
wf = WORKFLOW.read_text()
check("B3 gemini-review.yml sends the key header by default", "          AUTH_SCHEME: key" in wf and "AUTH_SCHEME: bearer" not in wf)
proc, review, cov, secs = run_calls([["plain.py"]], api_url="")
check("55.3 no API URL (the check step did not run): fails before any call",
      proc.returncode != 0 and BODIES == [] and "no API URL" in proc.stdout, (proc.returncode, proc.stdout[-200:]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
