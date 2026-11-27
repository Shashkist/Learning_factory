#!/usr/bin/env bash
# A cancelled Deploy run is noticed (#658, D189). Env: RUN_ID, GH_REPO, GH_TOKEN.
# Stdout carries only the verdict line; diagnostics go to stderr.
set -euo pipefail

: "${RUN_ID:?RUN_ID is required}"
: "${GH_REPO:?GH_REPO is required}"

# Read one endpoint as raw JSON. A failed or empty read is an error, never "no".
read_api() {
  local out
  out=$(gh api "$1") || { echo "read failed: $1" >&2; exit 2; }
  [ -n "$out" ] || { echo "empty read: $1" >&2; exit 2; }
  printf '%s' "$out"
}

run=$(read_api "repos/$GH_REPO/actions/runs/$RUN_ID")
jobs=$(read_api "repos/$GH_REPO/actions/runs/$RUN_ID/jobs")
commit=$(read_api "repos/$GH_REPO/commits/main")
sha=$(jq -er '.sha | select(type == "string" and length > 0)' <<<"$commit") \
  || { echo "no sha in commits/main" >&2; exit 2; }
ci=$(read_api "repos/$GH_REPO/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=$sha&per_page=50")
deploy=$(read_api "repos/$GH_REPO/actions/workflows/deploy.yml/runs?head_sha=$sha&per_page=50")

event=$(jq -er '.event | select(type == "string")' <<<"$run") || { echo "no event in run" >&2; exit 2; }
started=$(jq -er '[.jobs[] | select(.name == "deploy" and .started_at != null)] | length' <<<"$jobs") \
  || { echo "bad jobs list" >&2; exit 2; }
covering=$(jq -er --arg sha "$sha" '[.workflow_runs[] | select(.head_sha == $sha and
  ((.status | IN("queued", "in_progress", "waiting", "pending"))
   or (.status == "completed" and .conclusion == "success")))] | length' <<<"$deploy") \
  || { echo "bad deploy runs list" >&2; exit 2; }
ci_conclusion=$(jq -er --arg sha "$sha" '[.workflow_runs[] | select(.head_sha == $sha and .head_branch == "main" and .event == "push")]
  | sort_by(.created_at) | reverse | (.[0].conclusion // "none")' <<<"$ci") \
  || { echo "bad ci runs list" >&2; exit 2; }

if [ "$started" -gt 0 ]; then
  echo "deploy job had started: deliberate cancel or timeout, nothing to do"
  exit 0
fi
if [ "$covering" -gt 0 ]; then
  echo "a Deploy for $sha already covers main"
  exit 0
fi
if [ "$ci_conclusion" != "success" ]; then
  echo "CI for main's head is $ci_conclusion: nothing to deploy"
  exit 0
fi
if [ "$event" != "workflow_run" ]; then
  echo "cancelled Deploy run $RUN_ID was a $event run and nothing covers $sha: not re-dispatching" >&2
  exit 1
fi

gh workflow run deploy.yml --ref main -f "sha=$sha" >&2
echo "re-dispatched Deploy for $sha"
