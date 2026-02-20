#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 || $# -gt 5 ]]; then
  cat <<USAGE
Usage:
  scripts/bootstrap_phase_orchestrate.sh <working_directory> <design_doc> <plan_doc> <phase_name> [session_name]

Example:
  scripts/bootstrap_phase_orchestrate.sh \
    /Users/tao/work/acp-orchestrator \
    /Users/tao/work/acp-orchestrator/docs/design.md \
    /Users/tao/work/acp-orchestrator/docs/plan.md \
    phase-1 \
    cao-dev

Environment overrides (optional):
  CAO_BASE_URL (default: http://localhost:9889)
  DEVELOPER_PROVIDER (default: codex)
  DEVELOPER_PROFILE (default: developer)
  REVIEWER_PROVIDER (default: codex)
  REVIEWER_PROFILE (default: reviewer)
  ANALYZER_PROVIDER (default: claude_code)
  ANALYZER_PROFILE (default: reviewer)
  RUN_ORCHESTRATE (default: 1; set to 0 to only create terminals and print IDs)
  RUN_ROOT (default: .orchestrator/runs)
  POLL_INTERVAL (default: 2)
  RESPONSE_TIMEOUT (default: 1800)
  COMMIT_MAX_RETRIES (default: 1)
USAGE
  exit 1
fi

WORKING_DIRECTORY="$1"
DESIGN_DOC="$2"
PLAN_DOC="$3"
PHASE_NAME="$4"
SESSION_NAME="${5:-}"

if [[ ! -d "$WORKING_DIRECTORY" ]]; then
  echo "Working directory not found: $WORKING_DIRECTORY" >&2
  exit 1
fi
if [[ ! -f "$DESIGN_DOC" ]]; then
  echo "Design doc not found: $DESIGN_DOC" >&2
  exit 1
fi
if [[ ! -f "$PLAN_DOC" ]]; then
  echo "Plan doc not found: $PLAN_DOC" >&2
  exit 1
fi

CAO_BASE_URL="${CAO_BASE_URL:-http://localhost:9889}"
DEVELOPER_PROVIDER="${DEVELOPER_PROVIDER:-codex}"
DEVELOPER_PROFILE="${DEVELOPER_PROFILE:-developer}"
REVIEWER_PROVIDER="${REVIEWER_PROVIDER:-codex}"
REVIEWER_PROFILE="${REVIEWER_PROFILE:-reviewer}"
ANALYZER_PROVIDER="${ANALYZER_PROVIDER:-claude_code}"
ANALYZER_PROFILE="${ANALYZER_PROFILE:-reviewer}"
RUN_ORCHESTRATE="${RUN_ORCHESTRATE:-1}"
RUN_ROOT="${RUN_ROOT:-.orchestrator/runs}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"
RESPONSE_TIMEOUT="${RESPONSE_TIMEOUT:-1800}"
COMMIT_MAX_RETRIES="${COMMIT_MAX_RETRIES:-1}"

if ! curl -sS --max-time 5 "${CAO_BASE_URL}/health" >/dev/null; then
  echo "cao-server is not reachable at ${CAO_BASE_URL}" >&2
  exit 1
fi

json_get() {
  local json_input="$1"
  local field_name="$2"
  python3 - "$json_input" "$field_name" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
field = sys.argv[2]
val = obj.get(field)
if isinstance(val, str) and val:
    print(val)
else:
    raise SystemExit(1)
PY
}

json_detail() {
  local json_input="$1"
  python3 - "$json_input" <<'PY'
import json
import sys

try:
    obj = json.loads(sys.argv[1])
except Exception:
    print(sys.argv[1])
    raise SystemExit(0)

detail = obj.get("detail")
if isinstance(detail, str) and detail:
    print(detail)
else:
    print(sys.argv[1])
PY
}

HTTP_STATUS=""
HTTP_BODY=""
post_get() {
  local url="$1"
  shift
  local tmp_body
  tmp_body="$(mktemp)"
  HTTP_STATUS="$(curl -sS -o "$tmp_body" -w "%{http_code}" -X POST "$url" --get "$@")"
  HTTP_BODY="$(cat "$tmp_body")"
  rm -f "$tmp_body"
}

get_json() {
  local url="$1"
  local tmp_body
  tmp_body="$(mktemp)"
  HTTP_STATUS="$(curl -sS -o "$tmp_body" -w "%{http_code}" "$url")"
  HTTP_BODY="$(cat "$tmp_body")"
  rm -f "$tmp_body"
}

echo "Creating developer terminal (${DEVELOPER_PROVIDER}/${DEVELOPER_PROFILE})..."
if [[ -n "$SESSION_NAME" ]]; then
  get_json "${CAO_BASE_URL}/sessions/${SESSION_NAME}"
  if [[ "$HTTP_STATUS" == "200" ]]; then
    post_get "${CAO_BASE_URL}/sessions/${SESSION_NAME}/terminals" \
      --data-urlencode "provider=${DEVELOPER_PROVIDER}" \
      --data-urlencode "agent_profile=${DEVELOPER_PROFILE}" \
      --data-urlencode "working_directory=${WORKING_DIRECTORY}"
    if [[ "$HTTP_STATUS" != "201" ]]; then
      echo "Failed to create developer terminal in existing session '${SESSION_NAME}' (HTTP ${HTTP_STATUS}):" >&2
      json_detail "$HTTP_BODY" >&2
      exit 1
    fi
    DEVELOPER_JSON="$HTTP_BODY"
  else
    post_get "${CAO_BASE_URL}/sessions" \
      --data-urlencode "provider=${DEVELOPER_PROVIDER}" \
      --data-urlencode "agent_profile=${DEVELOPER_PROFILE}" \
      --data-urlencode "working_directory=${WORKING_DIRECTORY}" \
      --data-urlencode "session_name=${SESSION_NAME}"
    if [[ "$HTTP_STATUS" != "201" ]]; then
      echo "Failed to create developer session '${SESSION_NAME}' (HTTP ${HTTP_STATUS}):" >&2
      json_detail "$HTTP_BODY" >&2
      exit 1
    fi
    DEVELOPER_JSON="$HTTP_BODY"
  fi
else
  post_get "${CAO_BASE_URL}/sessions" \
    --data-urlencode "provider=${DEVELOPER_PROVIDER}" \
    --data-urlencode "agent_profile=${DEVELOPER_PROFILE}" \
    --data-urlencode "working_directory=${WORKING_DIRECTORY}"
  if [[ "$HTTP_STATUS" != "201" ]]; then
    echo "Failed to create developer session (HTTP ${HTTP_STATUS}):" >&2
    json_detail "$HTTP_BODY" >&2
    exit 1
  fi
  DEVELOPER_JSON="$HTTP_BODY"
fi

DEVELOPER_ID="$(json_get "$DEVELOPER_JSON" "id" || true)"
SESSION_NAME_ACTUAL="$(json_get "$DEVELOPER_JSON" "session_name" || true)"
if [[ -z "$DEVELOPER_ID" || -z "$SESSION_NAME_ACTUAL" ]]; then
  echo "Failed to parse developer creation response:" >&2
  echo "$DEVELOPER_JSON" >&2
  exit 1
fi
echo "Developer terminal id: ${DEVELOPER_ID}"
echo "Session name: ${SESSION_NAME_ACTUAL}"

echo "Creating reviewer terminal (${REVIEWER_PROVIDER}/${REVIEWER_PROFILE})..."
post_get "${CAO_BASE_URL}/sessions/${SESSION_NAME_ACTUAL}/terminals" \
  --data-urlencode "provider=${REVIEWER_PROVIDER}" \
  --data-urlencode "agent_profile=${REVIEWER_PROFILE}" \
  --data-urlencode "working_directory=${WORKING_DIRECTORY}"
if [[ "$HTTP_STATUS" != "201" ]]; then
  echo "Failed to create reviewer terminal (HTTP ${HTTP_STATUS}):" >&2
  json_detail "$HTTP_BODY" >&2
  exit 1
fi
REVIEWER_JSON="$HTTP_BODY"
REVIEWER_ID="$(json_get "$REVIEWER_JSON" "id" || true)"
if [[ -z "$REVIEWER_ID" ]]; then
  echo "Failed to parse reviewer creation response:" >&2
  echo "$REVIEWER_JSON" >&2
  exit 1
fi
echo "Reviewer terminal id: ${REVIEWER_ID}"

echo "Creating analyzer terminal (${ANALYZER_PROVIDER}/${ANALYZER_PROFILE})..."
post_get "${CAO_BASE_URL}/sessions/${SESSION_NAME_ACTUAL}/terminals" \
  --data-urlencode "provider=${ANALYZER_PROVIDER}" \
  --data-urlencode "agent_profile=${ANALYZER_PROFILE}" \
  --data-urlencode "working_directory=${WORKING_DIRECTORY}"
if [[ "$HTTP_STATUS" != "201" ]]; then
  echo "Failed to create analyzer terminal (HTTP ${HTTP_STATUS}):" >&2
  json_detail "$HTTP_BODY" >&2
  exit 1
fi
ANALYZER_JSON="$HTTP_BODY"
ANALYZER_ID="$(json_get "$ANALYZER_JSON" "id" || true)"
if [[ -z "$ANALYZER_ID" ]]; then
  echo "Failed to parse analyzer creation response:" >&2
  echo "$ANALYZER_JSON" >&2
  exit 1
fi
echo "Analyzer terminal id: ${ANALYZER_ID}"

echo ""
echo "Summary:"
echo "  session_name: ${SESSION_NAME_ACTUAL}"
echo "  developer_terminal_id: ${DEVELOPER_ID}"
echo "  reviewer_terminal_id: ${REVIEWER_ID}"
echo "  analyzer_terminal_id: ${ANALYZER_ID}"

if [[ "$RUN_ORCHESTRATE" != "1" ]]; then
  echo "RUN_ORCHESTRATE=0, skip orchestrate run."
  exit 0
fi

echo ""
echo "Starting orchestrate run..."
cd "$WORKING_DIRECTORY"
uv run cao orchestrate run \
  --design "$DESIGN_DOC" \
  --plan "$PLAN_DOC" \
  --phase-name "$PHASE_NAME" \
  --developer-terminal "$DEVELOPER_ID" \
  --reviewer-terminal "$REVIEWER_ID" \
  --intent-provider analyzer \
  --intent-terminal "$ANALYZER_ID" \
  --run-root "$RUN_ROOT" \
  --poll-interval "$POLL_INTERVAL" \
  --response-timeout "$RESPONSE_TIMEOUT" \
  --commit-max-retries "$COMMIT_MAX_RETRIES"
