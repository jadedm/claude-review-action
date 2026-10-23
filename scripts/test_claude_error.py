#!/usr/bin/env python3
"""Cases for saying why the Claude review failed (#84). Run: python3 scripts/test_claude_error.py

Runs the real "Say why Claude failed" step of claude-review.yml in bash
against fixture execution files and reads what it prints. Standard library only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github/workflows/claude-review.yml"
TITLE = "::error title=Claude review failed::"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


lines = WORKFLOW.read_text().splitlines()
start = lines.index("      - name: Say why Claude failed")
end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("      - name:"))
STEP = lines[start:end]
run_at = STEP.index("        run: |")
SCRIPT = "\n".join(l[10:] for l in STEP[run_at + 1:]) + "\n"
IF = next(l.strip() for l in STEP if l.strip().startswith("if:"))

ERROR = [
    {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"},
    {"type": "result", "subtype": "success", "is_error": True, "duration_ms": 842, "num_turns": 1,
     "result": "Invalid bearer token\nPlease run /login", "total_cost_usd": 0},
]


def run(content=None, named=True):
    """content: a JSON value to write, a str to write raw, or None for no file."""
    work = pathlib.Path(tempfile.mkdtemp())
    path = work / "claude-execution-output.json"
    if isinstance(content, str):
        path.write_text(content)
    elif content is not None:
        path.write_text(json.dumps(content))
    env = dict(os.environ, RUNNER_TEMP=str(work), EXECUTION_FILE=str(path) if named else "")
    p = subprocess.run(["bash", "-e", "-c", SCRIPT], cwd=work, env=env, capture_output=True, text=True)
    shutil.rmtree(work)
    return p


print("failure reason")
p = run(ERROR)
check("84.1 an error result: one annotation with the subtype and the message, newlines folded, exit 0",
      p.returncode == 0 and p.stdout == f"{TITLE}success: Invalid bearer token Please run /login\n", (p.stdout, p.stderr))
p = run(ERROR, named=False)
check("84.2 no execution_file output: falls back to the file in RUNNER_TEMP",
      p.returncode == 0 and "Invalid bearer token" in p.stdout, (p.stdout, p.stderr))
p = run(None)
check("84.3 no file: says the action did not write it, exit 0",
      p.returncode == 0 and p.stdout.startswith(TITLE) and "stopped before writing it, or could not write it" in p.stdout, (p.stdout, p.stderr))
p = run("{not json")
check("84.4 a file that is not JSON: same message, exit 0",
      p.returncode == 0 and "No readable execution file" in p.stdout, (p.stdout, p.stderr))
p = run([{"type": "system", "subtype": "init"}])
check("84.5 no result message: says so, exit 0",
      p.returncode == 0 and "no result message" in p.stdout, (p.stdout, p.stderr))
p = run([{"type": "result", "subtype": "error_during_execution", "is_error": True}])
check("84.6 a result with no text and no errors: names the subtype",
      p.returncode == 0 and p.stdout == f"{TITLE}error_during_execution: no message\n", p.stdout)
p = run([{"type": "result", "subtype": "error_max_turns", "is_error": True, "errors": ["Reached maximum turns", "stopped"]}])
check("84.14 an error subtype carries its cause in errors: printed",
      p.returncode == 0 and p.stdout == f"{TITLE}error_max_turns: Reached maximum turns stopped\n", p.stdout)
p = run([{"type": "result", "subtype": "success", "result": "bad\r\n::set-output name=x::y 100%"}])
check("84.7 text carrying a workflow command prints one escaped line, no second command",
      p.stdout.count("\n") == 1 and p.stdout.count("::error") == 1 and "100%25" in p.stdout
      and "\n::set-output" not in p.stdout, repr(p.stdout))
p = run([{"type": "result", "subtype": "success", "result": "x" * 2000}])
check("84.8 long text is cut at 500 characters",
      p.returncode == 0 and "x" * 500 + " (cut)" in p.stdout and "x" * 501 not in p.stdout, len(p.stdout))
p = run([{"type": "result", "subtype": "success", "result": "first"}, {"type": "assistant"},
         {"type": "result", "subtype": "error_max_turns", "result": "last"}])
check("84.9 several result messages: the last one", "error_max_turns: last" in p.stdout and "first" not in p.stdout, p.stdout)
p = run({"type": "result", "result": "not a list"})
check("84.10 a file that is not a list: says no result message", "no result message" in p.stdout, p.stdout)
p = run([{"type": "tool_result", "content": "SECRET-TOOL-OUTPUT"}, ERROR[1]])
check("84.11 prints nothing from the file but the result message", "SECRET-TOOL-OUTPUT" not in p.stdout + p.stderr, p.stdout)
TOKENS = {"gh": "ghp_" + "a" * 36, "pat": "github_pat_" + "B" * 30, "ant": "sk-ant-oat01-" + "c" * 30,
          "aws": "AKIA" + "D" * 16, "slack": "xoxb-" + "1" * 12, "jwt": "eyJ" + "e" * 12 + ".eyJ" + "f" * 12 + "." + "g" * 12}
p = run([{"type": "result", "subtype": "success", "is_error": True, "result": "leaked " + " ".join(TOKENS.values()) + " end"}])
check("84.15 token shapes in the text are redacted before printing",
      p.returncode == 0 and not any(v in p.stdout for v in TOKENS.values()) and p.stdout.count("[redacted]") == len(TOKENS)
      and "leaked" in p.stdout and "end" in p.stdout, p.stdout)
AWS = "AKIA" + "H" * 16
p = run([{"type": "result", "subtype": "success", "result": "a \x1b[31m" + AWS + " b \\n" + AWS + " c " + "github_pat_" + "J" * 12}])
check("84.16 an AWS key after a colour code or an escaped newline, and a short fine-grained token fragment, are redacted",
      AWS not in p.stdout and "J" * 12 not in p.stdout and p.stdout.count("[redacted]") == 3, repr(p.stdout))
p = run([{"type": "result", "subtype": "error_during_execution", "errors": "one plain string"}])
check("84.17 errors as a plain string is printed, not dropped",
      p.stdout == f"{TITLE}error_during_execution: one plain string\n", p.stdout)
check("84.12 the step runs only when the review step failed",
      IF == "if: failure() && steps.claude-review.outcome == 'failure'", IF)
run_step = WORKFLOW.read_text().split("      - name: Run Claude review", 1)[1].split("\n      - name:", 1)[0]
check("84.13 show_full_output stays off", "show_full_output" not in run_step, "show_full_output found")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
