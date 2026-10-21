#!/usr/bin/env python3
"""Cases for the inline comment instructions in the review prompt (#69).

Run: python3 scripts/test_inline_prompt.py

claude-code-action's tag-mode prompt tells Claude to communicate only by
editing its one summary comment, and our prompt is appended after it. Unless
our prompt names the inline comment tool and says it is allowed, Claude puts
every finding in the summary. These cases check the prompt text and that the
real "Compose prompt" step of claude-review.yml carries it. Whether Claude
then calls the tool shows only in a live run. Standard library only.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
WF = REPO / ".github/workflows"
CLAUDE_WF = WF / "claude-review.yml"
BASE = (REPO / "prompts/base.md").read_text()
TOOL = "mcp__github_inline_comment__create_inline_comment"
SUMMARY_TOOL = "mcp__github_comment__update_claude_comment"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def step_script(workflow, name):
    """The run: block of the named step, dedented."""
    lines = workflow.read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("      - name:")), len(lines))
    step = lines[start:end]
    run_at = step.index("        run: |")
    return "\n".join(l[10:] for l in step[run_at + 1:]) + "\n"


print("base.md inline comment section")
check("1. names the inline comment tool", TOOL in BASE, "tool name missing from prompts/base.md")

allowed = re.search(r'--allowedTools "([^"]*)"', CLAUDE_WF.read_text())
allowed_list = allowed.group(1).split(",") if allowed else []
named = set(re.findall(r"mcp__github_inline_comment__[a-z_]+", BASE))
check("2. every inline tool base.md names is on claude-review.yml's --allowedTools",
      bool(named) and named <= set(allowed_list), f"named {sorted(named)}, allowed {allowed_list}")

check("3. says to load the tool with ToolSearch",
      f"select:{TOOL}" in BASE, "no ToolSearch select line for the tool")

check("4. names the summary tool and says inline comments are allowed despite it",
      SUMMARY_TOOL in BASE and re.search(r"inline comments[^.]*\b(are|is) (allowed|expected)", BASE, re.I) is not None,
      "base.md does not reconcile with the single-comment rule")

check("5. asks for confirmed: true", "`confirmed: true`" in BASE, "confirmed: true missing")

check("8. the old unnamed line is gone",
      "- Use inline review comments for specific code issues." not in BASE, "old line still present")

# Gemini reads base.md too, and its tail wants findings in "issues", with
# "summary" a short overview. Only the section for the reviewer with the tool
# may talk about a summary comment.
section = re.search(r"^### Inline comments \(when you have .*?(?=^## )", BASE, re.M | re.S)
outside = BASE.replace(section.group(0), "") if section else BASE
check("9. outside the tool section, base.md does not tell reviewers to use a summary",
      section is not None and "summary" not in outside.lower(),
      [l for l in outside.splitlines() if "summary" in l.lower()] or "section heading not found")

print("Compose prompt step of claude-review.yml")
work = pathlib.Path(tempfile.mkdtemp())
try:
    root = work / ".claude-review-action"
    (root / "prompts").mkdir(parents=True)
    shutil.copy(REPO / "prompts/base.md", root / "prompts/base.md")
    shutil.copy(REPO / "prompts/python.md", root / "prompts/python.md")
    out = work / "github_output"
    out.write_text("")
    script = step_script(CLAUDE_WF, "Compose prompt").replace("/tmp/prompt.md", str(work / "prompt.md"))
    env = dict(os.environ, STACK="python", EXTRA_PROMPT="", EXTRA_PROMPT_PATH="",
               REPO_FULL_NAME="o/r", GH_TOKEN="x", PR_NUMBER="1",
               RUNNER_TEMP=str(work), GITHUB_OUTPUT=str(out))
    r = subprocess.run(["bash", "-c", script], cwd=work, env=env, capture_output=True, text=True)
    prompt = out.read_text()
    check("6. step exits 0", r.returncode == 0, r.stderr)
    check("6. PROMPT output carries the tool name and confirmed: true",
          prompt.startswith("PROMPT<<") and TOOL in prompt and "`confirmed: true`" in prompt, prompt[:300])
finally:
    shutil.rmtree(work)

print("Gemini prompt order")
for name in ("gemini-review.yml", "gemini-review-vertex.yml"):
    text = (WF / name).read_text()
    base_at = text.find('cat "$BASE"')
    tail_at = text.find("prompts/_gemini-tail.md\"")
    check(f"7. {name} reads the JSON tail after base.md", 0 <= base_at < tail_at, (base_at, tail_at))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
