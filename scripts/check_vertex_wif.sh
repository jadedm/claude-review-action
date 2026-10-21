#!/usr/bin/env bash
# Check that the live Google Cloud sign-in rule admits the current v1 release (#70).
#
#   scripts/check_vertex_wif.sh --project PROJECT_ID
#
# Reads the provider's attributeCondition (set by setup_vertex_wif.sh) and the
# v1 tag object and commit with git ls-remote, and checks four things: the tag
# object and the commit are both in the job_workflow_sha list, and both
# @refs/tags/v1 (callers at @v1) and @<commit> (callers pinned by commit) are
# admitted as refs. It looks for each value as a quoted string in the
# condition; it does not evaluate the CEL.
#
# Read only: one gcloud describe and one git ls-remote. Exit 0 when all four
# hold, 1 naming each one that does not, 2 on bad arguments, and 3 when the
# condition or the tag cannot be read (not signed in, no access, no provider),
# so it never passes on a failed read. Uses the active gcloud account (set
# CLOUDSDK_CONFIG for another profile). Run it after moving v1
# (CONTRIBUTING.md, Releases) and after any manual edit of the provider.
set -euo pipefail

usage() { echo "usage: $0 --project PROJECT_ID" >&2; exit 2; }
project=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[[ "$project" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || usage

repo_url="https://github.com/jadedm/pr-review-workflows"
pool="github-pr-review"
provider="github"
workflow="jadedm/pr-review-workflows/.github/workflows/gemini-review-vertex.yml"

errf=$(mktemp)
trap 'rm -f "$errf"' EXIT
cannot_read() {
  echo "cannot check: ${1}" >&2
  sed 's/^/  /' "$errf" >&2
  exit 3
}

tags=$(git ls-remote "$repo_url" refs/tags/v1 'refs/tags/v1^{}' 2>"$errf") \
  || cannot_read "git ls-remote ${repo_url} failed"
tag_object=$(awk '$2 == "refs/tags/v1" { print $1 }' <<<"$tags")
commit=$(awk '$2 == "refs/tags/v1^{}" { print $1 }' <<<"$tags")
[[ "$tag_object" =~ ^[0-9a-f]{40}$ ]] || cannot_read "no refs/tags/v1 at ${repo_url}"
# A lightweight tag has no ^{} line: the tag names the commit itself.
commit=${commit:-$tag_object}
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || cannot_read "unreadable commit for refs/tags/v1 at ${repo_url}"

condition=$(gcloud iam workload-identity-pools providers describe "$provider" --project "$project" --location global \
  --workload-identity-pool "$pool" --format='value(attributeCondition)' 2>"$errf") \
  || cannot_read "reading provider ${pool}/${provider} in ${project}"
[ -n "$condition" ] || cannot_read "provider ${pool}/${provider} in ${project} has no attribute condition"

listed_shas=()
if [[ "$condition" =~ job_workflow_sha[[:space:]]+in[[:space:]]*\[([^]]*)\] ]]; then
  IFS=, read -r -a items <<<"${BASH_REMATCH[1]}"
  for item in ${items[@]+"${items[@]}"}; do
    item=${item//[[:space:]]/}
    item=${item//\"/}
    item=${item//\'/}
    listed_shas+=("$item")
  done
fi

sha_listed() {
  local s
  for s in ${listed_shas[@]+"${listed_shas[@]}"}; do [ "$s" = "$1" ] && return 0; done
  return 1
}
ref_admitted() {
  [[ "$condition" == *"\"${workflow}@${1}\""* || "$condition" == *"'${workflow}@${1}'"* ]]
}

missing=()
result() {
  if [ "$1" = ok ]; then echo "  listed   ${2}"; return 0; fi
  echo "  MISSING  ${2}"
  missing+=("$2")
}

echo "v1 at ${repo_url}: tag object ${tag_object}, commit ${commit}"
echo "Condition on ${pool}/${provider} in ${project}:"
echo "  ${condition}"
echo
[ ${#listed_shas[@]} -gt 0 ] || result missing "a job_workflow_sha list in the condition"
sha_listed "$tag_object" && r=ok || r=missing
result "$r" "tag object ${tag_object} in the SHA list (callers at @v1)"
if [ "$commit" != "$tag_object" ]; then
  sha_listed "$commit" && r=ok || r=missing
  result "$r" "commit ${commit} in the SHA list (callers pinned by commit)"
fi
ref_admitted refs/tags/v1 && r=ok || r=missing
result "$r" "ref @refs/tags/v1 (callers at @v1)"
ref_admitted "$commit" && r=ok || r=missing
result "$r" "ref @${commit} (callers pinned by commit)"

echo
if [ ${#missing[@]} -eq 0 ]; then
  echo "The sign-in rule admits the current v1."
  exit 0
fi
echo "The sign-in rule does not admit the current v1 for every caller. Rerun setup_vertex_wif.sh --apply" >&2
echo "with --release-sha ${tag_object} --release-sha ${commit} (CONTRIBUTING.md, Releases)." >&2
exit 1
