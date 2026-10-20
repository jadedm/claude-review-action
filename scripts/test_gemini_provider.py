#!/usr/bin/env python3
"""Cases for choosing where Gemini is called (#55). Run: python3 scripts/test_gemini_provider.py

Runs the real "Check the provider" step, cut out of gemini-review.yml, in bash
with the inputs as environment variables, and reads the URL it writes to
GITHUB_OUTPUT. Standard library only.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github/workflows/gemini-review.yml"
LINES = WORKFLOW.read_text().splitlines()

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def step(name):
    start = LINES.index(f"      - name: {name}")
    stop = next(i for i in range(start + 1, len(LINES)) if LINES[i].startswith("      - name:"))
    return start, LINES[start:stop]


START, STEP = step("Check the provider")
RUN = STEP.index("        run: |")
SCRIPT = "\n".join(l[10:] for l in STEP[RUN + 1:]) + "\n"


def run(provider="ai_studio", model="gemini-4-argon", project="", location="global", thinking="high"):
    out = pathlib.Path(tempfile.mkstemp()[1])
    env = dict(os.environ, PROVIDER=provider, GEMINI_MODEL=model, VERTEX_PROJECT=project, VERTEX_LOCATION=location,
               THINKING_LEVEL=thinking, GITHUB_OUTPUT=str(out))
    p = subprocess.run(["bash", "-e", "-c", SCRIPT], env=env, capture_output=True, text=True)
    written = out.read_text()
    out.unlink()
    url = written.split("api_url=", 1)[1].strip() if "api_url=" in written else None
    return p, url


print("where the step runs")
first = next(i for i, l in enumerate(LINES) if l.startswith("      - name:"))
check("55.13 it is the job's first step", START == first, (START, first))
check("55.13 it has no if:, so a pull request with nothing to send still runs it",
      not any(l.strip().startswith("if:") for l in STEP), STEP[:3])
check("55.3 the Call step reads the URL from it", "          API_URL: ${{ steps.provider.outputs.api_url }}" in LINES)
check("S1 gemini-review.yml has no provider or Vertex inputs, and its check step is fixed to AI Studio",
      not [l for l in LINES if l in ("      provider:", "      vertex_project:", "      vertex_location:")]
      and "          PROVIDER: ai_studio" in STEP and "inputs.provider" not in "\n".join(LINES))

print("URLs")
p, url = run()
check("55.1 ai_studio: the same URL as before", p.returncode == 0
      and url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-argon:generateContent", (p.stdout, url))
p, url = run("vertex", project="hyphn-studio-1013")
check("55.2 vertex, global: the global host and the project path", p.returncode == 0 and url ==
      "https://aiplatform.googleapis.com/v1/projects/hyphn-studio-1013/locations/global/publishers/google/models/gemini-4-argon:generateContent",
      (p.stdout, url))
p, url = run("vertex", project="hyphn-studio-1013", location="us-central1")
check("55.2 vertex, a region: the regional host", p.returncode == 0 and url ==
      "https://us-central1-aiplatform.googleapis.com/v1/projects/hyphn-studio-1013/locations/us-central1/publishers/google/models/gemini-4-argon:generateContent",
      (p.stdout, url))
p, url = run("ai_studio", project="ignored-project", location="nowhere!")
check("55.1 ai_studio ignores the Vertex inputs", p.returncode == 0 and "generativelanguage" in url, (p.stdout, url))
p, url = run("vertex", project="hyphn-studio-1013", model="gemini-3.8-flash", thinking="minimal")
check("55.5 vertex, another model, minimal: left to Vertex", p.returncode == 0 and "aiplatform" in url, (p.stdout, url))
p, url = run("ai_studio", thinking="minimal")
check("55.1 ai_studio still allows minimal", p.returncode == 0 and url, p.stdout)

print("refused before any call")
REFUSE = [
    ("55.3 vertex with no project", dict(provider="vertex"), "needs vertex_project"),
    ("55.4 a provider that is neither", dict(provider="openai"), "provider must be ai_studio or vertex"),
    ("55.4 provider in capitals", dict(provider="Vertex"), "provider must be ai_studio or vertex"),
    ("55.4 a project with a slash", dict(provider="vertex", project="proj/../x"), "vertex_project must be"),
    ("55.4 a project with a query", dict(provider="vertex", project="proj-123?key=x"), "vertex_project must be"),
    ("55.4 a project in capitals", dict(provider="vertex", project="Hyphn-Studio"), "vertex_project must be"),
    ("55.4 a location with a slash", dict(provider="vertex", project="hyphn-studio-1013", location="global/x"), "vertex_location must be"),
    ("55.4 a location that is a multi-region name", dict(provider="vertex", project="hyphn-studio-1013", location="us"), "vertex_location must be"),
    ("55.4 an empty location", dict(provider="vertex", project="hyphn-studio-1013", location=""), "vertex_location must be"),
    ("55.5 vertex, gemini-4-argon, thinking minimal", dict(provider="vertex", project="hyphn-studio-1013", thinking="minimal"), "does not take thinking_level minimal"),
    ("55.16 a model with a slash", dict(model="gemini/../x"), "model must be"),
    ("55.16 a model with a query", dict(model="gemini-4-argon?alt=sse"), "model must be"),
    ("55.16 a model with a fragment", dict(model="gemini#x"), "model must be"),
    ("55.16 a model with a space", dict(model="gemini 4"), "model must be"),
    ("55.16 a model with a newline", dict(model="gemini\napi_url=https://evil"), "model must be"),
    ("55.16 a vertex model with a slash", dict(provider="vertex", project="hyphn-studio-1013", model="a/b"), "model must be"),
]
for label, kw, message in REFUSE:
    p, url = run(**kw)
    check(f"{label}: fails, says why, writes no URL", p.returncode != 0 and message in p.stdout and url is None,
          (p.returncode, p.stdout[-200:], url))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
