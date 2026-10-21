#!/usr/bin/env python3
"""Cases for the README and community files against the workflows (#48).

Run: python3 scripts/test_docs.py

The README quotes inputs, defaults, secrets, permissions, stacks and line
numbers from the workflows. Each of those is read back from the files here,
so a change to a workflow that leaves the README behind turns this red.
Standard library only.
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
WF = REPO / ".github/workflows"
README = (REPO / "README.md").read_text()
PROSE = [REPO / p for p in ("README.md", "CONTRIBUTING.md", "SECURITY.md", ".github/pull_request_template.md")]
PROSE += sorted((REPO / ".github/ISSUE_TEMPLATE").glob("*.yml"))

passed = failed = 0
TYPES, README_TYPES = {}, {}


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def workflow_call(name):
    """{input: default or 'required'}, [secrets] from a workflow's workflow_call block."""
    lines = (WF / name).read_text().split("\njobs:", 1)[0].splitlines()
    inputs, secrets, section, current = {}, [], None, None
    types = {}
    for line in lines:
        if line in ("    inputs:", "    secrets:"):
            section = line.strip(" :")
            continue
        m = re.match(r"^      ([a-z_]+):$", line)
        if m and section == "inputs":
            current = m.group(1)
            inputs[current] = None
        if m and section == "secrets":
            secrets.append(m.group(1))
        d = re.match(r"^        default: (.*)$", line)
        if d and section == "inputs":
            inputs[current] = d.group(1).strip("'")
        if line.strip() == "required: true" and section == "inputs":
            inputs[current] = "required"
        t = re.match(r"^        type: (.*)$", line)
        if t and section == "inputs":
            types[current] = t.group(1)
    TYPES[name] = types
    return inputs, secrets


def readme_table(heading):
    """{input: default} from the README table under a heading."""
    block = README.split(f"### {heading}\n", 1)[1].split("\n\n#", 1)[0]
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("`"):
            continue
        rows[cells[0].strip("`")] = cells[2].strip("`").replace("''", "").strip('"')
        README_TYPES[cells[0].strip("`")] = cells[1].split(" ")[0]
    return rows


def norm(default):
    return (default or "").strip('"')


print("inputs and secrets")
claude_in, claude_sec = workflow_call("claude-review.yml")
gemini_in, gemini_sec = workflow_call("gemini-review.yml")
both, gemini_only = readme_table("Both workflows"), readme_table("Gemini only")
check("R2 the 'Both workflows' table lists exactly the Claude inputs", set(both) == set(claude_in), (sorted(both), sorted(claude_in)))
check("R2 'Both' plus 'Gemini only' lists exactly the Gemini inputs", set(both) | set(gemini_only) == set(gemini_in),
      (sorted(set(both) | set(gemini_only)), sorted(gemini_in)))
wrong = [(k, v, norm(gemini_in.get(k))) for k, v in {**both, **gemini_only}.items() if v != norm(gemini_in.get(k))]
wrong += [(k, v, norm(claude_in.get(k))) for k, v in both.items() if v != norm(claude_in.get(k))]
check("R2 every default in the README equals the workflow's", not wrong, wrong)
mistyped = [(k, t, TYPES["gemini-review.yml"].get(k)) for k, t in README_TYPES.items() if t != TYPES["gemini-review.yml"].get(k)]
mistyped += [(k, README_TYPES[k], t) for k, t in TYPES["claude-review.yml"].items() if README_TYPES.get(k) != t]
check("R2 every type in the README equals the workflow's", not mistyped, mistyped)
vertex_in, vertex_sec = workflow_call("gemini-review-vertex.yml")
vertex_only = readme_table("`gemini-review-vertex.yml` only")
check("B12 the Vertex table lists exactly the inputs gemini-review-vertex.yml adds", set(vertex_only) == set(vertex_in) - set(gemini_in),
      (sorted(vertex_only), sorted(set(vertex_in) - set(gemini_in))))
check("B12 the Vertex workflow takes the Gemini inputs except ref, as the README says",
      set(vertex_in) - set(vertex_only) == set(gemini_in) - {"ref"}, sorted(set(vertex_in) ^ set(gemini_in)))
check("B12 its only secret is the Slack webhook, and vertex_project is required there",
      vertex_sec == ["slack_webhook_url"] and vertex_in.get("vertex_project") == "required", (vertex_sec, vertex_in.get("vertex_project")))
named = set(re.findall(r"\| `([a-z_]+)`(?:, optional)? \|", README.split("## Secrets", 1)[1].split("\n## ", 1)[0]))
check("R2 the secrets table names exactly the workflows' secrets", named == set(claude_sec) | set(gemini_sec), (named, claude_sec, gemini_sec))


def job_permissions(name):
    """The first permissions block after workflow_call, at workflow or job level."""
    lines = (WF / name).read_text().split("\n    secrets:", 1)[1].splitlines()
    at = next(i for i, l in enumerate(lines) if l.strip() == "permissions:")
    indent = len(lines[at]) - len(lines[at].lstrip()) + 2
    out = []
    for l in lines[at + 1:]:
        if len(l) - len(l.lstrip()) != indent or not l.strip():
            break
        out.append(l.strip())
    return out


def readme_permissions(caller):
    block = README.split(f"`.github/workflows/{caller}`:", 1)[1].split("   jobs:", 1)[0]
    body = block.split("   permissions:\n", 1)[1]
    return [l.strip() for l in body.splitlines() if l.startswith("     ") and ":" in l]


for caller, wf in (("claude.yml", "claude-review.yml"), ("gemini.yml", "gemini-review.yml")):
    want = [p for p in job_permissions(wf) if re.match(r"^[a-z-]+: (read|write)$", p)]
    check(f"R2 the quickstart {caller} grants the permissions {wf}'s job asks for", readme_permissions(caller) == want,
          (readme_permissions(caller), want))
    uses = f"uses: jadedm/pr-review-workflows/.github/workflows/{wf}@v1"
    check(f"R2 the quickstart {caller} calls {wf}", uses in README, uses)

stacks = sorted(p.stem for p in (REPO / "prompts").glob("*.md") if p.stem not in ("base",) and not p.stem.startswith("_"))
listed = sorted(re.findall(r"`([a-z0-9-]+)`", README.split("`stack` picks a prompt", 1)[1].split("\n", 1)[0]))
check("R2 the quickstart lists every stack prompt and no others", [s for s in listed if s != "stack"] == stacks, (listed, stacks))

print("line references")
# The exact citation in the README, the file, the lines, and text that must be on them.
CITES = [
    ("`claude-review.yml:139`", "claude-review.yml", 139, 139, "scripts/base_file.py"),
    ("`gemini-review.yml:336`", "gemini-review.yml", 336, 336, "scripts/base_file.py"),
    ("`claude-review.yml:64`", "claude-review.yml", 64, 64, "@3d3c42e5aac5ba805825da76410c181273ba90b1"),
    ("`:80`", "claude-review.yml", 80, 80, "@3d3c42e5aac5ba805825da76410c181273ba90b1"),
    ("`:171`", "claude-review.yml", 171, 171, "anthropics/claude-code-action@374f254f5efaf968abfe15d399fd90972e8138b9"),
    ("lines 32 to 66", "../../scripts/gemini_review_comment.py", 32, 66, 'text.replace("@", "@" + ZWSP)'),
    ("`gemini_review_comment.py:91-127`", "../../scripts/gemini_review_comment.py", 91, 127, '"### Not reviewed"'),
    ("`claude-review.yml:254`", "claude-review.yml", 254, 254, "Say when Claude did not review"),
    ("`claude-review.yml:218-220`", "claude-review.yml", 218, 220, "--disallowedTools \"Edit,MultiEdit,Write"),
    ("`claude-review.yml:42-50`", "claude-review.yml", 42, 50, '["OWNER","MEMBER","COLLABORATOR"]'),
    ("`gemini-review.yml:85-94`", "gemini-review.yml", 85, 94, '["OWNER","MEMBER","COLLABORATOR"]'),
]
for cite, name, start, end, needle in CITES:
    lines = (WF / name).resolve().read_text().splitlines()[start - 1:end]
    check(f"R1 {cite} is in the README and its lines hold {needle[:40]}", cite in README and any(needle in l for l in lines),
          (cite in README, lines[:3]))
for cite in re.findall(r"`[a-z_-]+\.(?:yml|py):\d+(?:-\d+)?`", README):
    check(f"R1 {cite} has a case here", any(c[0] == cite for c in CITES), cite)
check("R4 the Gemini default named in the README is the workflow's", "`gemini-4-argon` with thinking `high`" in README
      and gemini_in["model"] == "gemini-4-argon" and gemini_in["thinking_level"] == "high", (gemini_in["model"], gemini_in["thinking_level"]))

print("links and writing")
for doc in PROSE:
    text = doc.read_text()
    for target in re.findall(r"\]\((?!https?://|#)([^)]+)\)", text):
        check(f"R7 {doc.relative_to(REPO)} links to {target}, which exists", (doc.parent / target).exists(), target)
    dashes = len(re.findall("[–—]", text))
    emoji = len(re.findall("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]", text))
    check(f"R6 {doc.relative_to(REPO)}: 0 em or en dashes, 0 emojis", dashes == 0 and emoji == 0, (dashes, emoji))
stale = [s for s in ("Not community-supported", "Tikiti", "Gemini 2.5", "claude-review-action/.github") if s in README]
check("R6 the README drops the old framing and names", not stale, stale)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
