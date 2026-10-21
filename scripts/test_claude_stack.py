#!/usr/bin/env python3
"""Cases for the stack check in claude-review.yml (#58). Run: python3 scripts/test_claude_stack.py

The stack input names a file, prompts/<stack>.md, so only a plain prompt name
may pass. Runs the real "Check the stack" step, cut out of the workflow, in
bash with the input as an environment variable, then the real "Compose
prompt" step for a valid name that has no file. Standard library only.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
WF = REPO / ".github/workflows"
LINES = (WF / "claude-review.yml").read_text().splitlines()

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def step(lines, name):
    start = lines.index(f"      - name: {name}")
    stop = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("      - name:")), len(lines))
    return start, lines[start:stop]


def script(body):
    at = body.index("        run: |")
    return "\n".join(l[10:] for l in body[at + 1:]) + "\n"


START, STEP = step(LINES, "Check the stack")
CHECK = script(STEP)


def run(stack):
    return subprocess.run(["bash", "-e", "-c", CHECK], env=dict(os.environ, STACK=stack), capture_output=True, text=True)


print("where the step runs")
first = next(i for i, l in enumerate(LINES) if l.startswith("      - name:"))
check("58.C9 it is the job's first step, before any checkout", START == first, (START, first))
check("58.C9 it has no if:, so every run checks", not any(l.strip().startswith("if:") for l in STEP), STEP[:4])
check("58.C9 it reads the stack only through env", "          STACK: ${{ inputs.stack }}" in STEP
      and "inputs.stack" not in CHECK, STEP)

print("names that pass")
for name in ("python", "nestjs-fastify-drizzle", "nope"):
    p = run(name)
    check(f"58.C6/C7 stack {name!r} passes", p.returncode == 0 and not p.stdout, (p.returncode, p.stdout))
prompts = sorted(f.stem for f in (REPO / "prompts").glob("*.md") if f.stem != "base" and not f.stem.startswith("_"))
locked = [n for n in prompts if run(n).returncode != 0]
check("58.C10 every shipped stack prompt passes", prompts and not locked, (prompts, locked))

print("names that are refused")
for name in ("../../README", "python/../base", "", "Python", "_gemini-tail", "_claude-tail", "python\nx", "py thon", "python.md", "base/"):
    p = run(name)
    check(f"58.C8 stack {name!r}: fails and says why", p.returncode != 0 and "::error::stack must be a prompt name" in p.stdout,
          (p.returncode, p.stdout[-200:]))

print("the same rule as Gemini")
gem = script(step((WF / "gemini-review.yml").read_text().splitlines(), "Check the provider")[1])
rule = [l for l in CHECK.splitlines() if "STACK" in l]
check("58.C11 the pattern and the error line are the Gemini check's, word for word",
      len(rule) == 2 and all(l in gem.splitlines() for l in rule), rule)

print("a valid name with no file")
work = pathlib.Path(tempfile.mkdtemp())
try:
    root = work / ".claude-review-action/prompts"
    root.mkdir(parents=True)
    for f in ("base.md", "_claude-tail.md"):
        shutil.copy(REPO / "prompts" / f, root / f)
    compose = script(step(LINES, "Compose prompt")[1]).replace("/tmp/prompt.md", str(work / "prompt.md"))
    env = dict(os.environ, STACK="nope", EXTRA_PROMPT="", EXTRA_PROMPT_PATH="", REPO_FULL_NAME="o/r", GH_TOKEN="x",
               PR_NUMBER="1", RUNNER_TEMP=str(work), GITHUB_OUTPUT=str(work / "out"))
    p = subprocess.run(["bash", "-c", compose], cwd=work, env=env, capture_output=True, text=True)
    check("58.C7 Compose prompt still fails for it, saying not found", p.returncode != 0 and "stack prompt 'nope' not found" in p.stdout,
          (p.returncode, p.stdout[-200:]))
finally:
    shutil.rmtree(work)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
