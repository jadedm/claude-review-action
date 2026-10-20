# pr-review-workflows

Two AI reviewers on every pull request, Claude and Gemini, from two short workflow files in your repo. Each posts severity-rated findings with file and line. There is nothing to host: the review runs in GitHub Actions, on your runner and your API keys.

It is for teams that already use GitHub Actions and want AI review whose source they can read, with the review rules kept in their own repo.

- **Claude** runs through [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) and posts inline comments plus a summary.
- **Gemini** is called directly (`gemini-4-argon` with thinking `high` by default) and posts one comment listing its findings by severity.

Use one or both. Each is its own file.

## Quickstart

About five minutes for both reviewers.

1. Install the [Claude GitHub App](https://github.com/apps/claude) on the repo.
2. Set the secrets on the repo. `claude setup-token` prints the Claude token; a Gemini key comes from [Google AI Studio](https://aistudio.google.com/apikey). Put each value in a shell variable first: `gh secret set` with an empty `--body` stores an empty secret without an error.

   ```bash
   gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo OWNER/REPO --body "$CLAUDE_TOKEN"
   gh secret set GEMINI_API_KEY --repo OWNER/REPO --body "$GEMINI_KEY"
   ```

3. Add `.github/workflows/claude.yml`:

   ```yaml
   name: Claude Code

   on:
     pull_request:
       types: [opened, synchronize, ready_for_review, reopened]
     issue_comment:
       types: [created]
     pull_request_review_comment:
       types: [created]

   # A called workflow gets at most the caller's permissions, so these are required.
   permissions:
     actions: read
     contents: read
     pull-requests: write
     issues: write
     id-token: write

   jobs:
     review:
       uses: jadedm/pr-review-workflows/.github/workflows/claude-review.yml@v1
       with:
         stack: python
       secrets:
         claude_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
   ```

4. Add `.github/workflows/gemini.yml`:

   ```yaml
   name: Gemini Code Review

   on:
     pull_request:
       types: [opened, synchronize, ready_for_review, reopened]
     issue_comment:
       types: [created]
     pull_request_review_comment:
       types: [created]

   # Without this block the token falls back to the repo default, often read-only.
   permissions:
     contents: read
     pull-requests: write

   jobs:
     review:
       uses: jadedm/pr-review-workflows/.github/workflows/gemini-review.yml@v1
       with:
         stack: python
       secrets:
         gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
   ```

5. Merge both files to the default branch, then open a pull request. claude-code-action skips a pull request whose `claude.yml` differs from the default branch's copy, so the pull request that adds the file gets a "Claude did not review" notice instead of a review.

`stack` picks a prompt from [`prompts/`](prompts): `python`, `nestjs-fastify-drizzle`, `nextjs-turborepo-shadcn`, `expo-router-nativewind` or `apps-script`. Comment `@claude` or `@gemini` on a pull request to run that reviewer again.

## What it does differently

Each point names the file that does it.

- **The review rules come from the base branch.** Your repo's rules file (`extra_prompt_path`) is read from the pull request's base commit (the base branch as the pull request records it) through the GitHub API, never from the pull request, so a pull request cannot rewrite the rules its own review follows. [`scripts/base_file.py`](scripts/base_file.py), called at `claude-review.yml:137` and `gemini-review.yml:313`.
- **Every third-party action is pinned to a commit.** Whoever controls an upstream repo can move its tags, and these steps hold your tokens. The pins are at `claude-review.yml:63`, `:78` and `:169`; [`.github/dependabot.yml`](.github/dependabot.yml) opens a pull request each week when a pinned action has a newer release.
- **Model output is made safe before it is posted.** Gemini reads the pull request, so its reply is treated as untrusted: no @-mentions, links, images, HTML or bidirectional control characters reach the comment, and each field is capped. [`scripts/gemini_review_comment.py`](scripts/gemini_review_comment.py), lines 32 to 66.
- **Large pull requests are split, and the comment says what was not reviewed.** A pull request over the per-call limits is split into up to `max_chunks` parallel calls whose findings merge into one comment. Files that fit no call are listed under "Not reviewed" with the reason. [`scripts/gemini_chunks.py`](scripts/gemini_chunks.py), [`scripts/gemini_review_merge.py`](scripts/gemini_review_merge.py), and the list at `gemini_review_comment.py:91-127`.
- **When Claude does not review, the pull request says so.** claude-code-action can skip a review and still show a green check. This posts one comment naming the cause and a warning in the run, and the Slack message says "Not reviewed". `claude-review.yml:247`.
- **The Claude reviewer is denied writes.** File edits, commits, pushes, tags and `gh` are denied, and so are reads of the places on a runner known to hold credentials. These are deny rules for named tools and paths, not a sandbox; see Security below. `claude-review.yml:211-213`.
- **Strangers cannot spend your quota.** On a public repo only owners, members and collaborators can start a review by comment. `claude-review.yml:42-50`, `gemini-review.yml:95-103`.
- **Two model families.** Claude and Gemini review independently. They miss different things, and where they disagree is often the most useful part of the review.

The workflow files are in [`.github/workflows/`](.github/workflows).

## Compared with other tools

- **[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action)** is the engine the Claude reviewer runs on. On its own it is a general agent: you write the prompt, the permissions and the guard. This repo adds the review prompts, the base-branch rules file, the write and credential deny list, the skip notice and the second model.
- **[PR-Agent](https://github.com/The-PR-Agent/pr-agent)** (MIT) does more than review: pull request descriptions, suggested changes, and GitLab, Bitbucket and Azure DevOps as well as GitHub. It runs one model with a fallback. At the commit we read (`9302fa5`), its GitHub docs used `@main` and suggested `pull_request_target` for forks, which gives a fork's pull request your secrets.
- **[Kodus](https://github.com/kodustech/kodus-ai)** (AGPL-3.0, with paid code under `ee/`) is an app you self-host or use as a service, with an API, workers and two databases. It reviews only the commits since the last review, checks findings again before posting, and supports several git hosts. It posts the model's text unchanged.

What this repo does not do yet: review only the commits since the last review, edit one summary comment in place instead of posting a new one each run, check each finding against the code before posting, offer committable suggestions, or retry on another provider when one fails. It supports GitHub only.

## Costs

- **Claude** uses the Claude subscription behind the OAuth token; reviews count against that plan's usage limits.
- **Gemini** bills per token to the key's Google project. Measured on ten of our own merged pull requests in October 2026, `gemini-4-argon` with thinking `high` cost $2.26 in total at Google's listed rates, about $0.23 a review, with a median of 78 seconds a call. `model: gemini-3.8-flash` cost about half as much on the same ten.
- With `provider: vertex`, Gemini bills to the Google Cloud project instead, under Vertex AI's quotas rather than AI Studio's tier spend caps.
- A pull request split into chunks costs one call per chunk, so up to `max_chunks` times a single review.
- Runner minutes are your own. Set `runner` to use a self-hosted runner.

## Gemini through Vertex AI

AI Studio stops calls when a billing account reaches its [tier's monthly spend cap](https://aistudio.google.com/docs/billing#tier-spend-caps). Vertex AI bills the same models to a Google Cloud project instead. Locations are `global` or a region; the multi-region names `us` and `eu` are not accepted.

### With no stored key (recommended)

`gemini-review-vertex.yml` signs in with the job's GitHub OIDC token, exchanged for a short-lived token of a service account whose only permission is `aiplatform.endpoints.predict`. It is generated from `gemini-review.yml` by [`scripts/render_vertex_workflow.py`](scripts/render_vertex_workflow.py), so the review steps are the same.

1. Set up Google Cloud. [`scripts/setup_vertex_wif.sh`](scripts/setup_vertex_wif.sh) prints what it would create; `--apply` creates it and prints the two values the caller needs. It admits a token only from `gemini-review-vertex.yml` at a release tag, called from one of the listed repositories:

   ```bash
   scripts/setup_vertex_wif.sh --project YOUR_PROJECT_ID --repos OWNER/REPO --apply
   ```

2. Replace `gemini.yml`'s job. The caller must grant `id-token: write` itself, because a called workflow cannot raise its caller's permissions:

   ```yaml
   permissions:
     contents: read
     pull-requests: write
     id-token: write

   jobs:
     review:
       uses: jadedm/pr-review-workflows/.github/workflows/gemini-review-vertex.yml@v1
       with:
         stack: python
         vertex_project: YOUR_PROJECT_ID
         workload_identity_provider: projects/NUMBER/locations/global/workloadIdentityPools/github-pr-review/providers/github
         service_account: pr-review-vertex@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```

Pull requests from forks get no OIDC token, so the review fails on them, as it does without secrets.

### With a key

`gemini-review.yml` with `provider: vertex` sends a Google Cloud API key instead. Vertex AI accepts only a key bound to a service account (an unbound key gets HTTP 401), and some organisations' `iam.managed.disableServiceAccountApiKeyCreation` policy refuses those for Vertex AI until `aiplatform.googleapis.com` is in its allowed services ([`scripts/allow_vertex_api_keys.sh`](scripts/allow_vertex_api_keys.sh) adds it, given organisation rights).

```bash
scripts/create_vertex_key.sh --project YOUR_PROJECT_ID --apply
test -s /tmp/.vertex-gemini && gh secret set GEMINI_VERTEX_API_KEY --repo OWNER/REPO < /tmp/.vertex-gemini
```

```yaml
    with:
      stack: python
      provider: vertex
      vertex_project: YOUR_PROJECT_ID
    secrets:
      gemini_api_key: ${{ secrets.GEMINI_VERTEX_API_KEY }}
```

The caller's `permissions:` block stays as in the quickstart.

## Repo-specific rules

The stack prompts describe a stack, not your codebase. For your own conventions (guard names, internal paths, what the reviewer should flag), use:

- `extra_prompt_path: .github/review-extras.md`, a markdown file in your repo appended under "Repo-specific notes". It is read from the base branch, so a change to it is reviewed like any other change and applies from the next pull request. If the base branch has no such file, the default branch's copy is used; a folder, a link or an unreadable file fails the step.
- `extra_prompt: <text>` for a short inline note.

If both are set, the file comes first. Claude also reads the repo's `CLAUDE.md` on its own; Gemini sees it only when the pull request changes it.

```markdown
## Project conventions

- Guards: `JwtAuthGuard`, `AdminRoleGuard`
- Typed exceptions live in `@app/common`; never raw `HttpException`
- Every list endpoint is paginated by cursor
```

## Inputs

### Both workflows

| Input | Type | Default | Description |
|---|---|---|---|
| `stack` | string | required | Prompt name in `prompts/<stack>.md` |
| `runner` | string (JSON) | `"ubuntu-latest"` | `runs-on` value, JSON-encoded, for example `'["self-hosted", "linux"]'` |
| `ref` | string | `''` | Ref of this repo to load prompts, scripts and the Slack action from. Empty means the commit of the workflow that is running, so `@v1.6.14` or `@<sha>` also fixes them. It names a ref in the repo whose workflow is running, so a fork's caller needs a ref that exists in the fork. On GitHub Enterprise Server, which lacks that context, empty means `v1` |
| `slack_notify` | boolean | `false` | Post a Slack message when a `pull_request` run ends (not on comment runs). Pass `slack_webhook_url` under `secrets:` as well |
| `extra_prompt` | string | `''` | Inline repo-specific notes |
| `extra_prompt_path` | string | `''` | Path in your repo to a markdown file of repo-specific notes, read from the base branch |

### Gemini only

| Input | Type | Default | Description |
|---|---|---|---|
| `model` | string | `gemini-4-argon` | Gemini model ID |
| `thinking_level` | string | `high` | `minimal`, `low`, `medium`, `high`, or `none` (or empty) to send no thinking setting. Ignored for `gemini-1.x` and `gemini-2.x`. Vertex AI does not take `minimal` for `gemini-4-argon` |
| `provider` | string | `ai_studio` | `ai_studio` calls the Gemini API; `vertex` calls Vertex AI on Google Cloud billing (see "Gemini through Vertex AI") |
| `vertex_project` | string | `''` | Google Cloud project ID Vertex AI bills to. Required with `provider: vertex` |
| `vertex_location` | string | `global` | `global`, or a region such as `us-central1` that serves the model |
| `max_total_lines` | number | `20000` | Source lines per call |
| `max_per_file` | number | `3000` | For a file with more lines than this, send only its diff, not its full source |
| `max_files` | number | `50` | Files per call |
| `max_diff_lines` | number | `5000` | Diff lines per call |
| `max_payload_bytes` | number | `2000000` | Diff plus source bytes per call |
| `max_chunks` | number | `3` | Calls a large pull request is split into (1 to 5). Set `1` for one call |

### `gemini-review-vertex.yml` only

It takes the Gemini inputs above except `provider`, needs no `gemini_api_key`, and adds:

| Input | Type | Default | Description |
|---|---|---|---|
| `workload_identity_provider` | string | required | Resource name of the workload identity provider that trusts GitHub; `setup_vertex_wif.sh` prints it |
| `service_account` | string | required | Service account the job signs in as |

`vertex_project` is required there.

## Secrets

| Workflow | Secret | Where it comes from |
|---|---|---|
| Claude | `claude_oauth_token` | `claude setup-token` |
| Gemini | `gemini_api_key` | [Google AI Studio](https://aistudio.google.com/apikey) |
| Either | `slack_webhook_url`, optional | A Slack incoming webhook |

Claude needs both the GitHub App and the token. The App posts the comments; the review's model call needs the token.

On a GitHub Free organisation, organisation secrets are not available to private repos: `${{ secrets.X }}` resolves to empty and the API call fails with 401 or 403. Set the secrets on the repo.

## Security

- **Do not switch the trigger to `pull_request_target`.** Pull requests from forks, and from Dependabot, get no secrets under `pull_request`, so both reviewers run and fail on them. `pull_request_target` would run with your secrets and a write token while the model reads the fork's content. Both workflows run only on `pull_request` and comment events, so a caller switched to `pull_request_target` gets a skipped review.
- A maintainer's `@claude` comment on a fork's pull request runs with secrets, and Claude reads the fork's files; `@gemini` sends the fork's diff. Read the pull request before asking for a review on it.
- Claude applies your repo's `.claude/settings.json` and `.mcp.json` as they are on the base branch. Do not allow broad commands there, such as `Bash(git:*)`, `Bash(curl:*)` or `Bash(gh:*)`: a rule like that gives back a way to write or send data out that the deny list does not name. The deny list covers the known credential locations on a runner, not every location, so on a self-hosted runner keep other credentials off the machine.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Versioning

- `@v1` moves to the latest 1.x release. You get prompt updates and fixes on your next pull request. Breaking changes go to `v2`.
- `@v1.6.14`, or a commit SHA, fixes the workflow, prompts and scripts to that release.
- `@main` is for testing this repo only.

Releases are tags; [the tag list](https://github.com/jadedm/pr-review-workflows/tags) is the changelog.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The run shows 0 jobs and "workflow file issue" | The `uses:` path or tag does not exist, or the caller's YAML is invalid | Check the path and tag; parse the file with a YAML parser |
| Review fails on a fork's or Dependabot's pull request | Those pull requests get no secrets | Expected; review them with `@claude` or `@gemini` after reading them (see Security) |
| "Claude did not review this pull request" | The pull request changes a file under `.github/workflows/`, or another reason the run's log gives | Merge the workflow change to the default branch; later pull requests are reviewed |
| The API returns 401 or 403 and the key shows blank in the log | The secret resolved to empty, usually an organisation secret on a Free plan | Set the secret on the repo |
| "Claude Code is not installed on this repository" | The Claude GitHub App is not installed | Install it; the token is still needed |
| "Either ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or WIF is required" | The App is installed but the token is missing | Set `CLAUDE_CODE_OAUTH_TOKEN` |

## Adding a stack

Add `prompts/<name>.md` with that stack's conventions, free of any one project's details, and open a pull request; see [CONTRIBUTING.md](CONTRIBUTING.md). [`prompts/base.md`](prompts/base.md) holds the rules every review uses and is always loaded first.

## Layout

```
.github/workflows/claude-review.yml   reusable Claude workflow
.github/workflows/gemini-review.yml   reusable Gemini workflow
.github/workflows/tests.yml           this repo's own tests
actions/slack-notify/                 Slack message, used by both
prompts/                              base rules and one file per stack
scripts/                              Gemini payload, merge and comment; base-branch reader; tests
```

## Background

Design choices and operating notes: [running AI code review in CI with Gemini and Claude](https://manishj.com/garden/running-ai-code-review-ci-gemini-claude).

## License

[MIT](LICENSE). Built by [Manish Jadhav](https://manishj.com).
