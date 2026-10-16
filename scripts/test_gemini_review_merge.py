#!/usr/bin/env python3
"""Cases for gemini_review_merge.py (#30). Run: python3 scripts/test_gemini_review_merge.py"""
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_review_comment import COMMENT_LIMIT, UNPARSED, build_comment  # noqa: E402
from gemini_review_merge import main  # noqa: E402

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}: {str(detail)[:500]}")


def review(summary, issues=(), verdict="comment", positives=()):
    return {"summary": summary, "issues": list(issues), "positives": list(positives), "overall_assessment": verdict}


def issue(file, line, desc, severity="low"):
    return {"file": file, "line": line, "description": desc, "severity": severity, "category": "bug"}


def run(calls, coverage=None):
    """calls: list of (status or None, files, review or None). Returns (exit, review, coverage)."""
    root = pathlib.Path(tempfile.mkdtemp())
    chunks = root / "chunks"
    for n, (status, files, rev) in enumerate(calls, 1):
        d = chunks / str(n)
        d.mkdir(parents=True)
        (d / "files.json").write_text(json.dumps(files))
        if status:
            (d / "status").write_text(status + "\n")
        if rev is not None:
            (d / "review.json").write_text(json.dumps(rev))
    cov_path = root / "coverage.json"
    cov_path.write_text(json.dumps(coverage or {"omitted": [], "filtered": 0, "diff_sent": 1, "diff_total": 1}))
    out = root / "review.json"
    code = main(str(chunks), str(out), str(cov_path))
    merged = json.loads(out.read_text()) if out.exists() else None
    return code, merged, json.loads(cov_path.read_text())


print("merging")
one = review("only", [issue("a.py", 1, "x")], "approve")
code, merged, cov = run([("ok", ["a.py"], one)])
check("30.1 one call: its review is written unchanged", code == 0 and merged == one, merged)

code, merged, cov = run([("ok", ["a.py"], review("first", [issue("a.py", 1, "x")], "approve", ["tidy"])),
                         ("ok", ["b.py"], review("second", [issue("b.py", 2, "y", "high")], "request_changes", ["tidy", "tested"]))])
check("30.8 two calls: findings from both, in call order",
      [i["file"] for i in merged["issues"]] == ["a.py", "b.py"], merged)
check("  the verdict is the most severe", merged["overall_assessment"] == "request_changes", merged)
check("  each summary is a part", [p["label"] for p in merged["parts"]] == ["Part 1 of 2", "Part 2 of 2"]
      and [p["summary"] for p in merged["parts"]] == ["first", "second"], merged.get("parts"))
check("  positives are merged without repeats", merged["positives"] == ["tidy", "tested"], merged["positives"])

code, merged, cov = run([("ok", ["a.py"], review("s", [issue("a.py", 1, "same", "low")])),
                         ("ok", ["b.py"], review("s", [issue("a.py", 1, "same", "critical"), issue("a.py", 1, "other")]))])
check("30.9 a repeated finding is kept once, at its highest severity",
      len(merged["issues"]) == 2 and merged["issues"][0]["severity"] == "critical", merged["issues"])

code, merged, cov = run([("ok", ["a.py"], review("s", verdict="approve")), ("ok", ["b.py"], review("s", verdict="lgtm"))])
check("  an unknown verdict counts as comment", merged["overall_assessment"] == "comment", merged)

print("failed and unreadable calls")
code, merged, cov = run([("ok", ["a.py"], review("good", [issue("a.py", 1, "x")])),
                         ("failed", ["b.py", "c.py"], None)])
check("30.10 one call failed: the other's findings post", code == 0 and merged["issues"][0]["file"] == "a.py", merged)
check("  and the failed call's files are listed",
      cov["omitted"] == [{"file": "b.py", "reason": "review call failed"}, {"file": "c.py", "reason": "review call failed"}], cov)
code, merged, cov = run([("ok", ["a.py"], review("good")), (None, ["b.py"], None)])
check("  a call with no status counts as failed", cov["omitted"] == [{"file": "b.py", "reason": "review call failed"}], cov)
code, merged, cov = run([("ok", ["a.py"], review("good")), ("unreadable", ["b.py"], None)])
check("30.11 an unreadable reply: its files are listed as such",
      code == 0 and cov["omitted"] == [{"file": "b.py", "reason": "reply unreadable"}], cov)
code, merged, cov = run([("ok", ["a.py"], review("good")), ("unreadable", ["b.py"], None), ("failed", ["c.py"], None)])
check("30.23 mixed ok, unreadable and failed: posts with two parts listed",
      code == 0 and len(merged["parts"]) == 1 and {o["reason"] for o in cov["omitted"]} == {"reply unreadable", "review call failed"},
      (merged, cov))
code, merged, cov = run([("unreadable", ["a.py"], None), ("unreadable", ["b.py"], None)])
check("30.23 all unreadable: the unreadable marker, so the fixed notice posts and the job fails",
      code == 0 and merged == {"unparsed": True} and build_comment(merged, cov).startswith(UNPARSED.split("---")[0]), merged)
code, merged, cov = run([("failed", ["a.py"], None), ("failed", ["b.py"], None)])
check("30.12 all failed: exit 1 and no review written", code == 1 and merged is None, (code, merged))
code, merged, cov = run([("unreadable", ["a.py"], None), ("failed", ["b.py"], None)])
check("  unreadable plus failed, none ok: the unreadable marker", code == 0 and merged == {"unparsed": True}, merged)
code, merged, cov = run([("ok", ["a.py"], review("good")), ("failed", ["b.py"], None)],
                        {"omitted": [{"file": "big.py", "reason": "over 3000 lines"}], "filtered": 0,
                         "diff_sent": 10, "diff_total": 20, "diff_cut": True})
check("30.25 a cut diff and a failed call are both kept in coverage",
      cov["diff_cut"] is True and [o["file"] for o in cov["omitted"]] == ["big.py", "b.py"], cov)

print("the merged comment")
code, merged, cov = run([("ok", ["a.py"], review("@team see https://x <img>", [issue("a.py", 1, "[l](https://x) @u")])),
                         ("ok", ["b.py"], review("fine"))])
comment = build_comment(merged, cov)
check("30.14 hostile text in a part is inert",
      not re.search(r"@[a-z]|https?://|<[a-z]|\]\(", re.sub(r"`[^`]+`", "", comment).replace("​", " ")), comment)
code, merged, cov = run([("ok", [f"f{n}.py"], review("s" * 3000, [issue("f.py", i, "d" * 1900) for i in range(200)]))
                         for n in range(3)], {"omitted": [{"file": f"x{i}.py", "reason": "review call failed"} for i in range(60)]})
comment = build_comment(merged, cov)
check("30.15 three near-full parts stay under the comment limit", len(comment) <= COMMENT_LIMIT, len(comment))
check("  and keep the footer and the Not reviewed section",
      comment.endswith("*Reviewed by Gemini*") and "### Not reviewed" in comment, comment[-300:])
check("30.26 three long part summaries all show their labels",
      all(f"Part {n} of 3" in comment for n in (1, 2, 3)), comment[:600])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
