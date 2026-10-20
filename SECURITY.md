# Security

These workflows run with the caller's API keys and a GitHub token, while a model reads pull request content that anyone who opens a pull request controls. A way for that content to reach a secret, write to the repo, or spend the caller's quota is a vulnerability.

## Reporting

Report it privately through [GitHub's private vulnerability reporting](https://github.com/jadedm/pr-review-workflows/security/advisories/new). Do not open a public issue.

Include the workflow and step, what an attacker controls, and what they get. A proof of concept against your own test repo helps.

You can expect a first reply within a week. A fix ships as a new `v1.x.y` tag with `v1` moved to it, and the advisory is published once callers on `@v1` have it.

## Supported versions

Only the latest `v1` release. A caller pinned to an older tag or SHA gets a fix by moving the pin.

## Out of scope

- What the model says in a review, unless it leaks a secret or posts something the comment renderer should have neutralised.
- A caller that switches to `pull_request_target`, or allows broad commands in its own `.claude/settings.json`. The README explains why both are unsafe.
- Issues in `anthropics/claude-code-action` or the Gemini API themselves. Report those upstream.
