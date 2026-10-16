#!/usr/bin/env python3
"""Merges the reviews of a pull request's Gemini calls into one (#30).

    python3 gemini_review_merge.py <chunks_dir> <review_out> <coverage>

Each <chunks_dir>/<n>/ holds files.json (the files that call owned), status
(ok, unreadable or failed; missing means failed) and, when ok, review.json.

- One call: its review is written unchanged, so a small pull request looks
  exactly as it did before chunking.
- Several: findings are joined in call order; a finding repeated with the same
  file, line and description is kept once, at its highest severity. The
  verdict is the most severe. Each call's summary is kept as a part.
- A call that failed or was unreadable: every file it owned is added to the
  coverage file, so the comment lists it under "Not reviewed".
- No call ok: the unreadable marker if any reply was unreadable (the comment
  step posts the fixed notice and fails the job), else exit 1.

Standard library only.
"""
import json
import pathlib
import sys

SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
VERDICT = {"request_changes": 2, "comment": 1, "approve": 0}
REASON = {"failed": "review call failed", "unreadable": "reply unreadable"}


def load_calls(chunks_dir):
    calls = []
    for d in sorted(pathlib.Path(chunks_dir).iterdir(), key=lambda p: int(p.name)):
        status_file = d / "status"
        status = status_file.read_text().strip() if status_file.exists() else "failed"
        review = json.loads((d / "review.json").read_text()) if status == "ok" else None
        if review is not None:
            review.pop("parts", None)  # parts are ours to build; a reply's own would replace the summary
        diff = d / "diff.txt"
        calls.append({"n": int(d.name), "status": status, "files": json.loads((d / "files.json").read_text()),
                      "review": review, "diff_lines": len(diff.read_bytes().splitlines()) if diff.exists() else 0})
    return calls


def merge_issues(reviews):
    """Findings in call order; a repeat keeps its first place and its highest severity."""
    merged, seen = [], {}
    for review in reviews:
        for issue in review.get("issues") or []:
            key = (str(issue.get("file")), str(issue.get("line")), str(issue.get("description")))
            if key not in seen:
                seen[key] = len(merged)
                merged.append(issue)
                continue
            kept = merged[seen[key]]
            rank = SEVERITY.get(str(issue.get("severity")).lower(), 0)
            if rank > SEVERITY.get(str(kept.get("severity")).lower(), 0):
                merged[seen[key]] = issue
    return merged


def verdict(reviews):
    """The most severe verdict; anything unknown counts as comment."""
    ranked = [(VERDICT.get(r.get("overall_assessment"), 1), r.get("overall_assessment")) for r in reviews]
    rank = max(r for r, _ in ranked)
    return {v: k for k, v in VERDICT.items()}[rank]


def merge(calls):
    reviews = [c["review"] for c in calls if c["status"] == "ok"]
    if len(calls) == 1 and reviews:
        return reviews[0]
    total = len(calls)
    positives = []
    for review in reviews:
        positives += [p for p in review.get("positives") or [] if p not in positives]
    parts = [{"label": f"Part {c['n']} of {total}", "summary": c["review"].get("summary", "")}
             for c in calls if c["status"] == "ok"]
    return {
        "summary": " ".join(f"{p['label']}: {p['summary']}" for p in parts),
        "parts": parts,
        "issues": merge_issues(reviews),
        "positives": positives,
        "overall_assessment": verdict(reviews),
    }


def main(chunks_dir, review_out, coverage_path):
    if not pathlib.Path(chunks_dir).is_dir():
        # Every changed file was skipped and there was no diff to send: say so
        # rather than fail, so the comment still lists what was skipped.
        print("No review calls were planned: nothing fit the caps")
        pathlib.Path(review_out).write_text(json.dumps({"nothing_sent": True}))
        return 0
    calls = load_calls(chunks_dir)
    coverage_file = pathlib.Path(coverage_path)
    coverage = json.loads(coverage_file.read_text()) if coverage_file.exists() else {"omitted": []}
    for call in calls:
        if call["status"] not in REASON:
            continue
        coverage["omitted"] += [{"file": f, "reason": REASON[call["status"]]} for f in call["files"]]
        coverage["diff_sent"] = max(int(coverage.get("diff_sent") or 0) - call["diff_lines"], 0)
        coverage["diff_cut"] = True
        print(f"::warning::Gemini call {call['n']} {call['status']}; its {len(call['files'])} files are listed as not reviewed")
    coverage_file.write_text(json.dumps(coverage))

    ok = [c for c in calls if c["status"] == "ok"]
    unreadable = [c for c in calls if c["status"] == "unreadable"]
    for c in calls:
        print(f"Call {c['n']}: {c['status']}, {len(c['files'])} files")
    if not ok and not unreadable:
        print("ERROR: every Gemini call failed; see each call's log above")
        return 1
    review = merge(calls) if ok else {"unparsed": True}
    pathlib.Path(review_out).write_text(json.dumps(review))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
