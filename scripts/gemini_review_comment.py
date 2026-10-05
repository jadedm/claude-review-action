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
ZWSP = "​"  # zero-width space: breaks a pattern without changing what is shown

# Characters that start Markdown or HTML constructs outside a code span.
_MARKDOWN = re.compile(r"([\\`*_\[\]()!#|~])")
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


def inert(value, limit=FIELD_LIMIT):
    """One line of untrusted text, safe to place in a comment."""
    text = " ".join(str(value).split())  # newlines would leave the quote block
    if len(text) > limit:
        text = text[:limit] + " (truncated)"
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(text):
        out.append(_plain(text[pos:m.start()]))
        out.append("`" + m.group(1) + "`")
        pos = m.end()
    out.append(_plain(text[pos:]))
    return "".join(out)


def code(value):
    """A value shown inside a code span, such as a file path."""
    text = " ".join(str(value).split()).replace("`", "'")
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


def build_comment(review):
    if review.get("unparsed"):
        return UNPARSED

    lines = ["### Gemini Code Review", ""]
    lines += [f"**Summary:** {inert(review.get('summary', '(no summary)'))}", ""]

    issues = review.get("issues") or []
    body = []
    if issues:
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
    tail = [f"**Overall: {icon} {inert(assessment.replace('_', ' ').title(), 40)}**", "", *_FOOTER]

    # Add whole blocks while they fit, so a cut never lands inside one.
    size = len("\n".join(lines + tail))
    for block in body:
        if isinstance(block, str):
            block = [block]
        piece = "\n".join(block)
        if size + len(piece) + 1 > COMMENT_LIMIT:
            lines += ["*Further findings were cut to fit GitHub's comment limit.*", ""]
            break
        lines += block
        size += len(piece) + 1
    return "\n".join(lines + tail)


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        print(build_comment(json.load(f)))
