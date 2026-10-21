## Inline comments (when you have the `mcp__github_inline_comment__create_inline_comment` tool)

Inline comments are expected in this review, and they are allowed alongside the single summary comment. The instructions earlier in this prompt about communicating only by updating one comment with `mcp__github_comment__update_claude_comment` apply to top-level comments: keep exactly one summary comment, and also post inline comments with the tool below.

1. If the tool's schema is not loaded, load it with ToolSearch (`select:mcp__github_inline_comment__create_inline_comment`).
2. For each finding on a line this pull request adds or changes, call it once with `path`, `line` (or `startLine` and `line` for a range), `side: "RIGHT"`, `confirmed: true`, and a `body` that gives the severity, the problem and the fix.
3. Only lines inside the diff can take a comment. If a call returns an error, keep the finding in the summary and move on; do not retry the same line.
4. The summary still lists every finding, one line each with its file and line, so nothing depends on an inline call succeeding.
