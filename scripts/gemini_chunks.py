#!/usr/bin/env python3
"""Splits a pull request into Gemini review calls (#30).

    python3 gemini_chunks.py <out_dir>

Run from the consumer checkout. Reads the changed-file lists and the diff the
earlier steps wrote to /tmp, and the caps from the environment (MAX_FILES,
MAX_TOTAL_LINES, MAX_PER_FILE, MAX_DIFF_LINES, MAX_PAYLOAD_BYTES, MAX_CHUNKS).
Writes <out_dir>/<n>/code.txt, diff.txt and files.json for each call, and
/tmp/review-coverage.json listing what no call will see (#29). Prints the
number of calls.

Each changed file and its diff hunk travel together. Files are sorted by path
so neighbours tend to share a call, and each goes into the first call with
room for it (first fit). With MAX_CHUNKS=1 that is the single call and the
skip-and-continue behaviour of #29, with the same reasons.

A pull request controls its file names, contents and symlinks, so a symlink or
a path resolving outside the checkout or into .git is never read.

Standard library only.
"""
import io
import json
import os
import pathlib
import sys

MAX_CHUNKS_LIMIT = 5
CHANGED = "/tmp/changed-files.txt"
ALL_CHANGED = "/tmp/all-changed-files.txt"
DIFF = "/tmp/pr-diff.txt"
COVERAGE = "/tmp/review-coverage.json"


def lines_of(data):
    """Lines split on \\n only, as head -n splits them."""
    return io.BytesIO(data).readlines()


def read_list(path):
    return [line.rstrip(b"\n").decode("utf-8", "surrogateescape") for line in lines_of(pathlib.Path(path).read_bytes())]


def split_hunks(diff):
    """{path: hunk bytes}, plus the text before the first header under None.

    A hunk starts at a line beginning `diff --git `. Content lines in a diff
    start with +, - or a space, so they cannot start a hunk. The path is the
    b/ side, which is the name the files API gives a renamed file.
    """
    hunks, key, current = {}, None, []
    for line in lines_of(diff):
        if line.startswith(b"diff --git "):
            hunks[key] = hunks.get(key, b"") + b"".join(current)
            current = []
            text = line.decode("utf-8", "surrogateescape").rstrip("\n")
            key = text.split(" b/", 1)[1] if " b/" in text else text
        current.append(line)
    hunks[key] = hunks.get(key, b"") + b"".join(current)
    if not hunks[None]:
        del hunks[None]
    return hunks


def clip(data, max_lines, max_bytes):
    """data cut to whole lines and bytes, never splitting a UTF-8 character."""
    kept = b"".join(lines_of(data)[:max(max_lines, 0)])[:max(max_bytes, 0)]
    return kept.decode("utf-8", "ignore").encode("utf-8")


def readable(path):
    """Why a changed path must not be read, or None when it may be."""
    if os.path.islink(path):
        return "a symlink, not read"
    root = os.path.realpath(".")
    real = os.path.realpath(path)
    inside = real.startswith(root + os.sep) and not real.startswith(os.path.join(root, ".git") + os.sep)
    return None if inside else "outside the checkout, not read"


class Chunk:
    def __init__(self, caps):
        self.caps = caps
        self.files, self.code, self.diff = [], [], []
        self.lines = self.bytes = self.diff_lines = 0
        self.diff_cut = False

    def why_not(self, code_lines, code_bytes, has_file):
        """The cap a file would break here, in #29's order, or None."""
        c = self.caps
        if has_file and len([f for f in self.files if f["read"]]) >= c["files"]:
            return f"over the {c['files']}-file limit"
        if self.lines + code_lines > c["total_lines"]:
            return f"over the {c['total_lines']}-line total"
        if self.bytes + code_bytes > c["bytes"]:
            return f"over the {c['bytes']}-byte total"
        return None

    def diff_room(self):
        return self.caps["diff_lines"] - self.diff_lines, self.caps["bytes"] - self.bytes

    def add(self, name, code, hunk):
        """Place a file (code may be None) and as much of its hunk as fits."""
        room_lines, room_bytes = self.diff_room()
        if code is not None:
            room_bytes -= len(code)
            self.code.append(b"--- " + name.encode("utf-8", "surrogateescape") + b" ---\n" + code + b"\n\n")
            self.lines += len(lines_of(code))
            self.bytes += len(code)
        sent = clip(hunk, room_lines, room_bytes)
        self.diff_cut |= len(sent) < len(hunk)
        self.diff.append(sent)
        self.diff_lines += len(lines_of(sent))
        self.bytes += len(sent)
        self.files.append({"file": name, "read": code is not None})
        return len(lines_of(sent))


def caps_from_env():
    raw = os.environ.get("MAX_CHUNKS", "1")
    try:
        chunks = int(raw)
    except ValueError:
        chunks = None
    if chunks is None or str(chunks) != raw.strip() or not 1 <= chunks <= MAX_CHUNKS_LIMIT:
        sys.exit(f"::error::max_chunks must be a whole number from 1 to {MAX_CHUNKS_LIMIT}, got {raw!r}")
    return {
        "chunks": chunks,
        "files": int(os.environ["MAX_FILES"]),
        "total_lines": int(os.environ["MAX_TOTAL_LINES"]),
        "per_file": int(os.environ["MAX_PER_FILE"]),
        "diff_lines": int(os.environ["MAX_DIFF_LINES"]),
        "bytes": int(os.environ["MAX_PAYLOAD_BYTES"]),
    }


def plan(caps):
    """(chunks, omitted, diff_total_lines)."""
    changed = sorted(read_list(CHANGED))
    hunks = split_hunks(pathlib.Path(DIFF).read_bytes())
    diff_total = sum(len(lines_of(h)) for key, h in hunks.items() if key is None or key in changed)
    chunks, omitted = [Chunk(caps)], []

    def place(name, code, hunk):
        """First chunk with room, else a new one, else the reason it fits nowhere."""
        code_lines = len(lines_of(code)) if code is not None else 0
        code_bytes = len(code) if code is not None else 0
        too_big = Chunk(caps).why_not(code_lines, code_bytes, code is not None)
        if too_big:
            return too_big  # no call could hold it, so a new one would not help
        def hunk_room(chunk):
            lines_left, bytes_left = chunk.diff_room()
            return not hunk or (lines_left > 0 and bytes_left - code_bytes > 0)

        # A call with room for the file and its hunk; else a new call; else,
        # for a file that is read, any call with room for its code, its hunk
        # cut (as one call always did past the diff cap).
        for chunk in chunks:
            if chunk.why_not(code_lines, code_bytes, code is not None) is None and hunk_room(chunk):
                chunk.add(name, code, hunk)
                return None
        if len(chunks) < caps["chunks"]:
            chunks.append(Chunk(caps))
            return place(name, code, hunk)
        if code is None:
            return "not in the checkout, and past the diff cut"
        for chunk in chunks:
            no_room = chunk.why_not(code_lines, code_bytes, True)
            if no_room is None:
                chunk.add(name, code, hunk)
                return None
        if caps["chunks"] > 1:
            return f"over the {caps['chunks']}-call limit"
        return no_room

    if None in hunks:
        chunks[0].add("", None, hunks[None])
        chunks[0].files.pop()  # the text before the first header is no one's file

    for name in changed:
        hunk = hunks.get(name, b"")
        reason = readable(name)
        if reason:
            omitted.append({"file": name, "reason": reason})
            continue
        if not os.path.isfile(name):
            # Deleted, or added on an @gemini run that checked out the default branch (#16).
            # Its removal is in its hunk; a missing file with no hunk has nothing to send.
            reason = place(name, None, hunk) if hunk else None
            if reason:
                omitted.append({"file": name, "reason": reason})
            continue
        code = pathlib.Path(name).read_bytes()
        if len(lines_of(code)) > caps["per_file"]:
            omitted.append({"file": name, "reason": f"over {caps['per_file']} lines"})
            continue
        reason = place(name, code, hunk)
        if reason:
            omitted.append({"file": name, "reason": reason})
    return [c for c in chunks if c.files or c.diff], omitted, diff_total


def main(out_dir):
    caps = caps_from_env()
    chunks, omitted, diff_total = plan(caps)
    out = pathlib.Path(out_dir)
    for n, chunk in enumerate(chunks, 1):
        d = out / str(n)
        d.mkdir(parents=True, exist_ok=True)
        (d / "code.txt").write_bytes(b"".join(chunk.code))
        (d / "diff.txt").write_bytes(b"".join(chunk.diff))
        (d / "files.json").write_text(json.dumps([f["file"] for f in chunk.files]))
    sent = sum(c.diff_lines for c in chunks)
    filtered = len(read_list(ALL_CHANGED)) - len(read_list(CHANGED))
    coverage = {"omitted": omitted, "filtered": filtered, "diff_sent": sent, "diff_total": diff_total,
                "diff_cut": any(c.diff_cut for c in chunks) or sent < diff_total}
    pathlib.Path(COVERAGE).write_text(json.dumps(coverage))
    for n, c in enumerate(chunks, 1):
        print(f"Call {n}: {len(c.files)} files, {c.lines} lines, {c.bytes} bytes, diff {c.diff_lines} lines",
              file=sys.stderr)
    print(len(chunks))


if __name__ == "__main__":
    main(sys.argv[1])
