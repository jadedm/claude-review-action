# Code Review Prompt — Base

Review this pull request thoroughly. **Always read the `CLAUDE.md` file in the consuming repo root** (and any sub-directory CLAUDE.md files) for project-specific conventions before reviewing.

## Output Format

- Rate every finding by severity: **critical**, **high**, **medium**, **minor**.
- Include the file path and line number for each finding.
- Post findings on changed lines as inline review comments when your tools allow it (see below); otherwise report them in the output format given at the end of your prompt.
- End with a "What's Well Done" section calling out good patterns.
- Be concise but thorough — skip obvious/trivial observations.

### Inline comments (when you have the `mcp__github_inline_comment__create_inline_comment` tool)

Inline comments are expected in this review, and they are allowed alongside the single summary comment. The instructions earlier in this prompt about communicating only by updating one comment with `mcp__github_comment__update_claude_comment` apply to top-level comments: keep exactly one summary comment, and also post inline comments with the tool below.

1. If the tool's schema is not loaded, load it with ToolSearch (`select:mcp__github_inline_comment__create_inline_comment`).
2. For each finding on a line this pull request adds or changes, call it once with `path`, `line` (or `startLine` and `line` for a range), `side: "RIGHT"`, `confirmed: true`, and a `body` that gives the severity, the problem and the fix.
3. Only lines inside the diff can take a comment. If a call returns an error, keep the finding in the summary and move on; do not retry the same line.
4. The summary still lists every finding, one line each with its file and line, so nothing depends on an inline call succeeding.

## Universal Review Areas

### Security
- No hardcoded secrets, API keys, tokens, or credentials in code or fixtures.
- All non-public endpoints / mutations protected by auth guards or equivalent.
- Input validation on every external boundary (HTTP, queue messages, webhooks). Never trust raw request data.
- Parameterized DB queries — no string concatenation in SQL or NoSQL filters.
- No sensitive data (passwords, tokens, PII, full card numbers) written to logs.
- Outbound calls validate TLS and check response shapes before consuming.
- Rate limiting in place for authentication, OTP, password reset, and other abuse-prone endpoints.

### Correctness & Reliability
- Errors are surfaced with explicit codes and messages, not swallowed.
- Async code awaits properly — no floating promises or unhandled rejections.
- Idempotency where the operation could be retried (payments, webhooks, queue jobs).
- Transaction boundaries cover multi-step writes that must succeed or fail together.
- Resource cleanup in finally blocks where applicable (file handles, DB transactions).

### Code Quality
- No `any` / `unknown` escape hatches without justification.
- No dead code or commented-out blocks.
- Variable and function names communicate intent.
- Imports are minimal and ordered consistently.
- Comments explain *why*, not *what*. Remove obvious comments.

### Performance
- No accidental N+1 queries or repeated network calls in loops.
- Avoid blocking the event loop with synchronous I/O.
- Cache appropriately, invalidate explicitly.

### Maintainability
- New code follows the existing module / file organisation of the project.
- New public APIs documented in their respective DTOs / OpenAPI / interfaces.
- Tests added or updated for changed behaviour where the project has a test suite.

---

The sections above are stack-neutral. The next sections (loaded from a stack-specific prompt) cover framework conventions for this repository.
