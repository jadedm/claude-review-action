#!/usr/bin/env bash
# Let gemini-review-vertex.yml sign in to Google Cloud with no stored key (#55).
#
#   scripts/setup_vertex_wif.sh --project PROJECT_ID --repos OWNER/REPO[,OWNER/REPO...] \
#     --release-sha SHA [--release-sha SHA]... [--allow-ref REF]... [--apply [--prune]]
#
# Creates, or brings up to date: a workload identity pool and an OIDC provider
# for GitHub Actions in PROJECT_ID; a custom role holding only
# aiplatform.endpoints.predict and a service account granted only that role;
# and, for each listed repository, permission for that repository's workflow
# runs to act as the service account.
#
# The provider admits a GitHub token only when all three hold:
#   - the job runs gemini-review-vertex.yml from jadedm/pr-review-workflows
#     called as @v1, as @ one of the --release-sha values, or at one of the
#     --allow-ref refs, given in full such as refs/heads/my-branch, for testing
#     (remove those afterwards; a branch also needs its head commit passed as
#     --release-sha, since the next rule applies to it too). Older tags are not admitted, so a caller
#     cannot pin back to a release that a later one fixed;
#   - the token's job_workflow_sha is one of the --release-sha values. A tag
#     is a name that can be moved; this ties the token to what was reviewed
#     (#66). GitHub reports the SHA the caller's ref names: for @v1, which is
#     an annotated tag, that is the tag object (git rev-parse v1), not the
#     commit; for a caller pinned by commit, the commit (git rev-parse
#     'v1^{commit}'). Moving v1 makes a new tag object, so list both for the
#     release in use. When v1 moves, rerun with the old and the new SHAs,
#     move the tag, then rerun with the new ones only (CONTRIBUTING.md,
#     Releases);
#   - the calling repository's numeric id is one of the listed repositories'
#     (ids, not names, because a deleted repository's name can be taken again).
# So another workflow in a listed repository cannot get a token.
#
# Every run also reads what the service account can do and who can act as it,
# and lists anything not as expected (#65): a project role other than the
# custom one, a permission in the custom role other than
# aiplatform.endpoints.predict, predict missing from it or the role not at
# stage GA (--apply restores both), and any binding on the service account other
# than roles/iam.workloadIdentityUser for a repository passed in --repos. The
# run exits 1 while anything is listed. --prune with --apply removes it; a
# repository dropped from --repos loses its binding this way. Only the
# project's own policy and the service account's are read: not folder or
# organisation grants, grants through groups, or grants on other resources. A policy
# that cannot be read stops the run (exit 3), never a clean report.
#
# Without --apply it prints what is there and what it would set. Reruns change
# nothing that is already as wanted. The condition is rewritten from the
# --repos given, so a rerun must list every repository that should keep access;
# a dropped repository's binding stays but the condition no longer admits it. Uses the active gcloud account (set
# CLOUDSDK_CONFIG for another profile) and gh to look up repository ids.
set -euo pipefail

usage() { echo "usage: $0 --project PROJECT_ID --repos OWNER/REPO[,...] --release-sha SHA [--release-sha SHA]... [--allow-ref REF]... [--apply [--prune]]" >&2; exit 2; }
project="" repos="" apply=0 prune=0 refs=() shas=()
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    --repos) [ $# -ge 2 ] || usage; repos=$2; shift 2 ;;
    --release-sha) [ $# -ge 2 ] || usage; shas+=("$2"); shift 2 ;;
    --allow-ref) [ $# -ge 2 ] || usage; refs+=("$2"); shift 2 ;;
    --apply) apply=1; shift ;;
    --prune) prune=1; shift ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$project" ] && [ -n "$repos" ] && [ ${#shas[@]} -gt 0 ] || usage
for sha in "${shas[@]}"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "--release-sha must be a full 40-character lower-case SHA, got '${sha}'" >&2; exit 2; }
done
for ref in ${refs[@]+"${refs[@]}"}; do
  [[ "$ref" =~ ^refs/(heads|tags)/[A-Za-z0-9._/-]+$ ]] || { echo "--allow-ref must be a full ref such as refs/heads/main, got '${ref}'" >&2; exit 2; }
done

pool="github-pr-review"
provider="github"
sa_name="pr-review-vertex"
sa="${sa_name}@${project}.iam.gserviceaccount.com"
role_id="prReviewVertexPredict"
role_name="projects/${project}/roles/${role_id}"
predict="aiplatform.endpoints.predict"
workflow="jadedm/pr-review-workflows/.github/workflows/gemini-review-vertex.yml"

number=$(gcloud projects describe "$project" --format='value(projectNumber)')
[ -n "$number" ] || { echo "no project number for ${project}" >&2; exit 1; }
pool_name="projects/${number}/locations/global/workloadIdentityPools/${pool}"
provider_name="${pool_name}/providers/${provider}"

ids=()
IFS=, read -r -a repo_list <<<"$repos"
for repo in "${repo_list[@]}"; do
  [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "not OWNER/REPO: '${repo}'" >&2; exit 2; }
  id=$(gh api "repos/${repo}" --jq .id)
  [[ "$id" =~ ^[0-9]+$ ]] || { echo "no repository id for ${repo}" >&2; exit 1; }
  ids+=("$id")
  echo "  ${repo}: repository id ${id}"
done

members=()
for id in "${ids[@]}"; do
  members+=("principalSet://iam.googleapis.com/${pool_name}/attribute.repository_id/${id}")
done

# The attribute condition, in CEL.
refs_cel="assertion.job_workflow_ref == \"${workflow}@refs/tags/v1\""
for ref in "${shas[@]}" ${refs[@]+"${refs[@]}"}; do
  refs_cel="${refs_cel} || assertion.job_workflow_ref == \"${workflow}@${ref}\""
done
shas_cel=$(printf '"%s", ' "${shas[@]}")
ids_cel=$(printf '"%s", ' "${ids[@]}")
condition="(${refs_cel}) && assertion.job_workflow_sha in [${shas_cel%, }] && assertion.repository_id in [${ids_cel%, }]"
mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref,attribute.job_workflow_sha=assertion.job_workflow_sha"

state() {
  local pool_state provider_cond bindings
  pool_state=$(gcloud iam workload-identity-pools describe "$pool" --project "$project" --location global --format='value(state)' 2>/dev/null) || pool_state=none
  provider_cond=$(gcloud iam workload-identity-pools providers describe "$provider" --project "$project" --location global \
    --workload-identity-pool "$pool" --format='value(attributeCondition)' 2>/dev/null) || provider_cond=none
  bindings=$(gcloud iam service-accounts get-iam-policy "$sa" --project "$project" --flatten='bindings[].members' \
    --filter='bindings.role:roles/iam.workloadIdentityUser' --format='value(bindings.members)' 2>/dev/null) || bindings=""
  echo "  project:          ${project} (${number})"
  echo "  pool ${pool}:  ${pool_state}"
  echo "  provider ${provider}: ${provider_cond}"
  echo "  service account:  $(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)')"
  echo "  may act as it:"
  while IFS= read -r line; do echo "    ${line:-none}"; done <<<"$bindings"
}

tab=$'\t'
errf=$(mktemp)
trap 'rm -f "$errf"' EXIT
cannot_read() {
  echo "cannot check the service account's grants: ${1}" >&2
  sed 's/^/  /' "$errf" >&2
  exit 3
}
is_member() {
  local m
  for m in "${members[@]}"; do [ "$m" = "$1" ] && return 0; done
  return 1
}

# Adds to extras what differs in the custom role: a permission other than
# predict, predict missing, or a stage other than GA. A role not created yet
# adds nothing; --apply creates it.
role_extras() {
  local roles perms perm stage
  roles=$(gcloud iam roles list --project "$project" --format='value(name)' 2>"$errf") \
    || cannot_read "listing the custom roles of ${project}"
  grep -qxF "$role_name" <<<"$roles" || return 0
  perms=$(gcloud iam roles describe "$role_id" --project "$project" --format='value(includedPermissions)' 2>"$errf") \
    || cannot_read "reading role ${role_name}"
  stage=$(gcloud iam roles describe "$role_id" --project "$project" --format='value(stage)' 2>"$errf") \
    || cannot_read "reading role ${role_name}"
  for perm in ${perms//;/ }; do
    [ "$perm" = "$predict" ] && continue
    extras+=("permission${tab}${perm}${tab}-")
  done
  [[ ";${perms};" == *";${predict};"* ]] || extras+=("lacks${tab}${predict}${tab}-")
  [ "$stage" = GA ] || extras+=("stage${tab}${stage:-unset}${tab}-")
}

# Fills extras with one "kind<TAB>role or permission<TAB>member" line per
# difference from the expected set.
find_extras() {
  extras=()
  local sa_found rows role member
  role_extras
  sa_found=$(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)' 2>"$errf") \
    || cannot_read "listing the service accounts of ${project}"
  [ -n "$sa_found" ] || return 0
  rows=$(gcloud projects get-iam-policy "$project" --flatten='bindings[].members' \
    --format='csv[no-heading](bindings.role,bindings.members)' 2>"$errf") \
    || cannot_read "reading the IAM policy of ${project}"
  while IFS=, read -r role member; do
    [ "$member" = "serviceAccount:${sa}" ] || continue
    [ "$role" = "$role_name" ] && continue
    extras+=("project${tab}${role}${tab}${member}")
  done <<<"$rows"
  rows=$(gcloud iam service-accounts get-iam-policy "$sa" --project "$project" --flatten='bindings[].members' \
    --format='csv[no-heading](bindings.role,bindings.members)' 2>"$errf") \
    || cannot_read "reading the IAM policy of ${sa}"
  while IFS=, read -r role member; do
    [ -n "$role" ] || continue
    [ "$role" = roles/iam.workloadIdentityUser ] && is_member "$member" && continue
    extras+=("account${tab}${role}${tab}${member}")
  done <<<"$rows"
}

report_extras() {
  local e kind what member
  echo "Not as expected:"
  [ ${#extras[@]} -gt 0 ] || { echo "  none"; return 0; }
  for e in "${extras[@]}"; do
    IFS=$'\t' read -r kind what member <<<"$e"
    case "$kind" in
      permission) echo "  role ${role_id} has permission ${what}" ;;
      lacks) echo "  role ${role_id} lacks permission ${what}" ;;
      stage) echo "  role ${role_id} is at stage ${what}, not GA" ;;
      project) echo "  ${sa} holds ${what} on project ${project}" ;;
      account) echo "  ${member} has ${what} on ${sa}" ;;
    esac
  done
}

prune_extras() {
  local e kind what member reset=0
  for e in "${extras[@]}"; do
    IFS=$'\t' read -r kind what member <<<"$e"
    case "$kind" in
      permission|lacks|stage) reset=1 ;;
      project) gcloud projects remove-iam-policy-binding "$project" --member "$member" --role "$what" --all > /dev/null ;;
      account) gcloud iam service-accounts remove-iam-policy-binding "$sa" --project "$project" \
                 --member "$member" --role "$what" --all > /dev/null ;;
    esac
  done
  [ "$reset" -eq 1 ] || return 0
  gcloud iam roles update "$role_id" --project "$project" --permissions "$predict" --stage GA > /dev/null
}

echo
echo "Before:"
state
echo
echo "Wanted condition:"
echo "  ${condition}"

echo
find_extras
report_extras

if [ "$apply" -ne 1 ]; then
  [ "$prune" -eq 1 ] && [ ${#extras[@]} -gt 0 ] && echo "--prune with --apply would remove these."
  echo
  echo "Rerun with --apply to set it. Callers then pass:"
  echo "  workload_identity_provider: ${provider_name}"
  echo "  service_account: ${sa}"
  [ ${#extras[@]} -eq 0 ] || exit 1
  exit 0
fi

echo
echo "Applying:"
gcloud services enable aiplatform.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com --project "$project"
gcloud iam workload-identity-pools describe "$pool" --project "$project" --location global > /dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$pool" --project "$project" --location global \
       --display-name "GitHub, PR review" > /dev/null
if gcloud iam workload-identity-pools providers describe "$provider" --project "$project" --location global \
     --workload-identity-pool "$pool" > /dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$provider" --project "$project" --location global \
    --workload-identity-pool "$pool" --attribute-mapping "$mapping" --attribute-condition "$condition" > /dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$provider" --project "$project" --location global \
    --workload-identity-pool "$pool" --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "$mapping" --attribute-condition "$condition" > /dev/null
fi
gcloud iam roles describe "$role_id" --project "$project" > /dev/null 2>&1 \
  || gcloud iam roles create "$role_id" --project "$project" --title "PR review: Vertex AI generateContent" \
       --permissions aiplatform.endpoints.predict --stage GA > /dev/null
# Adds predict and sets GA if either was changed by hand; removes nothing.
gcloud iam roles update "$role_id" --project "$project" --add-permissions "$predict" --stage GA > /dev/null
[ -n "$(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)')" ] \
  || gcloud iam service-accounts create "$sa_name" --project "$project" --display-name "PR review, Vertex AI calls only"
gcloud projects add-iam-policy-binding "$project" --member "serviceAccount:${sa}" \
  --role "projects/${project}/roles/${role_id}" --condition=None > /dev/null
for id in "${ids[@]}"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$project" --role roles/iam.workloadIdentityUser \
    --member "principalSet://iam.googleapis.com/${pool_name}/attribute.repository_id/${id}" > /dev/null
done

find_extras
if [ "$prune" -eq 1 ] && [ ${#extras[@]} -gt 0 ]; then
  echo "Removing what is beyond the expected set:"
  report_extras
  prune_extras
  find_extras
fi

echo
echo "After:"
state
echo
report_extras
echo
echo "Callers pass:"
echo "  workload_identity_provider: ${provider_name}"
echo "  service_account: ${sa}"
[ ${#extras[@]} -eq 0 ] && exit 0
echo
echo "Grants beyond the expected set remain. Rerun with --prune --apply to remove them." >&2
exit 1
