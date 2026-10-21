#!/usr/bin/env python3
"""Cases for what each review job's credentials can do (#66). Run: python3 scripts/test_credentials.py

Reads claude-review.yml, the README and CONTRIBUTING. The Vertex condition is
tested by test_setup_vertex_wif.py and the Slack request by
test_slack_notify.py. Standard library only.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAUDE = (ROOT / ".github/workflows/claude-review.yml").read_text()
README = (ROOT / "README.md").read_text()
CONTRIBUTING = (ROOT / "CONTRIBUTING.md").read_text()

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:400]}")


def block(text, start):
    """Lines of the block that starts at the line equal to start, up to the next line indented as little."""
    lines = text.splitlines()
    if start not in lines:
        return []
    at = lines.index(start)
    indent = len(start) - len(start.lstrip())
    out = [start]
    for l in lines[at + 1:]:
        if l.strip() and len(l) - len(l.lstrip()) <= indent:
            break
        out.append(l)
    return out


step = block(CLAUDE, "      - name: Run Claude review")
with_lines = [l.strip() for l in step if not l.strip().startswith("#")]
check("C1 claude-code-action is given the job's own token",
      "github_token: ${{ github.token }}" in with_lines, with_lines[:12])

perms = [l.strip() for l in block(CLAUDE, "    permissions:")[1:] if not l.strip().startswith("#")]
check("C2 the Claude job asks for no id-token and keeps the rest",
      sorted(perms) == sorted(["actions: read", "contents: read", "pull-requests: write", "issues: write"]), perms)
check("C2 nothing in the Claude workflow asks for id-token",
      "id-token" not in "\n".join(l for l in CLAUDE.splitlines() if not l.strip().startswith("#")))

quick = README.split("## Quickstart", 1)[1].split("\n## ", 1)[0]
claude_caller = quick.split(".github/workflows/claude.yml", 1)[1].split("```", 2)[1]
check("C3 the README's Claude caller grants no id-token", "id-token" not in claude_caller and "permissions:" in claude_caller,
      claude_caller[:400])

stale = ["Claude GitHub App", "apps/claude", "The App posts", "is not installed on this repository",
         "differs from the default branch's copy", "matches the default branch's copy"]
found = [s for s in stale if s in README]
check("D3 the README no longer says Claude needs its App or skips pull requests that edit workflows", not found, found)
found = [s for s in ["Claude App token", "App token is"] if s in CLAUDE]
check("D3 the workflow comments no longer describe the App token", not found, found)
check("D3 the README says who posts Claude's comments", "github-actions[bot]" in README)

check("D1 the README's Vertex setup passes --release-sha", "--release-sha TAG_OBJECT_SHA --release-sha COMMIT_SHA --apply" in README)
versioning = README.split("## Versioning", 1)[1].split("\n## ", 1)[0]
check("D1 Versioning says Vertex signs in at @v1 and at a listed release commit, not at a version tag",
      "gemini-review-vertex.yml" in versioning and "commit SHA" in versioning and "pin it by SHA" in versioning, versioning)

releases = CONTRIBUTING.split("## Releases", 1)[1].split("\n## ", 1)[0]
order = [releases.find(s) for s in ("git rev-parse v1", "current tag object and commit and the new ones", "Push the tag", "new tag object and commit only")]
check("R1 CONTRIBUTING gives the release order: create v1 locally, list old and new, push, drop the old",
      -1 not in order and order == sorted(order), (order, releases))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed or not passed else 0)
