#!/usr/bin/env python3
"""Whether a path from a pull request's checkout may be read into a prompt (#29, #35).

A pull request controls its files, symlinks included. The consumer checkout
can hold the job's token in .git/config, and a self-hosted runner has other
files a link could point at. So a path is read only if it is not a symlink,
its real location is inside the checkout, and no part of it is a `.git`
folder, compared without case because macOS runners ignore case.

    python3 safe_path.py <path>    # exit 0 if it may be read as a file;
                                   # else exit 1 and print why on stdout

`readable()` is the same rule for code that imports it (gemini_chunks.py).
Run from the checkout's root. Standard library only.
"""
import os
import sys


def readable(path):
    """Why a path must not be read, or None when it may be."""
    if os.path.islink(path):
        return "a symlink, not read"
    root = os.path.realpath(".")
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return "outside the checkout, not read"
    if any(part.casefold() == ".git" for part in os.path.relpath(real, root).split(os.sep)):
        return "inside .git, not read"
    return None


def main(path):
    reason = readable(path)
    if reason is None and not os.path.isfile(path):
        reason = "missing or not a regular file"
    if reason:
        print(reason)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
