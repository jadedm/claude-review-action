#!/usr/bin/env bash
# Applies the release protections from sandbox#60 to this repo and prints the
# settings before and after. Safe to rerun: a ruleset that already exists by
# name is left alone.
#
#   scripts/apply-repo-protection.sh --repo jadedm/claude-review-action
#   scripts/apply-repo-protection.sh --repo jadedm/claude-review-action --dry-run
#
# Every consumer runs this repo's code with its own secrets, so:
#   - tags v*: cannot be deleted, rewritten or moved, except by a repo admin
#     (the release step moves v1 forward);
#   - main: cannot be deleted or force-pushed, and changes arrive by pull request;
#   - workflows in this repo get a read-only token and cannot approve pull requests.
set -euo pipefail

repo=""
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo=${2:-}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$repo" ] || { echo "usage: $0 --repo <owner/name> [--dry-run]" >&2; exit 64; }

show() {
  echo "  actions token:  $(gh api "repos/$repo/actions/permissions/workflow" \
    -q '"default=\(.default_workflow_permissions) can_approve_prs=\(.can_approve_pull_request_reviews)"')"
  echo "  rulesets:       $(gh api "repos/$repo/rulesets" -q '[.[] | "\(.name) (\(.target), \(.enforcement))"] | if length == 0 then "none" else join(", ") end')"
}

ruleset_exists() {
  gh api "repos/$repo/rulesets" -q ".[] | select(.name == \"$1\") | .id" | grep -q .
}

create_ruleset() {
  local name=$1 body=$2
  if ruleset_exists "$name"; then
    echo "  skip: ruleset '$name' already exists"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    echo "  would create ruleset '$name'"
    return 0
  fi
  printf '%s' "$body" | gh api -X POST "repos/$repo/rulesets" --input - -q '"  created ruleset \(.name) id=\(.id)"'
}

echo "Before ($repo):"
show

echo "Changes:"
# actor_id 5 is the built-in repository admin role.
create_ruleset "release tags" '{
  "name": "release tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
  "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}],
  "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}]
}'
create_ruleset "main" '{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false
    }}
  ]
}'
if [ "$dry_run" -eq 1 ]; then
  echo "  would set actions token to read, no pull request approvals"
else
  gh api -X PUT "repos/$repo/actions/permissions/workflow" \
    -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
  echo "  set actions token to read, no pull request approvals"
fi

echo "After ($repo):"
show
