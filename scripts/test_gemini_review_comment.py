#!/usr/bin/env python3
"""Cases for gemini_review_comment.py (#8). Run: python3 scripts/test_gemini_review_comment.py"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_review_comment import COMMENT_LIMIT, UNPARSED, build_comment, inert  # noqa: E402

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {detail}")


def outside_code(text):
    """The text GitHub would render as Markdown: everything not inside `...`."""
    return re.sub(r"`[^`]+`", "", text)


print("hostile text is inert")
for label, raw, pattern in [
    ("1 @-mention", "ping @octocat and @org/team", r"@[A-Za-z]"),
    ("2 Markdown link", "see [docs](https://evil.example)", r"\]\("),
    ("3 image", "![x](https://evil.example/t.png)", r"!\["),
    ("4 bare URL is not autolinked", "visit https://evil.example now", r"https?://"),
    ("  www. is not autolinked", "visit www.evil.example now", r"www\."),
    ("5 HTML tag", "<img src=x onerror=alert(1)> <details>", r"<[a-z]"),
    ("  autolink in angle brackets", "<https://evil.example>", r"<h"),
]:
    out = inert(raw)
    check(label, not re.search(pattern, out), repr(out))

out = inert("line one\n### Heading\n> quote\n@here")
check("6 newlines cannot start a heading or leave the quote", "\n" not in out, repr(out))

print("code spans are kept")
out = inert("use `re.match(r'^\\d+$', s)` instead of `@decorator`")
check("7 code span content is unchanged", "`re.match(r'^\\d+$', s)`" in out and "`@decorator`" in out, repr(out))
check("  a mention inside a code span stays literal", "`@decorator`" in out and "@\u200bdecorator" not in out, repr(out))
out = inert("odd ` backtick then @user")
check("  an unmatched backtick does not open code", "\\`" in out and "@\u200buser" in out, repr(out))

for label, raw, want in [
    ("  a leading - cannot start a list or divider", "--- or - item", "\\---"),
    ("  a leading + cannot start a list", "+ item", "\\+ item"),
    ("  a leading number cannot start a list", "1. item", "1\\. item"),
]:
    out = inert(raw)
    check(label, out.startswith(want), repr(out))
check("  a lone surrogate is replaced, not a crash", inert("a\ud800b") == "a?b", repr(inert("a\ud800b")))
check("  math cannot render", "$x^2$" not in inert("area $x^2$ here"), repr(inert("area $x^2$ here")))
bidi = "a\u202eb\u2066c\u061cd\u200fe"
check("  bidirectional controls are removed", inert(bidi) == "abcde", repr(inert(bidi)))

print("length bounds")
long_text = "a" * 10000
check("8 a long field is capped", len(inert(long_text)) < 2100, len(inert(long_text)))
many = {"summary": "s", "issues": [{"severity": "low", "file": "f", "line": 1, "category": "c",
                                     "description": "d" * 1900} for _ in range(200)]}
comment = build_comment(many)
check("9 a long review stays under the comment limit", len(comment) <= COMMENT_LIMIT, len(comment))
check("  and says it was cut", "were cut to fit" in comment)
check("  and keeps its footer", comment.endswith("*Reviewed by Gemini*"))
# Sizes chosen so the blocks fill to just under the limit, where the cut
# notice used to be added after the fit check and push the comment over it.
worst = max((len(build_comment({"summary": "s", "issues": [
    {"severity": "low", "file": "f", "line": 1, "category": "c", "description": "d" * n}] * 200}))
    for n in range(1500, 2000, 7)))
check("  the cut notice never pushes a comment over the limit", worst <= COMMENT_LIMIT, worst)

print("the review itself")
review = {"summary": "Adds `x`", "issues": [
    {"severity": "high", "file": "a.ts", "line": 3, "category": "security", "description": "bad",
     "suggestion": "fix"},
    {"severity": "minor", "file": "b`.ts", "line": 1, "category": "style", "description": "nit"}],
    "positives": ["tests"], "overall_assessment": "request_changes"}
comment = build_comment(review)
check("10 findings, icons and verdict render", all(s in comment for s in [
    "**Summary:** Adds `x`", "🟡 **HIGH** — `a.ts:3` (security)", "> bad", "> **Suggestion:** fix",
    "⚪ **MINOR**", "- tests", "**Overall: 🔄 Request Changes**"]), comment)
check("  a backtick in a file path cannot close the code span", "`b'.ts:1`" in comment, comment)
check("  an empty findings list says no issues", "No issues found." in build_comment({"summary": "s", "issues": []}))
check("  a missing findings list does not claim no issues",
      "No issues found." not in build_comment({"summary": "s"}) and "did not include a findings list" in build_comment({"summary": "s"}))
check("  a lone surrogate in a field still builds a printable comment",
      "\ud800" not in build_comment({"summary": "\ud800", "issues": [{"file": "\udc00", "description": "x"}]})
      and "\udc00" not in build_comment({"summary": "\ud800", "issues": [{"file": "\udc00", "description": "x"}]}))
check("11 the unreadable notice is fixed text", build_comment({"unparsed": True}) == UNPARSED)
check("  every hostile field in a full review is inert",
      not re.search(r"@[a-z]|https?://|<[a-z]|\]\(", outside_code(build_comment({
          "summary": "@a https://x <b>", "issues": [{"severity": "@s", "file": "f", "line": 1,
          "category": "[c](https://x)", "description": "<img>", "suggestion": "@d"}],
          "positives": ["www.x.com @p"], "overall_assessment": "<script>"})).replace("\u200b", " ")),
      "a field reached the comment raw")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
