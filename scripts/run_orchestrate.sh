#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 6 ]]; then
  cat <<USAGE
Usage:
  scripts/run_orchestrate.sh <design_doc> <plan_doc> <phase> [developer_terminal_id|-] [reviewer_terminal_id|-] [run_root]

Example:
  scripts/run_orchestrate.sh \
    /abs/path/design.md \
    /abs/path/plan.md \
    phase-1 \
    a1b2c3d4 \
    e5f6a7b8 \
    .orchestrator/runs

Environment overrides (optional):
  CAO_BASE_URL (default: http://localhost:9889)
  SESSION_NAME (default: empty)
  LAUNCH_DEVELOPER_AGENT (default: 0; set to 1 to auto-launch)
  DEVELOPER_PROVIDER (default: claude_code)
  DEVELOPER_PROFILE (default: developer)
  LAUNCH_REVIEWER_AGENT (default: 0; set to 1 to auto-launch)
  REVIEWER_PROVIDER (default: codex)
  REVIEWER_PROFILE (default: reviewer)
  MAX_REVIEW_ROUNDS (default: 8)
  POLL_INTERVAL (default: 2)
  RESPONSE_TIMEOUT (default: 1800)
  COMMIT_MAX_RETRIES (default: 1)
  INTENT_PROVIDER (default: reviewer; options: none|developer|reviewer|analyzer)
  INTENT_TERMINAL (default: empty)
  LAUNCH_INTENT_AGENT (default: 0; set to 1 to auto-launch)
  INTENT_AGENT_PROVIDER (default: claude_code)
  INTENT_AGENT_PROFILE (default: reviewer)
USAGE
  exit 1
fi

DESIGN_DOC="$1"
PLAN_DOC="$2"
PHASE="$3"
DEVELOPER_TERMINAL="${4:-}"
REVIEWER_TERMINAL="${5:-}"
RUN_ROOT="${6:-.orchestrator/runs}"

if [[ "$DEVELOPER_TERMINAL" == "-" ]]; then
  DEVELOPER_TERMINAL=""
fi
if [[ "$REVIEWER_TERMINAL" == "-" ]]; then
  REVIEWER_TERMINAL=""
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
SESSION_NAME="${SESSION_NAME:-}"
LAUNCH_DEVELOPER_AGENT="${LAUNCH_DEVELOPER_AGENT:-0}"
DEVELOPER_PROVIDER="${DEVELOPER_PROVIDER:-claude_code}"
DEVELOPER_PROFILE="${DEVELOPER_PROFILE:-developer}"
LAUNCH_REVIEWER_AGENT="${LAUNCH_REVIEWER_AGENT:-0}"
REVIEWER_PROVIDER="${REVIEWER_PROVIDER:-codex}"
REVIEWER_PROFILE="${REVIEWER_PROFILE:-reviewer}"
MAX_REVIEW_ROUNDS="${MAX_REVIEW_ROUNDS:-8}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"
RESPONSE_TIMEOUT="${RESPONSE_TIMEOUT:-1800}"
COMMIT_MAX_RETRIES="${COMMIT_MAX_RETRIES:-1}"
INTENT_PROVIDER="${INTENT_PROVIDER:-reviewer}"
INTENT_TERMINAL="${INTENT_TERMINAL:-}"
LAUNCH_INTENT_AGENT="${LAUNCH_INTENT_AGENT:-0}"
INTENT_AGENT_PROVIDER="${INTENT_AGENT_PROVIDER:-claude_code}"
INTENT_AGENT_PROFILE="${INTENT_AGENT_PROFILE:-reviewer}"

CMD=(
  uv run cao orchestrate run
  --design "$DESIGN_DOC"
  --plan "$PLAN_DOC"
  --phase-name "$PHASE"
  --run-root "$RUN_ROOT"
  --cao-base-url "$CAO_BASE_URL"
  --max-review-rounds "$MAX_REVIEW_ROUNDS"
  --poll-interval "$POLL_INTERVAL"
  --response-timeout "$RESPONSE_TIMEOUT"
  --commit-max-retries "$COMMIT_MAX_RETRIES"
  --intent-provider "$INTENT_PROVIDER"
)

if [[ -n "$SESSION_NAME" ]]; then
  CMD+=(--session-name "$SESSION_NAME")
fi

if [[ -n "$DEVELOPER_TERMINAL" ]]; then
  CMD+=(--developer-terminal "$DEVELOPER_TERMINAL")
fi

if [[ -n "$REVIEWER_TERMINAL" ]]; then
  CMD+=(--reviewer-terminal "$REVIEWER_TERMINAL")
fi

if [[ "$LAUNCH_DEVELOPER_AGENT" == "1" ]]; then
  CMD+=(--launch-developer-agent)
  CMD+=(--developer-provider "$DEVELOPER_PROVIDER")
  CMD+=(--developer-profile "$DEVELOPER_PROFILE")
fi

if [[ "$LAUNCH_REVIEWER_AGENT" == "1" ]]; then
  CMD+=(--launch-reviewer-agent)
  CMD+=(--reviewer-provider "$REVIEWER_PROVIDER")
  CMD+=(--reviewer-profile "$REVIEWER_PROFILE")
fi

if [[ -n "$INTENT_TERMINAL" ]]; then
  CMD+=(--intent-terminal "$INTENT_TERMINAL")
fi

if [[ "$LAUNCH_INTENT_AGENT" == "1" ]]; then
  CMD+=(--launch-intent-agent)
  CMD+=(--intent-agent-provider "$INTENT_AGENT_PROVIDER")
  CMD+=(--intent-agent-profile "$INTENT_AGENT_PROFILE")
fi

"${CMD[@]}"
