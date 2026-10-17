#!/usr/bin/env python3
"""Fetch a caller file from the pull request's base commit, not the pull request (#37).

    python3 base_file.py <owner/repo> <pr number> <path> <out file>

The review rules file (extra_prompt_path) used to be read from the pull
request's checkout, so a pull request could rewrite the instructions its own
review followed. This reads it at the base commit through the GitHub API
instead: a change to the rules is reviewed as an ordinary change and takes
effect from the next pull request.

Exit 0 and write <out file> when the file is there, logging the base commit
and its size. Exit 0 and write nothing (printing a ::warning::) only when the
base has no file at that path, as on the pull request that adds it. Anything
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


def api(path):
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def skip(path, why):
    print(f"::warning::extra_prompt_path '{path}' skipped: {why}")
    return 0


def fail(path, why):
    print(f"::error::extra_prompt_path '{path}': {why}")
    return 1


def main(repo, pr, path, out):
    if os.path.lexists(out):
        os.remove(out)  # a reused runner must never read an earlier run's rules
    parts = path.split("/")
    if path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return fail(path, "not a plain path inside the repository")
    base = api(f"/repos/{repo}/pulls/{int(pr)}")["base"]["sha"]
    quoted = urllib.parse.quote(path)
    try:
        item = api(f"/repos/{repo}/contents/{quoted}?ref={base}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return skip(path, f"not on the base branch at {base[:7]} (the pull request may be adding it)")
        raise
    if isinstance(item, list) or item.get("type") != "file":
        kind = "a folder" if isinstance(item, list) else (item.get("type") or "not a file")
        return fail(path, f"{kind} on the base branch, not a file")
    if item.get("encoding") != "base64" or item.get("size", 0) > MAX_BYTES:
        return fail(path, f"too large to read from the base branch ({item.get('size')} bytes)")
    data = base64.b64decode(item["content"])
    with open(out, "wb") as f:
        f.write(data)
    print(f"Read '{path}' from the base commit {base} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(*sys.argv[1:5]))
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"::error::could not read the review rules from the base branch: {e}")
        sys.exit(1)
