#!/usr/bin/env python3
"""Cases for who can start a review by comment (#64).

Run: python3 scripts/test_comment_trigger.py

Reads the review job's real `if:` from claude-review.yml, gemini-review.yml
and gemini-review-vertex.yml and evaluates it against
event payloads with a small evaluator of GitHub's expression syntax: literals,
property access, !, ==, !=, &&, || and the contains and fromJSON functions.
Missing properties are null, && and || return an operand as GitHub does, and
string comparison ignores case. Standard library only.
"""
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
WF = REPO / ".github/workflows"
# Each workflow and the mention that starts it.
WORKFLOWS = (("claude-review.yml", "@claude"), ("gemini-review.yml", "@gemini"), ("gemini-review-vertex.yml", "@gemini"))


def job_if(text):
    block = text.split("\n    if: >\n", 1)[1].split("\n    runs-on:", 1)[0]
    return " ".join(l.strip() for l in block.splitlines())

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+)|(?P<op>&&|\|\||==|!=|!|\(|\)|,|\.)|(?P<name>[A-Za-z_][A-Za-z0-9_-]*))")


def tokens(src):
    out, at = [], 0
    while at < len(src.rstrip()):
        m = TOKEN.match(src, at)
        if not m:
            raise ValueError(f"cannot read the expression at {src[at:at + 30]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        at = m.end()
    return out


def truthy(v):
    return v not in (None, False, 0, "")


def equal(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def contains(haystack, needle):
    if isinstance(haystack, str):
        return isinstance(needle, str) and needle.lower() in haystack.lower()
    if isinstance(haystack, list):
        return any(equal(item, needle) for item in haystack)
    return False


FUNCS = {"contains": contains, "fromJSON": json.loads}


class Eval:
    def __init__(self, src, ctx):
        self.toks, self.at, self.ctx = tokens(src), 0, ctx

    def peek(self):
        return self.toks[self.at] if self.at < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value!r}, got {tok!r}")
        self.at += 1
        return tok

    def run(self):
        v = self.or_()
        if self.at != len(self.toks):
            raise ValueError(f"unread tokens: {self.toks[self.at:]}")
        return v

    def or_(self):
        v = self.and_()
        while self.peek()[1] == "||":
            self.take()
            right = self.and_()
            v = v if truthy(v) else right
        return v

    def and_(self):
        v = self.cmp()
        while self.peek()[1] == "&&":
            self.take()
            right = self.cmp()
            v = right if truthy(v) else v
        return v

    def cmp(self):
        v = self.unary()
        while self.peek()[1] in ("==", "!="):
            op = self.take()[1]
            right = self.unary()
            v = equal(v, right) if op == "==" else not equal(v, right)
        return v

    def unary(self):
        if self.peek()[1] == "!":
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.take()
        if value == "(":
            v = self.or_()
            self.take(")")
            return v
        if kind == "str":
            return value[1:-1].replace("''", "'")
        if kind == "num":
            return int(value)
        if value in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[value]
        if kind != "name":
            raise ValueError(f"unexpected {value!r}")
        if self.peek()[1] == "(":
            self.take()
            args = [self.or_()]
            while self.peek()[1] == ",":
                self.take()
                args.append(self.or_())
            self.take(")")
            return FUNCS[value](*args)
        v = self.ctx.get(value)
        while self.peek()[1] == ".":
            self.take()
            key = self.take()[1]
            v = v.get(key) if isinstance(v, dict) else None
        return v


def evaluate(src, ctx):
    return Eval(src, ctx).run()


def event(name="issue_comment", action="created", private=False, role="MEMBER", body="@claude please review",
          on_pr=True, require=False):
    payload = {"action": action, "repository": {"private": private},
               "comment": {"body": body, "author_association": role}}
    if name == "issue_comment":
        payload["issue"] = {"number": 7, "pull_request": {"url": "u"} if on_pr else None}
    if name == "pull_request":
        payload = {"action": action, "repository": {"private": private}, "pull_request": {"number": 7}}
    return {"github": {"event_name": name, "event": payload}, "inputs": {"require_role_on_private": require}}


def runner(expr, mention):
    def runs(**kw):
        kw.setdefault("body", f"{mention} please review")
        return truthy(evaluate(expr, event(**kw)))
    return runs


print("the evaluator itself")
check("16 missing properties are null, && and || return an operand, ! negates, == ignores case",
      evaluate("a.b.c", {}) is None and evaluate("'x' && 'y'", {}) == "y" and evaluate("'' || 'z'", {}) == "z"
      and evaluate("!a.b", {}) is True and evaluate("'Ab' == 'aB'", {}) is True and evaluate("'a' != 'b'", {}) is True)
check("16 contains reads strings and arrays, fromJSON parses an array",
      evaluate("contains('Hi @Claude', '@claude')", {}) is True
      and evaluate("contains(fromJSON('[\"OWNER\",\"MEMBER\"]'), 'member')", {}) is True
      and evaluate("contains(fromJSON('[\"OWNER\"]'), x.y)", {}) is False)
check("16 && binds tighter than ||", evaluate("true || false && false", {}) is True and evaluate("(true || false) && false", {}) is False)

for wf, mention in WORKFLOWS:
    TEXT = (WF / wf).read_text()
    runs = runner(job_if(TEXT), mention)
    print(f"who can start a review from {wf} (#64)")
    check(f"{wf}: 64.1 a pull_request event runs, public or private, input on or off",
          all(runs(name="pull_request", action=a, private=p, require=r)
              for a in ("opened", "synchronize") for p in (False, True) for r in (False, True)))
    check(f"{wf}: 64.2 public repo, new {mention} comment by a MEMBER: runs", runs())
    check(f"{wf}: 64.3 public repo, association NONE: refused", not runs(role="NONE"))
    check(f"{wf}: 64.4 public repo, association CONTRIBUTOR: refused", not runs(role="CONTRIBUTOR"))
    check(f"{wf}: 64.5 private repo, input left at its default, association NONE: runs as before", runs(private=True, role="NONE"))
    check(f"{wf}: 64.6 private repo, input on, association NONE: refused", not runs(private=True, role="NONE", require=True))
    check(f"{wf}: 64.7 private repo, input on, association CONTRIBUTOR: refused", not runs(private=True, role="CONTRIBUTOR", require=True))
    check(f"{wf}: 64.8 private repo, input on, COLLABORATOR issue comment: runs", runs(private=True, role="COLLABORATOR", require=True))
    check(f"{wf}: 64.9 private repo, input on, OWNER review-thread comment: runs",
          runs(name="pull_request_review_comment", private=True, role="OWNER", require=True))
    check(f"{wf}: 64.10 edited {mention} comment by a MEMBER on a public repo: refused", not runs(action="edited"))
    check(f"{wf}: 64.11 deleted {mention} comment, private repo, default input: refused", not runs(action="deleted", private=True, role="NONE"))
    check(f"{wf}: 64.12 edited review-thread comment, private repo: refused",
          not runs(name="pull_request_review_comment", action="edited", private=True, role="OWNER"))
    check(f"{wf}: 64.13 {mention} on an issue that is not a pull request: refused", not runs(on_pr=False, role="OWNER"))
    check(f"{wf}: 64.14 new comment by a MEMBER without {mention}: refused", not runs(body="looks good"))

    spec = TEXT.split("      require_role_on_private:\n", 1)[-1].split("\n    secrets:", 1)[0]
    check(f"64.15 {wf}: the input is a boolean that defaults to false",
          "      require_role_on_private:\n" in TEXT and "        type: boolean" in spec and "        default: false" in spec, spec[:300])

    other = "@gemini" if mention == "@claude" else "@claude"
    check(f"{wf}: 64.17 a new {other} comment by a MEMBER does not start this workflow", not runs(body=f"{other} please review"))

readme = (REPO / "README.md").read_text()
row = next((l for l in readme.splitlines() if l.startswith("| `require_role_on_private` |")), "")
both = readme.split("### Both workflows\n", 1)[-1].split("\n### ", 1)[0]
check("64.16 the README lists the input once, under Both workflows, as a boolean defaulting to false",
      "| boolean | `false` |" in row and row in both and readme.count("| `require_role_on_private` |") == 1, row)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
