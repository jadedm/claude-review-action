# claude-review-action

Reusable GitHub Actions workflows for AI code review across Tikiti repositories. Hosts two parallel reviewers:

- **Claude** (via `anthropics/claude-code-action@v1`) — OAuth-authed, posts inline review comments and a top-level summary.
- **Gemini** (via the Gemini 2.5 API) — posts a single comment with severity-tagged findings as JSON-derived markdown.

Both reviewers read this repo's `prompts/base.md` (universal rules) and a stack-specific prompt (e.g. `prompts/nestjs-fastify-drizzle.md`), and the consuming repo's `CLAUDE.md` for project conventions.

## Usage in a consumer repo

Create two thin wrappers in the consuming repo's `.github/workflows/`:

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

jobs:
  review:
    uses: tikiti-technologies/claude-review-action/.github/workflows/claude-review.yml@v1
    with:
      stack: nestjs-fastify-drizzle
      runner: '["self-hosted","aws","spot"]'
      slack_notify: true
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

jobs:
  review:
    uses: tikiti-technologies/claude-review-action/.github/workflows/gemini-review.yml@v1
    with:
      stack: nestjs-fastify-drizzle
      runner: '["self-hosted","aws","spot"]'
      slack_notify: true
    secrets:
      gemini_api_key:    ${{ secrets.GEMINI_API_KEY }}
      slack_webhook_url: ${{ secrets.SLACK_WEBHOOK_URL }}
```

That's it. Updates to the prompts in this central repo propagate to every consumer on the next PR.

## Inputs

### Claude (`claude-review.yml`)

| Input | Type | Default | Description |
|---|---|---|---|
| `stack` | string | (required) | Prompt template name in `prompts/<stack>.md` |
| `runner` | string (JSON) | `"ubuntu-latest"` | `runs-on` value, JSON-encoded |
| `ref` | string | `v1` | Pinned ref of this central repo for prompts |
| `slack_notify` | boolean | `false` | Post a Slack message on completion |
| `extra_prompt` | string | `''` | Repo-specific addendum appended after the stack prompt |

### Gemini (`gemini-review.yml`)

Same inputs as above plus:

| Input | Type | Default | Description |
|---|---|---|---|
| `model` | string | `gemini-2.5-flash` | Gemini model ID |
| `max_total_lines` | number | `5000` | Cap on total source lines sent to Gemini |
| `max_per_file` | number | `1000` | Skip files larger than this |
| `max_files` | number | `20` | Cap on file count |

## Secrets required

| Workflow | Secret | Source |
|---|---|---|
| Claude | `claude_oauth_token` | Anthropic OAuth (Claude.ai → Account → Code) |
| Gemini | `gemini_api_key` | Google AI Studio |
| Either | `slack_webhook_url` (optional) | Slack incoming webhook |

## Adding a new stack

1. Create `prompts/<stack-name>.md` with stack-specific review conventions.
2. Reference it as `stack: <stack-name>` in consuming repos.
3. Optionally cut a new tag (`v1.x.0`) so consumers can pin to it.

`base.md` covers universal rules (security, correctness, code quality, perf, maintainability) and is always loaded first.

## Versioning

Consumers pin to a tag. Two conventions:

- **`@v1` — floating major (recommended for most repos).** The `v1` tag is moved forward to whichever commit is the latest in the 1.x line. Consumers automatically pick up prompt updates, bug fixes, and non-breaking input additions on their next PR. Breaking changes never land here — they go on `v2` instead.
- **`@v1.0.0` — strict pin.** Locks the consumer to that exact commit. Use when a repo needs guaranteed determinism (e.g. compliance audits, frozen review criteria).
- **`@main` — bleeding edge.** Not recommended outside of dogfooding within this central repo.

When cutting a release: tag both the strict (e.g. `v1.2.3`) AND fast-forward the floating (`v1`) to the same commit. Breaking prompt changes (different output format, removed inputs, etc.) require a new major — tag `v2.0.0` and create a new `v2` floating tag; leave `v1` untouched.

### Caller permissions in consuming repos

Reusable workflows in GitHub Actions are capped by the caller's `permissions:` block — the called workflow can only equal or restrict the caller's grants, never broaden them. **Each consuming workflow must declare its own `permissions:` block at the workflow or job level**, even though the reusable workflow already declares them internally. Example for the Claude wrapper:

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

Without these, the effective `GITHUB_TOKEN` in the reusable workflow falls back to the repo's default (typically read-only), and the actual review-posting / Slack-notify / OIDC steps silently fail.

## Layout

```
.
├── .github/workflows/
│   ├── claude-review.yml    # reusable workflow (workflow_call)
│   └── gemini-review.yml    # reusable workflow (workflow_call)
├── actions/
│   └── slack-notify/
│       └── action.yml       # composite action used by both reviewers
└── prompts/
    ├── base.md              # universal review rules (always loaded)
    └── nestjs-fastify-drizzle.md
```
