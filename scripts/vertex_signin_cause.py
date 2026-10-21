#!/usr/bin/env python3
"""Names why the Vertex sign-in was refused (#70).

    python3 vertex_signin_cause.py classify        # prints a cause code
    python3 vertex_signin_cause.py message CODE    # prints the line to post, or nothing

classify runs in the gemini job of gemini-review-vertex.yml after
google-github-actions/auth failed. That action prints Google's reason to its
log only, so this repeats the first half of its exchange once: it asks GitHub
for the job's OIDC token, offers it to Google's token service for the same
provider, and keeps only which of a few known answers came back. Neither
token and nothing from the reply is printed. It reads
ACTIONS_ID_TOKEN_REQUEST_URL, ACTIONS_ID_TOKEN_REQUEST_TOKEN and
WORKLOAD_IDENTITY_PROVIDER from the environment. --sts-url is for the tests.

Codes:
  attribute_condition  the provider's condition refused this workflow's ref or SHA, or this repository
  no_token             the job got no OIDC token (a fork's pull_request run)
  other                anything else, including no answer at all

message runs in the post job and turns a code into a fixed line, so the
comment never carries text from Google or from the gemini job.

Standard library only.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

STS_URL = "https://sts.googleapis.com/v1/token"
TIMEOUT = 20
MAX_REPLY = 64 * 1024

MESSAGES = {
    "attribute_condition": (
        "Gemini did not review this pull request: Google Cloud refused the sign-in because the project's sign-in "
        "rule does not list this release of gemini-review-vertex.yml or this repository. Run "
        "scripts/setup_vertex_wif.sh with the current v1 tag object and commit SHAs and this repository in --repos "
        "(CONTRIBUTING.md, Releases), then rerun this check."
    ),
    "other": (
        "Gemini did not review this pull request: Google Cloud refused the sign-in. "
        "The Sign in to Google Cloud step log in the gemini job has Google's reason."
    ),
    # no_token: a fork's pull_request run, whose token cannot comment either.
}


def oidc_token(env):
    """The job's OIDC token for the provider's audience, or None when the job has none."""
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL"), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        return None
    # google-github-actions/auth's default audience.
    audience = "https://iam.googleapis.com/" + env.get("WORKLOAD_IDENTITY_PROVIDER", "")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(url + sep + "audience=" + urllib.parse.quote(audience, safe=""),
                                 headers={"Authorization": "bearer " + bearer})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(MAX_REPLY))["value"]


def exchange_refusal(subject, provider, sts_url):
    """Google's error_description for the exchange, or '' when it was accepted."""
    body = json.dumps({
        "audience": "//iam.googleapis.com/" + provider,
        "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
        "subjectToken": subject,
    }).encode()
    req = urllib.request.Request(sts_url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read(MAX_REPLY)  # an accepted exchange: the token is dropped unread
            return ""
    except urllib.error.HTTPError as e:
        reply = json.loads(e.read(MAX_REPLY) or b"{}")
        return str(reply.get("error_description", "")) if isinstance(reply, dict) else ""


def classify(env, sts_url=STS_URL):
    try:
        subject = oidc_token(env)
    except Exception:
        return "other"
    if subject is None:
        return "no_token"
    try:
        refusal = exchange_refusal(subject, env.get("WORKLOAD_IDENTITY_PROVIDER", ""), sts_url)
    except Exception:
        return "other"
    if "attribute condition" in refusal.lower():
        return "attribute_condition"
    return "other"


def main(argv, env):
    if argv[:1] == ["message"] and len(argv) == 2:
        message = MESSAGES.get(argv[1], "")
        if message:
            print(message)
        return 0
    if argv[:1] == ["classify"]:
        sts_url = argv[argv.index("--sts-url") + 1] if "--sts-url" in argv[:-1] else STS_URL
        print(classify(env, sts_url))
        return 0
    print("usage: vertex_signin_cause.py classify | message CODE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import os
    sys.exit(main(sys.argv[1:], os.environ))
