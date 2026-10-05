#!/usr/bin/env python3
"""Cases for gemini_review_parse.py (#8). Run: python3 scripts/test_gemini_review_parse.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_review_parse import main, parse_review, repair_escapes  # noqa: E402

passed = failed = 0


def check(label, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


def review_with(description):
    """A reply as Gemini writes it, with `description` dropped in verbatim."""
    return ('{"summary": "s", "issues": [{"severity": "low", "file": "a.ts", "line": 1, '
            '"category": "c", "description": "' + description + '"}], '
            '"positives": [], "overall_assessment": "comment"}')


def description_of(text):
    r = parse_review(review_with(text))
    return r["issues"][0]["description"] if r else None


print("valid replies")
check("1 valid JSON is unchanged", parse_review('{"summary": "fine", "issues": []}'),
      {"summary": "fine", "issues": []})

print("invalid escapes from quoted code (the reported runs)")
check("2 \\d in a regex", description_of(r"uses /^\d+[smhd]$/"), r"uses /^\d+[smhd]$/")
check("  \\w in a regex", description_of(r"header /^[\w.-]{1,64}$/"), r"header /^[\w.-]{1,64}$/")
check("  \\s and \\0", description_of(r"[^\s@] and U&'\0308'"), r"[^\s@] and U&'\0308'")
check("3 a Windows path", description_of(r"C:\Users\x"), r"C:\Users\x")
check("  an escaped backslash at the end of the string", description_of("trailing x\\\\"), "trailing x\\")

print("valid escapes keep their meaning")
check("4 \\n \\\" \\\\ \\/ \\u00e9 beside \\d",
      description_of(r'line\none \"q\" back\\slash \/ caf\u00e9 \d'),
      'line\none "q" back\\slash / café \\d')
check("5 an escaped backslash before a letter is not doubled",
      repair_escapes(r'"\\d"'), r'"\\d"')
check("  \\u with too few hex digits is repaired",
      description_of(r"bad \u12 escape"), r"bad \u12 escape")

print("wrapping")
check("6 a ```json fence", parse_review('```json\n{"summary": "x"}\n```'), {"summary": "x"})
check("  a bare ``` fence", parse_review('```\n{"summary": "x"}\n```'), {"summary": "x"})
check("  a JSON value that is not an object is not a review", parse_review('["a"]'), None)

print("shape and ambiguity")
check("  a lone backslash before the closing quote stays unreadable",
      parse_review('{"summary": "path C:\\", "issues": []}'), None)
check("  issues holding strings is the wrong shape", parse_review('{"summary": "s", "issues": ["text"]}'), None)
check("  an assessment that is not text is the wrong shape", parse_review('{"summary": "s", "overall_assessment": 3}'), None)
check("  a severity that is not text is the wrong shape",
      parse_review('{"summary": "s", "issues": [{"severity": ["high"], "description": "d"}]}'), None)
# Seen live on the first canary run: Gemini used "minor", outside the prompt's
# list. The comment builder has always shown such values with a neutral icon,
# so they must not make the review unreadable.
check("  severity 'minor' (outside the prompt's list) still parses",
      parse_review('{"summary": "s", "issues": [{"severity": "minor", "description": "d"}]}'),
      {"summary": "s", "issues": [{"severity": "minor", "description": "d"}]})
check("  an unfamiliar assessment still parses",
      parse_review('{"summary": "s", "overall_assessment": "ship it"}'),
      {"summary": "s", "overall_assessment": "ship it"})
check("  true where text belongs is the wrong shape",
      parse_review('{"summary": "s", "issues": [{"severity": "low", "description": true}]}'), None)
check("  a line number still passes", parse_review('{"summary": "s", "issues": [{"line": 12}]}'),
      {"summary": "s", "issues": [{"line": 12}]})
check("  deep nesting is unreadable, not a crash",
      parse_review('{"summary": "s", "x": ' + "[" * 5000 + "]" * 5000 + "}"), None)
check("  reply text that is not a string is unreadable", parse_review(["not", "text"]), None)
check("  positives holding objects is the wrong shape", parse_review('{"summary": "s", "positives": [{"a": 1}]}'), None)
check("  an empty object is not a review", parse_review("{}"), None)
check("  a review with no summary is not a review", parse_review('{"issues": []}'), None)
# On Python 3.11+ an integer past the digit limit raises ValueError, not
# JSONDecodeError; earlier versions parse it. Either way it must not crash.
check("  a huge integer does not crash", type(parse_review('{"summary": "s", "n": ' + "1" * 5000 + "}")).__name__ in ("dict", "NoneType"), True)
check("  a minimal valid review passes", parse_review('{"summary": "s"}'), {"summary": "s"})

print("end to end through main()")


def run(envelope):
    d = tempfile.mkdtemp()
    src, dst = os.path.join(d, "in.json"), os.path.join(d, "out.json")
    with open(src, "w") as f:
        json.dump(envelope, f)
    code = main(src, dst)
    out = json.load(open(dst)) if os.path.exists(dst) else None
    return code, out


def envelope(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


code, out = run(envelope(review_with(r"regex /\d+/")))
check("  a reply with \\d: exit 0, review written", (code, out["issues"][0]["description"]),
      (0, r"regex /\d+/"))
truncated = review_with("cut off")[:60]
code, out = run(envelope(truncated))
check("7 a truncated reply: exit 2, no raw text in the output file", (code, out), (2, {"unparsed": True}))
code, out = run({"error": {"message": "quota exceeded", "code": 429}})
check("8 an API error: exit 1, nothing written", (code, out), (1, None))
code, out = run({"candidates": [{"finishReason": "SAFETY"}]})
check("  no reply text: exit 1, nothing written", (code, out), (1, None))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
