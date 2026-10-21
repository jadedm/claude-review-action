#!/usr/bin/env python3
"""Cases for the inline comment instructions in the review prompt (#69, #75).

Run: python3 scripts/test_inline_prompt.py

claude-code-action's tag-mode prompt tells Claude to communicate only by
editing its one summary comment, and our prompt is appended after it. Unless
our prompt names the inline comment tool and says it is allowed, Claude puts
every finding in the summary. The instructions live in prompts/_claude-tail.md,
which only the Claude workflow appends, because Gemini reads base.md too and
has no such tool. These cases check the prompt text and that the real
"Compose prompt" step of claude-review.yml carries it, last. Whether Claude
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
TAIL = (REPO / "prompts/_claude-tail.md").read_text()
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


print("_claude-tail.md inline comment section")
check("1. names the inline comment tool", TOOL in TAIL, "tool name missing from prompts/_claude-tail.md")

allowed = re.search(r'--allowedTools "([^"]*)"', CLAUDE_WF.read_text())
allowed_list = allowed.group(1).split(",") if allowed else []
named = set(re.findall(r"mcp__github_inline_comment__[a-z_]+", TAIL))
check("2. every inline tool the tail names is on claude-review.yml's --allowedTools",
      bool(named) and named <= set(allowed_list), f"named {sorted(named)}, allowed {allowed_list}")

check("3. says to load the tool with ToolSearch",
      f"select:{TOOL}" in TAIL, "no ToolSearch select line for the tool")

check("4. names the summary tool and says inline comments are allowed despite it",
      SUMMARY_TOOL in TAIL and re.search(r"inline comments[^.]*\b(are|is) (allowed|expected)", TAIL, re.I) is not None,
      "the tail does not reconcile with the single-comment rule")

check("5. asks for confirmed: true", "`confirmed: true`" in TAIL, "confirmed: true missing")

check("8. the old unnamed line is gone",
      "- Use inline review comments for specific code issues." not in BASE, "old line still present")

# Gemini reads base.md too, and its tail wants findings in "issues", with
# "summary" a short overview. Only Claude's tail may talk about the tool or a
# summary comment.
check("9. base.md names no inline comment tool and no summary comment",
      "mcp__" not in BASE and "summary" not in BASE.lower() and "inline" not in BASE.lower(),
      [l for l in BASE.splitlines() if "summary" in l.lower() or "mcp__" in l or "inline" in l.lower()])

print("Compose prompt step of claude-review.yml")
work = pathlib.Path(tempfile.mkdtemp())
try:
    root = work / ".claude-review-action"
    (root / "prompts").mkdir(parents=True)
    shutil.copy(REPO / "prompts/base.md", root / "prompts/base.md")
    shutil.copy(REPO / "prompts/python.md", root / "prompts/python.md")
    shutil.copy(REPO / "prompts/_claude-tail.md", root / "prompts/_claude-tail.md")
    out = work / "github_output"
    script = step_script(CLAUDE_WF, "Compose prompt").replace("/tmp/prompt.md", str(work / "prompt.md"))

    def compose(extra=""):
        out.write_text("")
        env = dict(os.environ, STACK="python", EXTRA_PROMPT=extra, EXTRA_PROMPT_PATH="",
                   REPO_FULL_NAME="o/r", GH_TOKEN="x", PR_NUMBER="1",
                   RUNNER_TEMP=str(work), GITHUB_OUTPUT=str(out))
        r = subprocess.run(["bash", "-c", script], cwd=work, env=env, capture_output=True, text=True)
        return r, out.read_text()

    r, prompt = compose("REPO NOTE LINE")
    check("6. step exits 0", r.returncode == 0, r.stderr)
    check("6. PROMPT output carries the tool name and confirmed: true",
          prompt.startswith("PROMPT<<") and TOOL in prompt and "`confirmed: true`" in prompt, prompt[:300])
    stack_first = (REPO / "prompts/python.md").read_text().splitlines()[0]
    at = {k: prompt.find(v) for k, v in (("base", BASE.splitlines()[0]), ("stack", stack_first),
                                          ("notes", "REPO NOTE LINE"), ("tail", TAIL.splitlines()[0]))}
    check("10. the tail comes last: after base.md, the stack prompt and the repo notes",
          0 <= at["base"] < at["stack"] < at["notes"] < at["tail"], at)
    (root / "prompts/_claude-tail.md").unlink()
    r, prompt = compose()
    check("11. with the tail missing (a ref from before it), the step warns and composes the prompt without it",
          r.returncode == 0 and "::warning::Claude prompt tail not found" in r.stdout and prompt.startswith("PROMPT<<")
          and BASE.splitlines()[0] in prompt and TOOL not in prompt, (r.returncode, r.stdout[-200:]))
finally:
    shutil.rmtree(work)

print("Gemini prompt order")
for name in ("gemini-review.yml", "gemini-review-vertex.yml"):
    text = (WF / name).read_text()
    base_at = text.find('cat "$BASE"')
    tail_at = text.find("prompts/_gemini-tail.md\"")
    check(f"7. {name} reads the JSON tail after base.md", 0 <= base_at < tail_at, (base_at, tail_at))
    check(f"12. {name} does not read Claude's tail", "_claude-tail" not in text)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
