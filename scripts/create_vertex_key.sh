#!/usr/bin/env bash
# Create a Google Cloud API key for provider: vertex in gemini-review.yml (#55).
#
#   scripts/create_vertex_key.sh --project PROJECT_ID [--out FILE] [--replace] [--apply]
#
# The key must be bound to a service account: Vertex AI answers 401 "API keys
# are not supported by this API" to a key with none, on both the project and the
# express-mode endpoints (tested 20 Oct 2026). Where the organisation policy
# iam.managed.disableServiceAccountApiKeyCreation refuses bound keys for Vertex
# AI, scripts/allow_vertex_api_keys.sh adds it to the policy's allowed services.
#
# Without --apply it only prints what is there and what it would do. With
# --apply it enables the Vertex AI and API Keys APIs, creates a service account
# whose only grant is a custom role holding aiplatform.endpoints.predict (the
# permission generateContent needs), and creates an API key bound to that
# account and restricted to the Vertex AI API. The key is written to FILE
# (default /tmp/.vertex-gemini, created new with mode 600) and never printed.
# Put it in a caller repo's secret with:
#
#   test -s /tmp/.vertex-gemini && gh secret set GEMINI_VERTEX_API_KEY --repo OWNER/REPO < /tmp/.vertex-gemini
#
# It refuses to create a second key while one of the same name exists, unless
# --replace is given, which deletes the existing ones first (their delete
# output, which echoes the key, is discarded). Uses the active gcloud account; set CLOUDSDK_CONFIG
# to pick another profile.
set -euo pipefail

usage() { echo "usage: $0 --project PROJECT_ID [--out FILE] [--replace] [--apply]" >&2; exit 2; }
project="" out="/tmp/.vertex-gemini" apply=0 replace=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    --out) [ $# -ge 2 ] || usage; out=$2; shift 2 ;;
    --apply) apply=1; shift ;;
    --replace) replace=1; shift ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$project" ] || usage

sa_name="pr-review-vertex"
sa="${sa_name}@${project}.iam.gserviceaccount.com"
key_name="pr-review-vertex"
role_id="prReviewVertexPredict"
role="projects/${project}/roles/${role_id}"

enabled() {
  local list
  # A failed lookup must not read as "disabled": -e is off inside $( ).
  list=$(gcloud services list --project "$project" --enabled --format='value(config.name)') || return 1
  if grep -qx "$1" <<<"$list"; then echo enabled; else echo disabled; fi
}

state() {
  local account billing vertex apikeys sa_now roles keys
  account=$(gcloud config get-value account 2>/dev/null)
  billing=$(gcloud billing projects describe "$project" --format='value(billingAccountName)')
  vertex=$(enabled aiplatform.googleapis.com) || exit 1
  apikeys=$(enabled apikeys.googleapis.com) || exit 1
  sa_now=$(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)')
  roles=$(gcloud projects get-iam-policy "$project" --flatten='bindings[].members' \
    --filter="bindings.members:serviceAccount:${sa}" --format='value(bindings.role)' | tr '\n' ' ')
  keys="(API Keys API disabled)"
  if [ "$apikeys" = enabled ]; then
    keys=$(gcloud services api-keys list --project "$project" --filter="displayName=${key_name}" \
      --format='value(uid,createTime,restrictions.apiTargets[].service.list(),serviceAccountEmail)')
  fi
  echo "  account:        ${account}"
  echo "  project:        ${project}"
  echo "  billing:        ${billing}"
  echo "  vertex api:     ${vertex}"
  echo "  api keys api:   ${apikeys}"
  echo "  service acct:   ${sa_now:-none}"
  echo "  its roles:      ${roles:-none}"
  echo "  keys named ${key_name}:"
  while IFS= read -r line; do echo "    ${line}"; done <<<"${keys:-none}"
}

echo "Before:"
state

if [ "$apply" -ne 1 ]; then
  echo
  echo "Would: enable aiplatform.googleapis.com and apikeys.googleapis.com; create role ${role_id}"
  echo "(aiplatform.endpoints.predict) and ${sa} if missing, and grant it that role only;"
  echo "create an API key '${key_name}' bound to it and restricted to aiplatform.googleapis.com; write it to ${out}."
  echo "Rerun with --apply to do it."
  exit 0
fi

if [ -e "$out" ] || [ -L "$out" ]; then
  echo "${out} already exists; remove it or pass --out with a new path" >&2; exit 1
fi
# Claim the path now, private and empty, so nothing can be put there during the
# gcloud calls below; it is removed again if the key is not written.
umask 077
(set -C; : > "$out")
trap '[ -s "$out" ] || rm -f "$out"' EXIT

echo
echo "Applying:"
gcloud services enable aiplatform.googleapis.com apikeys.googleapis.com --project "$project"
existing=$(gcloud services api-keys list --project "$project" --filter="displayName=${key_name}" --format='value(uid)')
if [ -n "$existing" ] && [ "$replace" -eq 1 ]; then
  for old in $(gcloud services api-keys list --project "$project" --filter="displayName=${key_name}" --format='value(name)'); do
    # delete prints the deleted key, string included, so its output is dropped.
    gcloud services api-keys delete "$old" --project "$project" --quiet > /dev/null 2>&1 \
      || { echo "deleting ${old##*/} failed" >&2; exit 1; }
    echo "  deleted ${old##*/}"
  done
  existing=""
fi
if [ -n "$existing" ]; then
  echo "a key named ${key_name} already exists (${existing}); delete it first:" >&2
  echo "  gcloud services api-keys delete UID --project ${project} --quiet > /dev/null 2>&1, or rerun with --replace" >&2
  exit 1
fi
gcloud iam roles describe "$role_id" --project "$project" > /dev/null 2>&1 \
  || gcloud iam roles create "$role_id" --project "$project" --title "PR review: Vertex AI generateContent" \
       --permissions aiplatform.endpoints.predict --stage GA > /dev/null
[ -n "$(gcloud iam service-accounts list --project "$project" --filter="email=${sa}" --format='value(email)')" ] \
  || gcloud iam service-accounts create "$sa_name" --project "$project" --display-name "PR review, Vertex AI calls only"
gcloud projects add-iam-policy-binding "$project" --member "serviceAccount:${sa}" --role "$role" --condition=None > /dev/null
# A fixed id, so the next command finds this key without parsing create's output.
uid="${key_name}-$(date -u +%Y%m%d%H%M%S)"
# --async returns the operation, not the finished key, so the key string is
# never in create's output or in gcloud's own log of it.
# With --async gcloud names the operation in a message ("operations wait operations/..."),
# not the key, so the operation id is read from that text.
op=$(gcloud services api-keys create --project "$project" --key-id "$uid" --display-name "$key_name" \
  --api-target=service=aiplatform.googleapis.com --service-account "$sa" --async 2>&1 | grep -o 'operations/[A-Za-z0-9._-]*' | head -1) || true
[ -n "$op" ] || { echo "creating key ${uid} returned no operation" >&2; exit 1; }
# The operation's own record carries the key string once it finishes, so it is
# never read. The key's metadata (api-keys describe) holds no key string; its
# appearing means the key exists. A key the org policy refuses never appears.
found=""
for attempt in $(seq 1 20); do
  found=$(gcloud services api-keys describe "$uid" --project "$project" --format='value(uid)' 2> /dev/null) || true
  [ -n "$found" ] && break
  sleep 3
done
if [ -z "$found" ]; then
  echo "key ${uid} did not appear within a minute. Its error is not printed here, because a finished" >&2
  echo "operation's record holds the key; to read it: gcloud services operations describe ${op} --format='value(error.message)' 2>/dev/null" >&2
  echo "An organisation policy (iam.managed.disableServiceAccountApiKeyCreation) that does not list" >&2
  echo "aiplatform.googleapis.com in allowedServices refuses a bound key." >&2
  exit 1
fi
# Still the plain, empty, private file claimed above, not a link or a pipe.
if ! [ -f "$out" ] || [ -L "$out" ] || [ -s "$out" ]; then
  echo "${out} changed while the key was being created; not writing to it" >&2; exit 1
fi
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  gcloud services api-keys get-key-string "$uid" --project "$project" --format='value(keyString)' >> "$out" 2> /dev/null && break
  [ "$attempt" -lt 10 ] && sleep 3
done
[ -s "$out" ] || { echo "no key string was written to ${out}; key ${uid} may still be creating" >&2; exit 1; }
echo "  key ${uid} written to ${out}"

echo
echo "After:"
state
