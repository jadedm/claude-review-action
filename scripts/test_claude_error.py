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

NOCALL = {"total_cost_usd": 0, "modelUsage": {}, "usage": {"input_tokens": 0, "output_tokens": 0}}
RAN = {"total_cost_usd": 0.42, "modelUsage": {"claude-sonnet-5-5": {"outputTokens": 900}}, "usage": {"output_tokens": 900}}


def result(**fields):
    """A result message; pass NOCALL or RAN fields to say whether the model was called."""
    return {"type": "result", **fields}


ERROR = [
    {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"},
    result(subtype="success", is_error=True, duration_ms=842, num_turns=1,
           result="Invalid bearer token\nPlease run /login", **NOCALL),
]


def run(content=None, named=True, path=None):
    """content: a JSON value to write, a str to write raw, or None for no file. path: EXECUTION_FILE as given."""
    work = pathlib.Path(tempfile.mkdtemp())
    file = work / "claude-execution-output.json"
    if isinstance(content, str):
        file.write_text(content)
    elif content is not None:
        file.write_text(json.dumps(content))
    given = path if path is not None else (str(file) if named else "")
    env = dict(os.environ, RUNNER_TEMP=str(work), EXECUTION_FILE=given)
    p = subprocess.run(["bash", "-e", "-c", SCRIPT], cwd=work, env=env, capture_output=True, text=True)
    shutil.rmtree(work)
    return p


print("failure reason")
p = run(ERROR)
check("84.1 an error before any model call: the subtype, turns and Claude Code's message, folded to one line, exit 0",
      p.returncode == 0 and p.stdout == f"{TITLE}success, 1 turns, before any model call: Invalid bearer token Please run /login\n",
      (p.stdout, p.stderr))
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
p = run([result(subtype="error_during_execution", is_error=True, **NOCALL)])
check("84.6 an error before any model call with no text and no errors: says no message",
      p.returncode == 0 and p.stdout == f"{TITLE}error_during_execution, unknown turns, before any model call: no message\n", p.stdout)
p = run([result(subtype="error_max_turns", is_error=True, num_turns=30, result="MODEL-WORDS",
                errors=["Reached maximum turns", "stopped"], **RAN)])
check("84.14 an error after the model ran: subtype and turns only, neither the result nor the errors list",
      p.returncode == 0 and "MODEL-WORDS" not in p.stdout and "Reached maximum turns" not in p.stdout
      and p.stdout == f"{TITLE}error_max_turns, 30 turns, after the model ran. Neither the model's text nor the error text is printed here; the action's own lines above give its errors.\n",
      p.stdout)
p = run([result(subtype="success", is_error=False, num_turns=12, result="MODEL-REVIEW-TEXT ghs_" + "q" * 36, **RAN)])
check("84.18 Claude finished and a later step of the action failed: the result is not printed",
      p.returncode == 0 and "MODEL-REVIEW-TEXT" not in p.stdout and "ghs_" not in p.stdout
      and p.stdout == f"{TITLE}success, 12 turns: Claude finished, and a later step of claude-code-action failed; its lines above say why.\n",
      p.stdout)
p = run([result(subtype="success", is_error=True, num_turns=7, result="MODEL-WORDS", **RAN)])
check("84.19 an error after the model ran with a result and no errors: the result is not printed",
      p.returncode == 0 and "MODEL-WORDS" not in p.stdout and "after the model ran" in p.stdout, p.stdout)
for label, fields in (("no cost or usage fields", {}), ("cost 0 but no modelUsage", {"total_cost_usd": 0}),
                      ("cost 0, empty modelUsage, output tokens", {"total_cost_usd": 0, "modelUsage": {}, "usage": {"output_tokens": 5}}),
                      ("cost given as a string", {"total_cost_usd": "0", "modelUsage": {}})):
    p = run([result(subtype="success", is_error=True, result="MODEL-WORDS", **fields)])
    check(f"84.20 fails closed, {label}: treated as after the model ran, the result is not printed",
          p.returncode == 0 and "MODEL-WORDS" not in p.stdout and "after the model ran" in p.stdout, p.stdout)
for label, fields in (("only a nonzero cost", {"total_cost_usd": 0.01, "modelUsage": {}, "usage": {"output_tokens": 0}}),
                      ("only a non-empty modelUsage", {"total_cost_usd": 0, "modelUsage": {"m": {}}, "usage": {"output_tokens": 0}})):
    p = run([result(subtype="success", is_error=True, result="MODEL-WORDS", **fields)])
    check(f"84.26 {label} counts as after the model ran: the result is not printed",
          p.returncode == 0 and "MODEL-WORDS" not in p.stdout and "after the model ran" in p.stdout, p.stdout)
p = run([result(subtype="success", is_error=True, result="bad\r\n::set-output name=x::y 100%", **NOCALL)])
check("84.7 text carrying a workflow command prints one escaped line, no second command",
      p.stdout.count("\n") == 1 and p.stdout.count("::error") == 1 and "100%25" in p.stdout
      and "\n::set-output" not in p.stdout, repr(p.stdout))
p = run(None, path="/nonexistent/a\n::warning::injected\rb")
check("84.21 say() escapes CR and LF itself: a path carrying them stays on one line",
      p.stdout.count("\n") == 1 and "%0A::warning::injected%0Db" in p.stdout and "\n::warning" not in p.stdout, repr(p.stdout))
p = run([result(subtype="success", is_error=True, result="x" * 2000, **NOCALL)])
check("84.8 long text is cut at 500 characters",
      p.returncode == 0 and "x" * 500 + " (cut)" in p.stdout and "x" * 501 not in p.stdout, len(p.stdout))
p = run([result(subtype="success", is_error=True, result="x" * 480 + " ghp_" + "a" * 36 + " tail", **NOCALL)])
check("84.22 a token across the 500-character cut is redacted first, no part of it printed",
      p.returncode == 0 and "ghp_" not in p.stdout and "aaaa" not in p.stdout and "[redacted]" in p.stdout, p.stdout[-80:])
p = run([result(subtype="success", is_error=True, result="first", **NOCALL), {"type": "system", "subtype": "status"},
         result(subtype="error_max_turns", is_error=True, result="last", **NOCALL)])
check("84.9 several result messages: the last one", "error_max_turns" in p.stdout and ": last" in p.stdout and "first" not in p.stdout, p.stdout)
p = run({"type": "result", "result": "not a list"})
check("84.10 a file that is not a list: says no result message", "no result message" in p.stdout, p.stdout)
p = run([{"type": "tool_result", "content": "SECRET-TOOL-OUTPUT"}, ERROR[1]])
check("84.11 prints nothing from the file but the result message", "SECRET-TOOL-OUTPUT" not in p.stdout + p.stderr, p.stdout)
TOKENS = {"gh": "ghp_" + "a" * 36, "pat": "github_pat_" + "B" * 30, "ant": "sk-ant-oat01-" + "c" * 30,
          "aws": "AKIA" + "D" * 16, "slack": "xoxb-" + "1" * 12, "jwt": "eyJ" + "e" * 12 + ".eyJ" + "f" * 12 + "." + "g" * 12}
p = run([result(subtype="success", is_error=True, result="leaked " + " ".join(TOKENS.values()) + " end", **NOCALL)])
check("84.15 token shapes in the text are redacted before printing",
      p.returncode == 0 and not any(v in p.stdout for v in TOKENS.values()) and p.stdout.count("[redacted]") == len(TOKENS)
      and "leaked" in p.stdout and "end" in p.stdout, p.stdout)
AWS = "AKIA" + "H" * 16
p = run([result(subtype="success", is_error=True, result="a \x1b[31m" + AWS + " b \\n" + AWS + " c " + "github_pat_" + "J" * 12, **NOCALL)])
check("84.16 an AWS key after a colour code or an escaped newline, and a short fine-grained token fragment, are redacted",
      AWS not in p.stdout and "J" * 12 not in p.stdout and p.stdout.count("[redacted]") == 3, repr(p.stdout))
p = run([result(subtype="error_during_execution", is_error=True, errors="one plain string", **NOCALL)])
check("84.17 before any model call, errors as a plain string is printed, not dropped",
      "before any model call: one plain string" in p.stdout, p.stdout)
p = run([result(subtype="error_during_execution", is_error=True, errors=["MCP tool failed: PR-STEERED-NAME"], **RAN)])
check("84.24 after the model ran, an errors list that may carry tool diagnostics is not printed",
      p.returncode == 0 and "PR-STEERED-NAME" not in p.stdout and "after the model ran" in p.stdout, p.stdout)
for kind in ("assistant", "user"):
    p = run([{"type": "system", "subtype": "init"}, {"type": kind, "message": {"content": "MODEL-ACTIVITY"}},
             result(subtype="error_during_execution", is_error=True, result="CRASH-TEXT", errors=["CRASH-ERR"], **NOCALL)])
    check(f"84.25 a zeroed final result after an {kind} message counts as after the model ran: nothing from the result printed",
          p.returncode == 0 and "CRASH-TEXT" not in p.stdout and "CRASH-ERR" not in p.stdout and "after the model ran" in p.stdout, p.stdout)
p = run([result(subtype="x\n::warning::y" * 20, is_error=True, **NOCALL)])
check("84.23 a long or multi-line subtype is folded and capped at 60 characters",
      p.stdout.count("\n") == 1 and len(p.stdout.split("::", 2)[2].split(",")[0]) <= 60, repr(p.stdout[:200]))
check("84.12 the step runs only when the review step failed",
      IF == "if: failure() && steps.claude-review.outcome == 'failure'", IF)
run_step = WORKFLOW.read_text().split("      - name: Run Claude review", 1)[1].split("\n      - name:", 1)[0]
check("84.13 show_full_output stays off", "show_full_output" not in run_step, "show_full_output found")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
