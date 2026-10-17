#!/usr/bin/env python3
"""Cases for apply_repo_protection.py (sandbox#60). Run: python3 scripts/test_repo_protection.py

A stub `gh` on PATH serves rulesets and token settings from a state file and
records every call, so no real repo is touched. Standard library only.
"""
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import apply_repo_protection as arp  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["STUB_STATE"]
state = json.load(open(state_path))
args = sys.argv[2:]  # drop "api"
open(os.environ["STUB_CALLS"], "a").write(json.dumps(args) + "\n")
method = "GET"
if "-X" in args:
    method = args[args.index("-X") + 1]
path = [a for a in args if a.startswith("repos/")][0]
query = path.split("?", 1)[1] if "?" in path else ""
path = path.split("?", 1)[0]
open(os.environ["STUB_CALLS"] + ".query", "a").write(query + "\n")
if state.get("fail"):
    sys.stderr.write("HTTP 500: boom\n"); sys.exit(1)
body = json.loads(sys.stdin.read()) if "--input" in args else None
if path.endswith("/rulesets") and method == "GET":
    print(json.dumps([{"id": r["id"], "name": r["name"]} for r in state["rulesets"]]))
elif path.endswith("/rulesets") and method == "POST":
    body["id"] = 100 + len(state["rulesets"])
    if state.get("drop_bypass"):
        body["bypass_actors"] = []  # the API quietly stores less than was sent
    state["rulesets"].append(body); print(json.dumps(body))
elif "/rulesets/" in path and method == "GET":
    rid = int(path.rsplit("/", 1)[1]); print(json.dumps(next(r for r in state["rulesets"] if r["id"] == rid)))
elif path.endswith("/actions/permissions/workflow") and method == "GET":
    print(json.dumps(state["token"]))
elif path.endswith("/actions/permissions/workflow") and method == "PUT":
    state["token"] = body
else:
    sys.stderr.write(f"unexpected {method} {path}\n"); sys.exit(2)
json.dump(state, open(state_path, "w"))
'''

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def live_like():
    """Rulesets as the API returns them: ids, extra fields and extra parameters."""
    out = []
    for i, r in enumerate(arp.RULESETS, 1):
        r = copy.deepcopy(r)
        r["id"] = i
        r["source"] = "o/r"
        for rule in r["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"]["require_extra_approval_for_unattributed_changes"] = True
        r["rules"] = list(reversed(r["rules"]))
        out.append(r)
    return out


def run(state, *flags):
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "gh").write_text(STUB)
    (work / "gh").chmod(0o755)
    (work / "state.json").write_text(json.dumps(state))
    env = dict(os.environ, PATH=f"{work}:{os.environ['PATH']}", STUB_STATE=str(work / "state.json"),
               STUB_CALLS=str(work / "calls.jsonl"))
    p = subprocess.run([sys.executable, str(HERE / "apply_repo_protection.py"), "--repo", "o/r", *flags],
                       capture_output=True, text=True, env=env)
    calls = [json.loads(l) for l in (work / "calls.jsonl").read_text().splitlines()] if (work / "calls.jsonl").exists() else []
    end = json.loads((work / "state.json").read_text())
    return p, calls, end


def writes(calls):
    return [c for c in calls if "-X" in c]


good_token = dict(arp.TOKEN)
p, calls, end = run({"rulesets": live_like(), "token": good_token})
check("1 everything as declared: matches, exit 0, no writes",
      p.returncode == 0 and p.stdout.count(": matches") == 3 and writes(calls) == [], p.stdout)

p, calls, end = run({"rulesets": [], "token": good_token})
check("2 report only, rulesets missing: says missing, exit 1, no writes",
      p.returncode == 1 and p.stdout.count(": missing") == 2 and writes(calls) == [], p.stdout)

p, calls, end = run({"rulesets": [], "token": good_token}, "--apply")
created = {r["name"]: r for r in end["rulesets"]}
check("3 --apply creates each missing ruleset exactly as declared, exit 0",
      p.returncode == 0 and set(created) == {r["name"] for r in arp.RULESETS}
      and all(arp.normal(created[r["name"]]) == arp.normal(r) for r in arp.RULESETS), p.stdout)
check("3 and the read-back pass writes nothing: exactly one POST per ruleset, then an after section",
      [c[c.index("-X") + 1] for c in writes(calls)] == ["POST", "POST"] and "after:" in p.stdout, writes(calls))

for label, mutate, field in [
    ("bypass removed", lambda r: r[0].update(bypass_actors=[]), "bypass_actors"),
    ("a tag rule dropped", lambda r: r[0].update(rules=[x for x in r[0]["rules"] if x["type"] != "creation"]), "rules"),
    ("main's pattern changed", lambda r: r[1]["conditions"]["ref_name"].update(include=["refs/heads/dev"]), "conditions"),
    ("review count raised", lambda r: next(x for x in r[1]["rules"] if x["type"] == "pull_request")["parameters"].update(required_approving_review_count=1), "rules"),
    ("enforcement off", lambda r: r[1].update(enforcement="disabled"), "enforcement"),
]:
    rs = live_like()
    mutate(rs)
    p, calls, end = run({"rulesets": rs, "token": good_token}, "--apply")
    check(f"4 drift ({label}): reported as differs in {field}, exit 1, and not overwritten",
          p.returncode == 1 and f"differs in {field}" in p.stdout and writes(calls) == [] and end["rulesets"] == rs, p.stdout)

rs = live_like() + [{"id": 9, "name": "someone else's", "target": "branch", "rules": []}]
p, calls, end = run({"rulesets": rs, "token": good_token})
check("5 a ruleset not in this file is named and left alone", p.returncode == 0 and "someone else's: not in this file" in p.stdout and writes(calls) == [], p.stdout)

bad_token = {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True}
p, calls, end = run({"rulesets": live_like(), "token": bad_token})
check("6 report only, token write: differs, exit 1, not changed", p.returncode == 1 and "actions token: differs" in p.stdout and end["token"] == bad_token, p.stdout)
p, calls, end = run({"rulesets": live_like(), "token": bad_token}, "--apply")
check("7 --apply sets the token to read and no approvals", p.returncode == 0 and end["token"] == arp.TOKEN, (p.stdout, end["token"]))

p, calls, end = run({"rulesets": [], "token": good_token, "fail": True}, "--apply")
check("8 a failing gh call stops with its error and a non-zero exit", p.returncode != 0 and "HTTP 500" in p.stderr, (p.returncode, p.stderr))

p, calls, end = run({"rulesets": [], "token": good_token, "drop_bypass": True}, "--apply")
check("9 --apply reads back what it wrote: a ruleset stored without its bypass fails the run",
      p.returncode == 1 and "differs in bypass_actors" in p.stdout, p.stdout)
p, calls, end = run({"rulesets": live_like(), "token": good_token})
lists = [c for c in calls if any(a.startswith("repos/o/r/rulesets?") for a in c)]
check("10 the list asks for 100 per page and this repo's own rulesets only",
      lists and all("per_page=100" in a and "includes_parents=false" in a for c in lists for a in c if a.startswith("repos/o/r/rulesets?")), calls[:2])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
