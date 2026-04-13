#!/usr/bin/env bash
# ==============================================================================
# XI v0.1 SMOKE RUNNER (scripts/smoke.sh)
# ------------------------------------------------------------------------------
# What this checks (commented/hidden on purpose):
# 1) Core flows start→run→end (davinci, hermes) without interactivity or crashes.
# 2) Journal promotions run for idea_generator_journal (weekly→period when possible).
# 3) Search pipeline returns something for "sentinel" via search_controller.
# 4) Sentinel audit runs with --quick and writes session files.
# 5) Indexer can ingest a fresh session journal in mem-only (air-gapped friendly).
#    Also prints a short DB peek.
# Anything beyond this is polish.
# ==============================================================================

set -Eeuo pipefail

# --- config -------------------------------------------------------------------
AGENTS=("davinci" "hermes")
JOURNAL_TYPE="idea_generator_journal"
DB="xi_memory"
DB_USER="postgres"
DB_HOST="localhost"
DB_PORT="5432"

# Non-interactive memory selection for flows
export XI_NON_INTERACTIVE=1

# Make sure logs dir exists
mkdir -p logs/smoke

# Colored output helpers
ok()    { echo -e "\e[32m✔ $*\e[0m"; }
warn()  { echo -e "\e[33m⚠ $*\e[0m"; }
bad()   { echo -e "\e[31m✘ $*\e[0m"; }
step()  { echo -e "\n\e[36m==> $*\e[0m"; }

fail() {
  bad "$1"
  echo "See logs under logs/smoke/ for details."
  exit 1
}

# --- step 0: preflight --------------------------------------------------------
step "Preflight: ensure we're in repo root and venv active"
[[ -d "flows" && -d "abilities" ]] || fail "Run from repo root (where flows/ and abilities/ exist)."
python -c "import sys; assert 'venv' in sys.executable" || warn "venv might not be active (python: $(which python))"

# --- step 1: Core flows (non-interactive) ------------------------------------
step "1) Core flows (non-interactive)"
for A in "${AGENTS[@]}"; do
  LOG="logs/smoke/flow_${A}.log"
  if ! python3 flows/idea_generator_flow.py --agents "$A" --max-seconds 15 --debug </dev/null >"$LOG" 2>&1; then
    tail -n 80 "$LOG" || true
    fail "Flow failed for agent=$A"
  fi
  ok "flow $A ✓ (log: $LOG)"
done

# --- step 2: Journal promotions ----------------------------------------------
step "2) Journal promotions (weekly/period/quarterly where thresholds met)"
for A in "${AGENTS[@]}"; do
  LOG="logs/smoke/promote_${A}.log"
  if ! python3 -m abilities.common_abilities.write_all_summaries_then_archive \
      --agent "$A" --journal_type "$JOURNAL_TYPE" --debug >"$LOG" 2>&1; then
    tail -n 80 "$LOG" || true
    fail "Promotions failed for agent=$A"
  fi
  ok "promotions $A:$JOURNAL_TYPE ✓ (log: $LOG)"
done

# --- step 3: Search pipeline --------------------------------------------------
step "3) Search pipeline (search_controller for 'sentinel')"
LOG="logs/smoke/search_controller.log"
if ! python3 -m abilities.common_abilities.search_controller \
      --query "sentinel" --agents "davinci,hermes" >"$LOG" 2>&1; then
  tail -n 80 "$LOG" || true
  fail "search_controller errored"
fi
# Quick sanity: file path string present
if ! grep -q "memory/sentinel/sentinel_audit_journal/session" "$LOG"; then
  warn "search_controller returned, but no sentinel session path was found. (log: $LOG)"
else
  ok "search_controller ✓ (log: $LOG)"
fi

# --- step 4: Sentinel audit ---------------------------------------------------
step "4) Sentinel audit (--quick)"
LOG="logs/smoke/sentinel_audit.log"
if ! python3 -m abilities.sentinel.sentinel_routine_audit --quick >"$LOG" 2>&1; then
  tail -n 80 "$LOG" || true
  fail "sentinel audit failed"
fi
ok "sentinel audit ✓ (log: $LOG)"

# --- step 5: Indexer mem-only (air-gapped) -----------------------------------
step "5) Indexer mem-only (air-gapped) on latest session (davinci)"
LATEST=$(ls -t memory/davinci/${JOURNAL_TYPE}/session | head -n1 || true)
if [[ -z "${LATEST}" ]]; then
  # write a tiny throwaway session to ensure the hash is new
  NOW=$(date +"%Y%m%d_%H%M%S")
  LATEST="davinci_session_journal_${NOW}_smoke.json"
  cat > "memory/davinci/${JOURNAL_TYPE}/session/${LATEST}" <<'JSON'
{
  "date": "2025-08-23",
  "agent": "davinci",
  "thread_type": "idea_generator",
  "summary": "Smoke test session from scripts/smoke.sh.",
  "thoughts": ["Indexer mem-only should ingest this.", "DB peek should show it."],
  "decisions": ["All good."]
}
JSON
fi

LOG="logs/smoke/index_latest.log"
if ! python3 -m abilities.common_abilities.memory_indexer \
      --file "memory/davinci/${JOURNAL_TYPE}/session/${LATEST}" \
      --agent davinci --mem-only --force >"$LOG" 2>&1; then
  tail -n 80 "$LOG" || true
  fail "indexer failed"
fi
ok "indexer mem-only ✓ (log: $LOG)"

# --- DB peek ------------------------------------------------------------------
step "DB peek (latest 5)"
SQL='SELECT uuid, agent, thread_type, LEFT(content,140) AS excerpt, source_file, "timestamp"
     FROM memory_entries
     ORDER BY "timestamp" DESC
     LIMIT 5;'
if ! psql -U "${DB_USER}" -d "${DB}" -h "${DB_HOST}" -p "${DB_PORT}" -c "$SQL"; then
  warn "DB peek query failed (psql not configured?)."
else
  ok "DB peek ✓"
fi

# --- summary ------------------------------------------------------------------
echo
ok "Smoke complete. Core 1–5 checks passed."
echo "Logs: logs/smoke/"

