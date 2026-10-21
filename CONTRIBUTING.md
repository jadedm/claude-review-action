# Contributing

Issues and pull requests are welcome. For a question or an idea that is not yet a change, use [Discussions](https://github.com/jadedm/pr-review-workflows/discussions).

## Before you open a pull request

- Open an issue first for anything bigger than a typo or a prompt tweak, so the approach can be agreed before you write it.
- Keep one change per pull request.
- A new stack prompt goes in `prompts/<name>.md`. Describe the stack's conventions, not one project's: project rules belong in the caller's `extra_prompt_path` file.
- Every third-party action stays pinned to a full commit SHA with its version in a trailing comment.
- The workflows never run code from the pull request under review, and never move to `pull_request_target`. A change that would needs an issue first.

## Tests

The tests use the Python standard library only. Run them all from the repo root:

```bash
(rc=0; for t in scripts/test_*.py; do python3 "$t" || { echo "FAILED: $t"; rc=1; }; done; exit $rc)
```

`scripts/test_gemini_review_parse.py` and the others each print one line per case and a pass and fail count. CI runs the same files (`.github/workflows/tests.yml`). A change to a workflow step that a test reads, such as the skip notice in `claude-review.yml`, should come with a case that fails without the change.

## Commits

Conventional commit messages (`fix(gemini): ...`, `docs: ...`). The body says what was wrong and why the change is shaped the way it is.

## Releases

Maintainers tag releases as `v1.x.y` and move the `v1` tag to the same commit. A change that breaks callers (a removed input, a different output format) goes to a new major version instead.

Each Google Cloud project that runs `gemini-review-vertex.yml` admits only listed release commits, so moving `v1` takes three steps there:

1. Rerun `scripts/setup_vertex_wif.sh --apply` with `--release-sha` for the current and the new commit.
2. Move `v1`.
3. Rerun it with the new commit only. Until this step the old commit can still sign in.
