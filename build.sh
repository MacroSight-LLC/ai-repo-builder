#!/bin/bash
# ============================================================
# AI Repo Builder — Plain English → Working Project
# ============================================================
# Usage:
#   ./build.sh "Build me a REST API for managing invoices"
#   ./build.sh --from-file my-idea.txt
#   ./build.sh --from-spec specs/my-project-20260228.yaml
#   ./build.sh --spec-only "A Next.js dashboard"
#   ./build.sh   # interactive mode
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Load .env ──────────────────────────────────────────────────
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Clear stale overrides that conflict with .env
unset OPENAI_API_KEY OPENAI_BASE_URL MODEL_NAME 2>/dev/null || true

# ── Preflight checks ──────────────────────────────────────────
echo "🔍 Preflight checks..."

# Activate venv
if [ -d .venv ]; then
  source .venv/bin/activate
  echo "   ✅ Virtual environment activated"
else
  echo "   ⚠️  No .venv found — using system Python"
fi

# Check required env vars
MISSING_VARS=()
for var in WATSONX_API_KEY WATSONX_PROJECT_ID; do
  if [ -z "${!var:-}" ]; then
    MISSING_VARS+=("$var")
  fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
  echo "❌ Missing required environment variables:"
  for var in "${MISSING_VARS[@]}"; do
    echo "   - $var"
  done
  echo "   Set them in .env or export them."
  exit 1
fi
echo "   ✅ Environment variables set"

export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
export SETTINGS_TOML_PATH="$SCRIPT_DIR/src/cuga/settings.toml"

# Map env vars for MCP servers that expect specific names
export GITHUB_PERSONAL_ACCESS_TOKEN="${GITHUB_TOKEN:-}"

# ── Start backing services ─────────────────────────────────────
# MCP dev-tool servers now run locally via stdio transport (npx).
# Only databases need Docker.
if command -v docker &>/dev/null; then
  echo "🐳 Starting backing services..."
  docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d \
    postgres-dev redis-dev 2>/dev/null && \
    echo "   ✅ Backing services started" || \
    echo "   ⚠️  docker compose failed — database tools may not work"

  # Wait for databases
  echo "   ⏳ Waiting for databases..."
  timeout 30 bash -c 'until docker compose exec -T postgres-dev pg_isready -U cuga 2>/dev/null; do sleep 1; done' \
    && echo "   ✅ PostgreSQL ready" \
    || echo "   ⚠️  PostgreSQL not ready (non-blocking)"
  timeout 10 bash -c 'until docker compose exec -T redis-dev redis-cli ping 2>/dev/null | grep -q PONG; do sleep 1; done' \
    && echo "   ✅ Redis ready" \
    || echo "   ⚠️  Redis not ready (non-blocking)"
else
  echo "   ⚠️  Docker not installed — database tools will not be available"
fi

# ── Check Node.js 18+ (required for MCP tool servers) ─────────
if command -v node &>/dev/null; then
  NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 18 ]; then
    echo "   ✅ Node.js v$(node -v | sed 's/v//')"
  else
    echo "   ❌ Node.js v$NODE_MAJOR found — need 18+"
    echo "      Upgrade: brew install node  (or nvm install 20)"
    exit 1
  fi
else
  echo "   ❌ Node.js not found — MCP tools require Node.js 18+"
  echo "      Install: brew install node  (or nvm install 20)"
  exit 1
fi

if ! command -v npx &>/dev/null; then
  echo "   ⚠️  npx not found (should come with Node.js)"
else
  echo "   ✅ npx available"
fi

export MCP_SERVERS_FILE="$SCRIPT_DIR/mcp_servers_local.yaml"

# ── Ensure output directory exists ─────────────────────────────
mkdir -p "$SCRIPT_DIR/output"

# ── Run the pipeline ──────────────────────────────────────────
echo ""
echo "🏗️  AI Repo Builder — Plain English → Working Project"
echo ""

python -m cuga.generate \
  --tools "$LOCAL_MCP" \
  --policy "$SCRIPT_DIR/policies/coding-policy.yaml" \
  --output "$SCRIPT_DIR/output" \
  "$@"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo ""
  echo "════════════════════════════════════════════"
  echo "  ✅ Done! Check the output/ directory."
  echo "════════════════════════════════════════════"
else
  echo ""
  echo "════════════════════════════════════════════"
  echo "  ❌ Build failed (exit code: $EXIT_CODE)"
  echo "  Check logs above for details."
  echo "════════════════════════════════════════════"
fi

exit $EXIT_CODE
