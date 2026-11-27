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
ci=$(read_api "repos/$GH_REPO/actions/workflows/ci.yml/runs?branch=main&event=push&per_page=50")
deploy=$(read_api "repos/$GH_REPO/actions/workflows/deploy.yml/runs?per_page=50")
boot=$(read_api "repos/$GH_REPO/actions/workflows/bootstrap.yml/runs?per_page=50")
smoke=$(read_api "repos/$GH_REPO/actions/workflows/server-smoke.yml/runs?per_page=50")

# The deploy target is the newest push CI run on main: a docs-only head has none of its own.
newest=$(jq -er '[.workflow_runs[] | select(.head_branch == "main" and .event == "push")]
  | sort_by(.created_at) | reverse | .[0] | select(. != null)' <<<"$ci")   || { echo "no push CI run on main" >&2; exit 2; }
sha=$(jq -er '.head_sha | select(type == "string" and length > 0)' <<<"$newest")   || { echo "no head_sha in the newest CI run" >&2; exit 2; }
ci_conclusion=$(jq -er '.conclusion // "none"' <<<"$newest") || { echo "bad ci run" >&2; exit 2; }

event=$(jq -er '.event | select(type == "string")' <<<"$run") || { echo "no event in run" >&2; exit 2; }
# GitHub fills started_at even for a job that never ran: started means steps or a runner.
started=$(jq -er '[.jobs[] | select(.name == "deploy" and ((.steps // [] | length) > 0 or .runner_id != null))] | length' <<<"$jobs")   || { echo "bad jobs list" >&2; exit 2; }
covering=$(jq -er --arg sha "$sha" '[.workflow_runs[] | select(.head_sha == $sha and
  ((.status | IN("queued", "in_progress", "waiting", "pending", "requested"))
   or (.status == "completed" and .conclusion == "success")))] | length' <<<"$deploy")   || { echo "bad deploy runs list" >&2; exit 2; }
# A pending bootstrap or smoke run holds the one pending slot of the shared concurrency group.
pending=$(jq -er '[.workflow_runs[] | select(.status | IN("queued", "pending", "waiting", "requested"))] | length' <<<"$boot")   || { echo "bad bootstrap runs list" >&2; exit 2; }
pending_smoke=$(jq -er '[.workflow_runs[] | select(.status | IN("queued", "pending", "waiting", "requested"))] | length' <<<"$smoke")   || { echo "bad smoke runs list" >&2; exit 2; }

if [ "$started" -gt 0 ]; then
  echo "deploy job had started: deliberate cancel or timeout, nothing to do"
  exit 0
fi
if [ "$ci_conclusion" != "success" ]; then
  echo "CI for main's head is $ci_conclusion: nothing to deploy"
  exit 0
fi
if [ "$covering" -gt 0 ]; then
  echo "a Deploy for $sha already covers main"
  exit 0
fi
if [ $((pending + pending_smoke)) -gt 0 ]; then
  echo "a bootstrap or smoke run is pending: not re-dispatching, $sha is not deployed" >&2
  exit 1
fi
if [ "$event" != "workflow_run" ]; then
  echo "cancelled Deploy run $RUN_ID was a $event run and nothing covers $sha: not re-dispatching" >&2
  exit 1
fi

gh workflow run deploy.yml --ref main -f "sha=$sha" >&2
echo "re-dispatched Deploy for $sha"
