# claude-review-action

Reusable GitHub Actions workflows for running AI code review on every pull request. Two parallel reviewers, severity-rated findings, optional Slack notification.

- **Claude** — via [`anthropics/claude-code-action@v1`](https://github.com/anthropics/claude-code-action). OAuth-authed; posts inline review comments and a top-level summary.
- **Gemini** — direct call to the Gemini 2.5 API. Posts a single comment with severity-tagged findings rendered from a JSON response.

Both reviewers load:

1. **`prompts/base.md`** — universal rules (security, correctness, code quality, performance, maintainability).
2. **`prompts/<stack>.md`** — stack-specific conventions (e.g. `nestjs-fastify-drizzle.md`).
3. The consuming repo's **`CLAUDE.md`** for project-specific guidance.
4. **Optional repo-specific addendum** via `extra_prompt` (inline) or `extra_prompt_path` (file path in the consumer repo) — see below.

Originally built for internal CI at Tikiti; published for reuse and as a worked example of the pattern. Not community-supported (no inbound issue triage commitment), but PRs welcome.

## Why two reviewers?

Different models catch different things. Claude is excellent at threading multi-file context and producing a careful narrative review; Gemini is fast and consistent for structured-output / bullet-list-of-issues style. Running both costs roughly one Claude subscription seat + a handful of Gemini API calls per PR, and the disagreement between them is often more informative than either alone — Claude routinely notes which of Gemini's findings it agrees with, and vice versa, when both have already commented on a PR. Drop either by removing its wrapper.

## Usage in a consumer repo

Create two thin wrappers in the consuming repo's `.github/workflows/`.

### `claude.yml`

```yaml
name: Claude Code

on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]

# Required — reusable workflows can only restrict, not elevate, permissions.
# Without this block, posting comments and acquiring an OIDC token will fail.
permissions:
  actions: read
  contents: read
  pull-requests: write
  issues: write
  id-token: write

jobs:
  review:
    uses: jadedm/claude-review-action/.github/workflows/claude-review.yml@v1
    with:
      stack: nestjs-fastify-drizzle
      runner: '"ubuntu-latest"'
      slack_notify: true
      # Optional — repo-specific conventions appended to the public stack prompt
      extra_prompt_path: .github/review-extras.md
    secrets:
      claude_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      slack_webhook_url:  ${{ secrets.SLACK_WEBHOOK_URL }}
```

### `gemini.yml`

```yaml
name: Gemini Code Review

on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    uses: jadedm/claude-review-action/.github/workflows/gemini-review.yml@v1
    with:
      stack: nestjs-fastify-drizzle
      runner: '"ubuntu-latest"'
      slack_notify: true
      extra_prompt_path: .github/review-extras.md
    secrets:
      gemini_api_key:    ${{ secrets.GEMINI_API_KEY }}
      slack_webhook_url: ${{ secrets.SLACK_WEBHOOK_URL }}
```

That's the entire integration. Updates to the prompts in this central repo propagate to every consumer on the next PR.

### Mentioning the bots

Both reviewers re-trigger when their handle is mentioned in a PR comment:

- `@claude` — re-runs Claude. Also triggers on `pull_request_review_comment` events so you can prompt it inline from a review thread.
- `@gemini` — re-runs Gemini.

The `if:` guard inside each reusable workflow handles the routing — there's no extra wiring on the consumer side.

## Layering repo-specific conventions

The stack prompts (`prompts/<stack>.md`) describe the **stack**, not your specific codebase. To layer in repo-specific conventions (custom guard names, internal paths, threshold tunings, project-specific lint rules), use one of:

- **`extra_prompt: <inline string>`** — for short notes. Multi-line YAML strings get awkward beyond a paragraph.
- **`extra_prompt_path: <path>`** — points at a markdown file inside the consumer repo (e.g. `.github/review-extras.md`). Read at workflow-run time and appended under a "Repo-specific notes" heading. Cleanest for long-form additions; the file lives in the consumer repo so a PR that changes both the code conventions and the review prompt for them is atomic.

If both are set, file contents render first, then the inline string — both under one "Repo-specific notes" section.

Example `.github/review-extras.md` in a consumer repo:

```markdown
## Project-specific conventions

- Guards: `JwtAuthGuard`, `ProducerVerifiedGuard`, `AdminRoleGuard`
- Typed exceptions live in `@app/common`; never raw `HttpException`
- Pagination `_meta` always includes `cursor` (not just `page`)
- Throttler is global at 100 req/60s; flag endpoints needing a tighter cap
```

## Inputs

### Claude (`claude-review.yml`)

| Input | Type | Default | Description |
|---|---|---|---|
| `stack` | string | (required) | Prompt template name in `prompts/<stack>.md` |
| `runner` | string (JSON) | `"ubuntu-latest"` | `runs-on` value, JSON-encoded |
| `ref` | string | `v1` | Pinned ref of this central repo for prompts |
| `slack_notify` | boolean | `false` | Post a Slack message on completion |
| `extra_prompt` | string | `''` | Inline repo-specific addendum |
| `extra_prompt_path` | string | `''` | Path (in consumer repo) to a markdown file appended after the stack prompt |

### Gemini (`gemini-review.yml`)

All Claude inputs above, plus:

| Input | Type | Default | Description |
|---|---|---|---|
| `model` | string | `gemini-2.5-flash` | Gemini model ID |
| `max_total_lines` | number | `5000` | Cap on total source lines sent to Gemini |
| `max_per_file` | number | `1000` | Skip files larger than this |
| `max_files` | number | `20` | Cap on file count |

## Secrets required

| Workflow | Secret | Source |
|---|---|---|
| Claude | `claude_oauth_token` | Anthropic OAuth (Claude.ai → Account → Code → OAuth Token) |
| Gemini | `gemini_api_key` | [Google AI Studio](https://aistudio.google.com/apikey) |
| Either | `slack_webhook_url` (optional) | Slack Incoming Webhook |

Set on the consumer repo via:

```bash
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo <owner>/<repo>
gh secret set GEMINI_API_KEY          --repo <owner>/<repo>
gh secret set SLACK_WEBHOOK_URL       --repo <owner>/<repo>
```

> **Use repo-level secrets — not org-level — for private repos.** On the GitHub **Free** org plan, organisation-level Actions secrets are **not available to private repositories**: `${{ secrets.X }}` silently resolves to empty in the workflow (the job runs but the API call 401/403s). Set secrets on the repo (as above) or upgrade to Team/Enterprise.
>
> **`gh secret set` without `--body` reads from stdin** (an interactive paste prompt). In a non-interactive shell it sets an **empty** value with no error and the timestamp still updates. Pass `--body "<value>"`, pipe the value, or use the repo Settings UI.

**Claude auth — install the App *and* set the token.** Install the [Claude GitHub App](https://github.com/apps/claude) on the consumer repo (it handles posting), **and** set `claude_oauth_token` (generate via `claude setup-token`). The App alone is **not sufficient**: it may cover a small one-off `pull_request` review, but the `@claude` comment trigger and larger/subsequent reviews use the **direct Anthropic API**, which requires the token (`Environment variable validation failed: … ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / WIF is required`). Gemini likewise requires `gemini_api_key`.

## Adding a new stack

1. Create `prompts/<stack-name>.md` with stack-specific review conventions. Keep it project-agnostic — repo-specific things go in `extra_prompt` / `extra_prompt_path` on each consumer.
2. Reference it as `stack: <stack-name>` in consuming repo wrappers.
3. Optionally cut a new tag (`v1.x.0`) so consumers can pin to it.

`base.md` covers universal rules and is always loaded first.

## Versioning

Consumers pin to a tag. Three conventions:

- **`@v1` — floating major (recommended for most repos).** The `v1` tag is moved forward to whichever commit is the latest in the 1.x line. Consumers automatically pick up prompt updates, bug fixes, and non-breaking input additions on their next PR. Breaking changes never land here — they go on `v2` instead.
- **`@v1.2.3` — strict pin.** Locks the consumer to that exact commit. Use when a repo needs guaranteed determinism (e.g. compliance audits, frozen review criteria).
- **`@main` — bleeding edge.** Not recommended outside of dogfooding within this central repo.

When cutting a release: tag the strict version (e.g. `v1.2.3`) AND fast-forward the floating major (`v1`) to the same commit. Breaking prompt changes (different output format, removed inputs, etc.) require a new major — tag `v2.0.0` + create a new `v2` floating tag; leave `v1` untouched.

## Caller permissions

Reusable workflows in GitHub Actions are capped by the caller's `permissions:` block — the called workflow can only equal or restrict the caller's grants, never broaden them. **Each consuming workflow must declare its own `permissions:` block** at the workflow or job level, even though this reusable workflow declares them internally.

For Claude:

```yaml
permissions:
  actions: read
  contents: read
  pull-requests: write
  issues: write
  id-token: write
```

For Gemini:

```yaml
permissions:
  contents: read
  pull-requests: write
```

Without these, the effective `GITHUB_TOKEN` in the reusable workflow falls back to the repo's default (typically read-only on private repos), and the actual review-posting / Slack-notify / OIDC steps silently fail.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Gemini run shows **0 jobs** / *"This run likely failed because of a workflow file issue"* | Invalid YAML in the called reusable — e.g. a heredoc body dedented below its `run: \|` block ends the block scalar early | Validate with a real YAML parser (`python3 -c "import yaml;yaml.safe_load(open('f'))"`); keep multi-line content in a `prompts/*.md` file and `cat` it, not an inline heredoc |
| Reviewer job runs but the API returns **401/403 "unregistered callers"** and the step's env shows the key **blank** (not `***`) | `${{ secrets.X }}` resolved to empty — most often an **org-level secret on a GitHub Free-plan org, which is not available to private repositories** | Set the secret at the **repo** level: `gh secret set X --repo <owner>/<repo> --body "…"` (or upgrade the org to Team/Enterprise) |
| Secret "set" but still empty at runtime; its timestamp updates each attempt | `gh secret set` **without `--body`** reads the value from **stdin** (interactive paste). In a non-interactive shell it stores an **empty** value with no error | Pass `--body "<value>"`, pipe the value, or use the repo Settings UI (interactive in a real terminal is fine) |
| Claude step fails: **"Claude Code is not installed on this repository"** | The Claude GitHub App isn't installed on the repo | Install <https://github.com/apps/claude> on the repo (you still also need `claude_oauth_token`) |
| Claude fails: **"Either ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or WIF is required"** | App auth alone is insufficient — the review's model call goes through the direct Anthropic API | Set a real `claude_oauth_token` repo secret (`claude setup-token`); the App is also required but not sufficient on its own |

## Layout

```
.
├── .github/workflows/
│   ├── claude-review.yml    # reusable workflow (workflow_call)
│   └── gemini-review.yml    # reusable workflow (workflow_call)
├── actions/
│   └── slack-notify/
│       └── action.yml       # composite action used by both reviewers
├── prompts/
│   ├── base.md              # universal review rules (always loaded)
│   └── nestjs-fastify-drizzle.md
├── LICENSE
└── README.md
```

## Writeup

Background, design choices, and operational notes: [manishj.com/garden/running-ai-code-review-ci-gemini-claude](https://manishj.com/garden/running-ai-code-review-ci-gemini-claude)

## License

[MIT](./LICENSE)

---

**Built by [Manish Jadhav](https://manishj.com)**, engineer & technical consultant.

Need something like this designed or built? [Inoltro](https://inoltro.ai) is my studio.
