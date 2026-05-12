# Code Review Prompt — Base

Review this pull request thoroughly. **Always read the `CLAUDE.md` file in the consuming repo root** (and any sub-directory CLAUDE.md files) for project-specific conventions before reviewing.

## Output Format

- Rate every finding by severity: **critical**, **high**, **medium**, **minor**.
- Include the file path and line number for each finding.
- Use inline review comments for specific code issues.
- End with a "What's Well Done" section calling out good patterns.
- Be concise but thorough — skip obvious/trivial observations.

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
