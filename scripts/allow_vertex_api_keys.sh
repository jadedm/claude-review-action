#!/usr/bin/env bash
# Let an organisation create API keys bound to a service account for Vertex AI (#55).
#
#   scripts/allow_vertex_api_keys.sh --org ORG_ID --quota-project PROJECT_ID [--enable-api] [--apply]
#
# The managed constraint iam.managed.disableServiceAccountApiKeyCreation allows
# a bound key only when its API targets are all in allowedServices. This adds
# aiplatform.googleapis.com to that list and keeps every service already on it,
# plus generativelanguage.googleapis.com, which Google allows by default.
# Without --apply it prints the effective policy and the policy it would set.
# Reading needs the Organization Policy API on the quota project, which gcloud
# bills the policy calls to; --enable-api turns it on and then only reads.
set -euo pipefail

usage() { echo "usage: $0 --org ORG_ID --quota-project PROJECT_ID [--enable-api] [--apply]" >&2; exit 2; }
org="" quota="" apply=0 enable=0
while [ $# -gt 0 ]; do
  case "$1" in
    --org) [ $# -ge 2 ] || usage; org=$2; shift 2 ;;
    --quota-project) [ $# -ge 2 ] || usage; quota=$2; shift 2 ;;
    --apply) apply=1; shift ;;
    --enable-api) enable=1; shift ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[[ "$org" =~ ^[0-9]+$ ]] && [ -n "$quota" ] || usage

constraint="iam.managed.disableServiceAccountApiKeyCreation"
add="aiplatform.googleapis.com"

if ! gcloud services list --project "$quota" --enabled --format='value(config.name)' | grep -qx orgpolicy.googleapis.com; then
  [ "$enable" -eq 1 ] || { echo "The Organization Policy API is disabled on ${quota}; rerun with --enable-api to turn it on and read the policy."; exit 0; }
  gcloud services enable orgpolicy.googleapis.com --project "$quota"
  apply=0  # enabling and setting stay separate runs, so the new policy is read before it is set
fi

show() {
  gcloud org-policies describe "$constraint" --organization "$org" --effective --billing-project "$quota" --format=yaml
}

echo "Effective policy before:"
before=$(show)
while IFS= read -r line; do echo "  ${line}"; done <<<"$before"

# Services already allowed, from the effective policy's allowedServices.
current=$(printf '%s\n' "$before" | python3 -I -c '
import re, sys
text = sys.stdin.read()
block = text.split("allowedServices:", 1)[1] if "allowedServices:" in text else ""
for line in block.splitlines():
    m = re.match(r"^\s*-\s*([a-z0-9.-]+\.googleapis\.com)\s*$", line)
    if m:
        print(m.group(1))
    elif line.strip() and not line.strip().startswith("-"):
        break
')
services=$(printf '%s\n' "$current" generativelanguage.googleapis.com "$add" | grep . | sort -u)

spec=$(mktemp)
trap 'rm -f "$spec"' EXIT
{
  echo "name: organizations/${org}/policies/${constraint}"
  echo "spec:"
  echo "  rules:"
  echo "  - enforce: true"
  echo "    parameters:"
  echo "      allowedServices:"
  for s in $services; do echo "      - ${s}"; done
} > "$spec"

echo
echo "Policy to set:"
sed 's/^/  /' "$spec"

if [ "$apply" -ne 1 ]; then
  echo
  echo "Rerun with --apply to set it."
  exit 0
fi

gcloud org-policies set-policy "$spec" --update-mask spec --billing-project "$quota" > /dev/null
echo
echo "Effective policy after:"
show | sed 's/^/  /'
