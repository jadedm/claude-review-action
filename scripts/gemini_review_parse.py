#!/usr/bin/env python3
"""Turns a Gemini generateContent response into the review JSON (#8).

Gemini is asked for a JSON review, and when it quotes code from the diff it
copies backslashes as they appear: `\\d`, `\\w`, `\\s`, `C:\\Users`. Those are not
valid JSON escapes, so a plain json.loads raised and the job failed after the
review had already been written. This repairs exactly those escapes and keeps
every valid one. A reply that still cannot be read, or that is JSON of the wrong
shape, is reported as unreadable: the raw text goes to the job log only, never to
the pull request, because the model read untrusted PR text and its output could
carry mentions, links or Markdown that would be posted under the bot's name.

    python3 gemini_review_parse.py <api-response.json> <review-out.json>

Exit 0 with a validated review written. Exit 2 with {"unparsed": true} written
when the reply is unreadable (the caller posts a fixed notice and fails the job).
Exit 1 when the API returned an error or no reply at all. Standard library only.
"""
import json
import re
import sys

# A valid JSON escape is \" \\ \/ \b \f \n \r \t or \u plus four hex digits.
# Matched first, so an escaped backslash ("\\\\") is consumed as one unit and
# the letter after it is never mistaken for the start of an escape.
_ESCAPE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|\\')


def repair_escapes(text):
    """Double every backslash that does not begin a valid JSON escape."""
    return _ESCAPE.sub(lambda m: m.group(0) if len(m.group(0)) > 1 else "\\\\", text)


def strip_fence(text):
    """Remove a surrounding Markdown code fence, if the model added one."""
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", t, re.S)
    return m.group(1) if m else t


def text_or_number(value):
    """A string or an integer. JSON true/false load as bool, a subclass of int,
    and would be posted as "True" or "False", so they are refused."""
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def well_formed(review):
    """Whether `review` has the shape the comment builder reads.

    Types only, not values. Gemini sometimes answers with a severity outside the
    prompt's list ("minor"), and the comment builder has always shown those with
    a neutral icon; rejecting them would suppress reviews that used to post."""
    if not isinstance(review, dict) or not isinstance(review.get("summary"), str):
        return False  # a reply with no summary, such as {}, is not a review
    if not isinstance(review.get("overall_assessment", "comment"), str):
        return False
    issues = review.get("issues", [])
    positives = review.get("positives", [])
    if not isinstance(issues, list) or not isinstance(positives, list):
        return False
    if not all(isinstance(p, str) for p in positives):
        return False
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get("severity", "low"), str):
            return False
        if not all(text_or_number(issue.get(k, "")) for k in ("file", "line", "category", "description", "suggestion")):
            return False
    return True


def parse_review(text):
    """The review as a dict, or None if it is not readable JSON of the right shape.

    The repair is tried only after a plain parse fails. A lone backslash just
    before a closing quote reads as an escaped quote either way, so that reply
    stays unreadable rather than being guessed at."""
    if not isinstance(text, str):
        return None
    body = strip_fence(text)
    for candidate in (body, repair_escapes(body)):
        try:
            value = json.loads(candidate)
        except (ValueError, RecursionError):  # ValueError covers JSONDecodeError and, on 3.11+, oversized integers
            continue
        return value if well_formed(value) else None
    return None


def reply_text(envelope):
    """The model's text from an API response, or raise ValueError."""
    if "error" in envelope:
        raise ValueError(f"Gemini API error: {envelope['error'].get('message', envelope['error'])}")
    try:
        return envelope["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        reason = (envelope.get("candidates") or [{}])[0].get("finishReason", "no candidates")
        raise ValueError(f"Gemini returned no review text ({reason})")


def main(src, dst):
    with open(src) as f:
        envelope = json.load(f)
    try:
        text = reply_text(envelope)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    review = parse_review(text)
    if review is None:
        print("WARNING: Gemini's reply is not a readable review. Raw reply, for the log only:")
        print(text)
        with open(dst, "w") as f:
            json.dump({"unparsed": True}, f)
        return 2
    with open(dst, "w") as f:
        json.dump(review, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
