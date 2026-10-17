#!/usr/bin/env python3
"""Fetch a caller file from the pull request's base commit, not the pull request (#37).

    python3 base_file.py <owner/repo> <pr number> <path> <out file>

The review rules file (extra_prompt_path) used to be read from the pull
request's checkout, so a pull request could rewrite the instructions its own
review followed. This reads it at the base commit through the GitHub API
instead: a change to the rules is reviewed as an ordinary change and takes
effect from the next pull request.

Exit 0 and write <out file> when the file is there, logging the commit and
its size. When the base has no file at that path (the pull request that adds
it, or one retargeted to a branch without it) the default branch's copy is
used instead, so retargeting cannot drop the rules. Exit 0 and write nothing
(printing a ::warning::) only when neither has it. Anything
else exits 1, so a review never runs quietly without its rules: a path that is
not a plain repo path, a folder or link that is not a file, a file too large,
and any API failure (the pull request lookup included).

Needs GH_TOKEN (the job token) and GITHUB_API_URL. Standard library only.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 200_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib on 3.9 keeps the Authorization header across a redirect, even to
    another host; these API calls are never redirected, so refuse any."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def api(path):
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with OPENER.open(req, timeout=30) as resp:
        return json.load(resp)


def skip(path, why):
    print(f"::warning::extra_prompt_path '{path}' skipped: {why}")
    return 0


def fail(path, why):
    print(f"::error::extra_prompt_path '{path}': {why}")
    return 1


def contents(repo, path, ref):
    """The contents API's answer for path at ref, or None when there is no such path."""
    try:
        return api(f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main(repo, pr, path, out):
    if os.path.lexists(out):
        os.remove(out)  # a reused runner must never read an earlier run's rules
    parts = path.split("/")
    if path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return fail(path, "not a plain path inside the repository")
    base = api(f"/repos/{repo}/pulls/{int(pr)}")["base"]["sha"]
    item, where = contents(repo, path, base), f"the base commit {base}"
    if item is None:
        default = api(f"/repos/{repo}")["default_branch"]
        item, where = contents(repo, path, default), f"the default branch ({default})"
    if item is None:
        return skip(path, f"not on the base branch at {base[:7]} nor on the default branch")
    if isinstance(item, list) or item.get("type") != "file":
        kind = "a folder" if isinstance(item, list) else (item.get("type") or "not a file")
        return fail(path, f"{kind} on {where}, not a file")
    if item.get("encoding") != "base64" or not isinstance(item.get("size"), int) or item["size"] > MAX_BYTES:
        return fail(path, f"too large or unreadable on {where} (size {item.get('size')})")
    data = base64.b64decode(item["content"])
    with open(out, "wb") as f:
        f.write(data)
    print(f"Read '{path}' from {where} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) != 5:
            raise ValueError("usage: base_file.py <owner/repo> <pr number> <path> <out file>")
        sys.exit(main(*sys.argv[1:5]))
    except (OSError, KeyError, ValueError, TypeError) as e:  # OSError covers URLError and timeouts
        print(f"::error::could not read the review rules from the base branch: {e}")
        sys.exit(1)
