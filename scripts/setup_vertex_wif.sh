#!/usr/bin/env bash
# Let gemini-review-vertex.yml sign in to Google Cloud with no stored key (#55).
#
#   scripts/setup_vertex_wif.sh --project PROJECT_ID --repos OWNER/REPO[,OWNER/REPO...] \
#     [--allow-ref REF]... [--apply]
#
# Creates, or brings up to date: a workload identity pool and an OIDC provider
# for GitHub Actions in PROJECT_ID; a custom role holding only
# aiplatform.endpoints.predict and a service account granted only that role;
# and, for each listed repository, permission for that repository's workflow
# runs to act as the service account.
#
# The provider admits a GitHub token only when both hold:
#   - the job runs gemini-review-vertex.yml from jadedm/pr-review-workflows at
#     the v1 tag exactly, or at one of the --allow-ref refs, given in full such
#     as refs/heads/my-branch, for testing (remove those afterwards). Older
#     tags are not admitted, so a caller cannot pin back to a release that a
#     later one fixed;
#   - the calling repository's numeric id is one of the listed repositories'
#     (ids, not names, because a deleted repository's name can be taken again).
# So another workflow in a listed repository cannot get a token.
#
# Without --apply it prints what is there and what it would set. Reruns change
# nothing that is already as wanted. The condition is rewritten from the
# --repos given, so a rerun must list every repository that should keep access;
# a dropped repository's binding stays but the condition no longer admits it. Uses the active gcloud account (set
# CLOUDSDK_CONFIG for another profile) and gh to look up repository ids.
set -euo pipefail

usage() { echo "usage: $0 --project PROJECT_ID --repos OWNER/REPO[,...] [--allow-ref REF]... [--apply]" >&2; exit 2; }
project="" repos="" apply=0 refs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    --repos) [ $# -ge 2 ] || usage; repos=$2; shift 2 ;;
    --allow-ref) [ $# -ge 2 ] || usage; refs+=("$2"); shift 2 ;;
    --apply) apply=1; shift ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$project" ] && [ -n "$repos" ] || usage
for ref in ${refs[@]+"${refs[@]}"}; do
  [[ "$ref" =~ ^refs/(heads|tags)/[A-Za-z0-9._/-]+$ ]] || { echo "--allow-ref must be a full ref such as refs/heads/main, got '${ref}'" >&2; exit 2; }
done

pool="github-pr-review"
provider="github"
sa_name="pr-review-vertex"
sa="${sa_name}@${project}.iam.gserviceaccount.com"
role_id="prReviewVertexPredict"
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

# The attribute condition, in CEL.
refs_cel="assertion.job_workflow_ref == \"${workflow}@refs/tags/v1\""
for ref in ${refs[@]+"${refs[@]}"}; do
  refs_cel="${refs_cel} || assertion.job_workflow_ref == \"${workflow}@${ref}\""
done
ids_cel=$(printf '"%s", ' "${ids[@]}")
condition="(${refs_cel}) && assertion.repository_id in [${ids_cel%, }]"
mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref"

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

echo
echo "Before:"
state
echo
echo "Wanted condition:"
echo "  ${condition}"

if [ "$apply" -ne 1 ]; then
  echo
  echo "Rerun with --apply to set it. Callers then pass:"
  echo "  workload_identity_provider: ${provider_name}"
  echo "  service_account: ${sa}"
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
[ -n "$(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)')" ] \
  || gcloud iam service-accounts create "$sa_name" --project "$project" --display-name "PR review, Vertex AI calls only"
gcloud projects add-iam-policy-binding "$project" --member "serviceAccount:${sa}" \
  --role "projects/${project}/roles/${role_id}" --condition=None > /dev/null
for id in "${ids[@]}"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$project" --role roles/iam.workloadIdentityUser \
    --member "principalSet://iam.googleapis.com/${pool_name}/attribute.repository_id/${id}" > /dev/null
done

echo
echo "After:"
state
echo
echo "Callers pass:"
echo "  workload_identity_provider: ${provider_name}"
echo "  service_account: ${sa}"
