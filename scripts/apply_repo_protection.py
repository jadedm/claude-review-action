#!/usr/bin/env python3
"""The release protections for this repo, as code (sandbox#60).

    python3 scripts/apply_repo_protection.py --repo jadedm/claude-review-action            # report only
    python3 scripts/apply_repo_protection.py --repo jadedm/claude-review-action --apply    # create what is missing

Every caller runs this repo's workflows with its own secrets, so:
  - tags v*: only a repo admin may create, move or delete them (the release step moves v1);
  - main: no deletion or force-push, changes arrive by pull request, an admin may bypass;
  - this repo's own workflows get a read-only token and may not approve pull requests.

For each ruleset it prints "matches", "differs" (with the fields that differ) or
"missing". --apply creates a missing ruleset and sets the token; it never edits
or deletes an existing ruleset, so drift is reported for a person to settle.
Exits 1 when anything differs or is missing after the run. Uses the gh CLI.
"""
import argparse
import json
import subprocess
import sys

ADMIN = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]  # 5: built-in admin role
RULESETS = [
    {
        "name": "protect release tags",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "bypass_actors": ADMIN,
        "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
    },
    {
        "name": "protect main",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": ADMIN,
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "allowed_merge_methods": ["merge", "squash", "rebase"],
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": False,
            }},
        ],
    },
]
TOKEN = {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}
COMPARED = ("target", "enforcement", "conditions", "bypass_actors", "rules")


def gh(*args, body=None):
    p = subprocess.run(["gh", "api", *args], input=json.dumps(body) if body is not None else None,
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"gh api {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout) if p.stdout.strip() else None


def normal(ruleset):
    """The compared fields, with rules sorted by type and only the parameters this file sets."""
    want = {r["type"]: r for r in next((x for x in RULESETS if x["name"] == ruleset["name"]), {"rules": []})["rules"]}
    rules = []
    for rule in sorted(ruleset.get("rules", []), key=lambda r: r["type"]):
        keys = want.get(rule["type"], {}).get("parameters", {}).keys()
        params = {k: v for k, v in (rule.get("parameters") or {}).items() if k in keys}
        rules.append({"type": rule["type"], **({"parameters": params} if params else {})})
    out = {k: ruleset.get(k) for k in COMPARED if k != "rules"}
    out["bypass_actors"] = sorted(out["bypass_actors"] or [], key=json.dumps)
    out["rules"] = rules
    return out


def check_rulesets(repo, apply):
    live = {r["name"]: r["id"] for r in gh(f"repos/{repo}/rulesets") or []}
    problems = 0
    for wanted in RULESETS:
        name = wanted["name"]
        if name not in live and apply:
            created = gh("-X", "POST", f"repos/{repo}/rulesets", "--input", "-", body=wanted)
            print(f"  {name}: created (id {created['id']})")
            continue
        if name not in live:
            print(f"  {name}: missing")
            problems += 1
            continue
        have, want = normal(gh(f"repos/{repo}/rulesets/{live[name]}")), normal(wanted)
        diff = [k for k in COMPARED if have[k] != want[k]]
        print(f"  {name}: " + ("matches" if not diff else f"differs in {', '.join(diff)}"))
        problems += bool(diff)
    for extra in sorted(set(live) - {r["name"] for r in RULESETS}):
        print(f"  {extra}: not in this file (left alone)")
    return problems


def check_token(repo, apply):
    have = gh(f"repos/{repo}/actions/permissions/workflow")
    have = {k: have.get(k) for k in TOKEN}
    if have == TOKEN:
        print(f"  actions token: matches {TOKEN}")
        return 0
    if apply:
        gh("-X", "PUT", f"repos/{repo}/actions/permissions/workflow", "--input", "-", body=TOKEN)
        print(f"  actions token: was {have}, set to {TOKEN}")
        return 0
    print(f"  actions token: differs: is {have}, wanted {TOKEN}")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print(f"{a.repo} ({'apply' if a.apply else 'report only'}):")
    problems = check_rulesets(a.repo, a.apply) + check_token(a.repo, a.apply)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
