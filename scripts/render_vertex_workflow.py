#!/usr/bin/env python3
"""Writes .github/workflows/gemini-review-vertex.yml from gemini-review.yml (#55).

    python3 scripts/render_vertex_workflow.py          # write the file
    python3 scripts/render_vertex_workflow.py --check  # exit 1 if the file differs

The Vertex workflow signs in to Google Cloud with the job's OIDC token instead
of a stored key. That needs id-token: write, and a called workflow cannot ask
for more than its caller grants, so it is a separate file that only callers
choosing it grant that to. Every other step is gemini-review.yml's, so the file
is generated rather than kept by hand: each change below names the exact text
it replaces and how many times that text must occur, and the render fails if
the count is wrong, so an edit to gemini-review.yml cannot silently skip one.

Standard library only.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE = REPO / ".github/workflows/gemini-review.yml"
TARGET = REPO / ".github/workflows/gemini-review-vertex.yml"

# Dependabot bumps pins in the generated file, which the --check in CI then
# rejects; move a bumped pin here and rerun the render instead.
AUTH_ACTION = "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3.0.0"

HEADER = """# Generated from gemini-review.yml by scripts/render_vertex_workflow.py (#55).
# Do not edit by hand: change gemini-review.yml or the renderer, then rerun it.
"""

AUTH_STEP = f"""      - name: Sign in to Google Cloud
        # The job's OIDC token is exchanged for a short-lived access token of
        # the service account, through the caller's workload identity
        # provider. Nothing is written to the checkout, which is the pull
        # request's tree, or exported to later steps' environment.
        if: steps.files.outputs.count > 0
        id: auth
        uses: {AUTH_ACTION}
        with:
          workload_identity_provider: ${{{{ inputs.workload_identity_provider }}}}
          service_account: ${{{{ inputs.service_account }}}}
          token_format: access_token
          create_credentials_file: false
          export_environment_variables: false

      - name: Call Gemini API
"""

# (old text, new text, times the old text must occur)
CHANGES = [
    ("name: Gemini Review (reusable)\n", HEADER + "name: Gemini Review via Vertex AI (reusable)\n", 1),
    ("""      provider:
        description: 'Where Gemini is called: ai_studio (the Gemini API, default) or vertex (Vertex AI on Google Cloud billing, with a Google Cloud API key restricted to the Vertex AI API in gemini_api_key) (#55)'
        type: string
        default: 'ai_studio'
      vertex_project:
        description: 'Google Cloud project ID that Vertex AI bills to. Required when provider is vertex'
        type: string
        default: ''
""", """      vertex_project:
        description: 'Google Cloud project ID that Vertex AI bills to'
        type: string
        required: true
      workload_identity_provider:
        description: 'Full resource name of the workload identity provider that trusts GitHub, projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER (scripts/setup_vertex_wif.sh prints it)'
        type: string
        required: true
      service_account:
        description: 'Email of the service account the job signs in as; it needs only aiplatform.endpoints.predict'
        type: string
        required: true
""", 1),
    ("""      gemini_api_key:
        required: true
""", "", 1),
    ("""permissions:
  contents: read
  pull-requests: write
""", """permissions:
  contents: read
  pull-requests: write
  # The caller must grant this too; a called workflow cannot raise its caller's permissions.
  id-token: write
""", 1),
    ("          PROVIDER: ${{ inputs.provider }}\n", "          PROVIDER: vertex\n", 2),
    ("          GEMINI_API_KEY: ${{ secrets.gemini_api_key }}\n", "          GEMINI_API_KEY: ${{ steps.auth.outputs.access_token }}\n", 1),
    ("          AUTH_SCHEME: key\n", "          AUTH_SCHEME: bearer\n", 1),
    ("      - name: Call Gemini API\n", AUTH_STEP, 1),
]


def render(text, changes=CHANGES):
    for old, new, times in changes:
        found = text.count(old)
        if found != times:
            raise SystemExit(f"render: expected {times} of {old.splitlines()[0]!r} in gemini-review.yml, found {found}")
        text = text.replace(old, new)
    return text


def main():
    out = render(SOURCE.read_text())
    if "--check" in sys.argv:
        if TARGET.exists() and TARGET.read_text() == out:
            print("gemini-review-vertex.yml matches the render")
            return 0
        print("gemini-review-vertex.yml differs from the render; run python3 scripts/render_vertex_workflow.py")
        return 1
    TARGET.write_text(out)
    print(f"wrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
