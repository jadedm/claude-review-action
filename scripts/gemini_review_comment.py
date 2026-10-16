#!/usr/bin/env python3
"""Builds the pull request comment from a parsed Gemini review (#8).

Gemini reads the pull request, which anyone who can open one controls, so every
field it returns is treated as untrusted text. Outside `code spans` it is
rendered inert: no @-mentions, links, images, HTML, headings or line breaks
that escape the quote block. Code spans are kept, because reviews quote code,
and GitHub renders nothing inside them. Each field and the whole comment are
capped, so a long reply cannot exceed GitHub's comment limit.

    python3 gemini_review_comment.py <review.json>     # prints the comment

Standard library only.
"""
import json
import re
import sys

FIELD_LIMIT = 2000
COMMENT_LIMIT = 60000  # GitHub refuses comments over 65,536 characters
ZWSP = "\u200b"  # zero-width space: breaks a pattern without changing what is shown

# Characters that start Markdown or HTML constructs outside a code span.
_MARKDOWN = re.compile(r"([\\`*_\[\]()!#|~$])")  # $ starts GitHub math
# Bidirectional controls reorder how text is shown without changing it.
_BIDI = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_HTML_CHARS = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_CODE_SPAN = re.compile(r"`([^`]+)`")
_FOOTER = ["---", "*Reviewed by Gemini*"]


def _plain(text):
    """Text outside a code span, made inert."""
    for char, entity in _HTML_CHARS.items():  # & first, so the others are not double-escaped
        text = text.replace(char, entity)
    text = _MARKDOWN.sub(r"\\\1", text)
    text = text.replace("@", "@" + ZWSP)  # no mention or team ping
    text = re.sub(r"(?i)(https?|ftp|mailto):", lambda m: m.group(1) + ZWSP + ":", text)
    return re.sub(r"(?i)\bwww\.", "www" + ZWSP + ".", text)


def _encodable(text):
    """Replace lone surrogates, which JSON allows and UTF-8 output refuses."""
    return _BIDI.sub("", text.encode("utf-8", "replace").decode("utf-8"))


def _no_block_start(text):
    """Stop a value that begins a line from starting a list or a divider."""
    if text[:1] in ("-", "+", "="):
        return "\\" + text
    return re.sub(r"^(\d+)\.", r"\1\\.", text)


def inert(value, limit=FIELD_LIMIT):
    """One line of untrusted text, safe to place in a comment."""
    text = _encodable(" ".join(str(value).split()))  # newlines would leave the quote block
    if len(text) > limit:
        text = text[:limit] + " (truncated)"
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(text):
        out.append(_plain(text[pos:m.start()]))
        out.append("`" + m.group(1) + "`")
        pos = m.end()
    out.append(_plain(text[pos:]))
    return _no_block_start("".join(out))


def code(value):
    """A value shown inside a code span, such as a file path."""
    text = _encodable(" ".join(str(value).split())).replace("`", "'")
    return "`" + text[:300] + "`"


SEVERITY_ICONS = {"critical": "🔴", "high": "🟡", "medium": "🟠", "low": "🟢"}
ASSESSMENT_ICONS = {"approve": "✅", "comment": "💬", "request_changes": "🔄"}

UNPARSED = "\n".join([
    "### Gemini Code Review",
    "",
    "Gemini replied, but its review could not be read as the expected JSON, so no "
    "findings are posted. The raw reply is in this job's log. Re-running the job "
    "usually produces a readable review.",
    "",
    *_FOOTER,
])


NAMED_LIMIT = 50  # left-out files named one by one; the rest are counted


def coverage_section(coverage):
    """What the review did not see (#29), or [] when it saw everything.

    `coverage` comes from the payload builder: `omitted` is a list of
    {file, reason}, `filtered` counts lock, generated and binary files, and
    `diff_sent` / `diff_total` are line counts. File names come from the pull
    request, so they are rendered like every other untrusted field.
    """
    if not coverage:
        return []
    omitted = coverage.get("omitted") or []
    filtered = int(coverage.get("filtered") or 0)
    diff_sent = int(coverage.get("diff_sent") or 0)
    diff_total = int(coverage.get("diff_total") or 0)
    diff_cut = bool(coverage.get("diff_cut", diff_sent < diff_total))
    if not (omitted or filtered or diff_cut):
        return []

    lines = ["### Not reviewed", ""]
    if omitted:
        lines += ["Gemini did not see these changed files:", ""]
        lines += [f"- {code(o.get('file', '?'))} ({inert(o.get('reason', ''), 80)})" for o in omitted[:NAMED_LIMIT]]
        if len(omitted) > NAMED_LIMIT:
            lines.append(f"- and {len(omitted) - NAMED_LIMIT} more")
        lines.append("")
    if filtered:
        lines += [f"{filtered} lock, generated or binary files were skipped by design.", ""]
    if diff_cut and diff_sent < diff_total:
        lines += [f"The diff was cut to its first {diff_sent:,} of {diff_total:,} lines.", ""]
    if diff_cut and diff_sent >= diff_total:
        lines += ["The diff was cut at the byte limit, inside its last line.", ""]
    return lines


def build_comment(review, coverage=None):
    section = coverage_section(coverage)
    if review.get("unparsed"):
        return UNPARSED if not section else UNPARSED.replace("\n".join(_FOOTER), "\n".join(section + _FOOTER))

    lines = ["### Gemini Code Review", ""]
    lines += [f"**Summary:** {inert(review.get('summary', '(no summary)'))}", ""]

    issues = review.get("issues") or []
    body = []
    if "issues" not in review:
        body.append(["Gemini's reply did not include a findings list.", ""])
    elif issues:
        body += ["### Findings", ""]
        for issue in issues:
            severity = str(issue.get("severity", "low"))
            icon = SEVERITY_ICONS.get(severity, "⚪")
            where = code(f"{issue.get('file', '?')}:{issue.get('line', '?')}")
            entry = [
                f"{icon} **{inert(severity.upper(), 40)}** — {where} ({inert(issue.get('category', 'general'), 80)})",
                f"> {inert(issue.get('description', ''))}",
            ]
            if issue.get("suggestion"):
                entry.append(f"> **Suggestion:** {inert(issue['suggestion'])}")
            body.append(entry + [""])
    else:
        body.append(["No issues found.", ""])

    positives = review.get("positives") or []
    if positives:
        body.append(["### What's Well Done"] + [f"- {inert(p)}" for p in positives] + [""])

    assessment = str(review.get("overall_assessment", "comment"))
    icon = ASSESSMENT_ICONS.get(assessment, "💬")
    tail = [f"**Overall: {icon} {inert(assessment.replace('_', ' ').title(), 40)}**", "", *section, *_FOOTER]

    # Add whole blocks while they fit, so a cut never lands inside one. The
    # cut notice is counted up front so adding it cannot pass the limit.
    cut = ["*Further findings were cut to fit GitHub's comment limit.*", ""]
    size = len("\n".join(lines + tail + cut))
    for block in body:
        if isinstance(block, str):
            block = [block]
        piece = "\n".join(block)
        if size + len(piece) + 1 > COMMENT_LIMIT:
            lines += cut
            break
        lines += block
        size += len(piece) + 1
    return "\n".join(lines + tail)


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        review = json.load(f)
    coverage = None
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            coverage = json.load(f)
    print(build_comment(review, coverage))
