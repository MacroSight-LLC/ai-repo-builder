#!/bin/bash
set -e

# ──────────────────────────────────────────────────────────────────
# one-click.sh — Thin wrapper around the Python build loop.
#
# Usage:
#   ./one-click.sh                       # Uses specs/example-spec.yaml
#   ./one-click.sh specs/my-project.yaml
#   MAX_ITERATIONS=10 ./one-click.sh specs/big-project.yaml
#
# The real work happens in `python -m cuga.build_loop` which runs an
# in-process build→validate→feedback→retry loop (Smart Ralph).
#
# MCP servers run locally via stdio transport (npx spawns them on
# demand).  Only databases need Docker.
# ──────────────────────────────────────────────────────────────────

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
cd "$WORKSPACE"

# ── Load .env ──────────────────────────────────────────────────
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "✅ Loaded .env"
else
  echo "⚠️  No .env found — using existing environment"
fi

# ── Prerequisite checks ────────────────────────────────────────
# Node.js 18+ is required for MCP servers (npx)
if command -v npx &>/dev/null; then
    NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_VERSION" -lt 18 ]; then
        echo "❌ Node.js v$NODE_VERSION found — need 18+. Run: brew install node"
        exit 1
    fi
    echo "✅ Node.js v$(node -v | sed 's/v//') / npx available"
else
    echo "❌ npx not found. Install Node.js 18+: brew install node"
    exit 1
fi

# Python 3.10+ required
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "✅ Python $PY_VERSION"
else
    echo "❌ python3 not found"
    exit 1
fi

# Check at least one LLM provider
HAS_LLM=false
[ -n "${WATSONX_API_KEY:-}" ] && [ -n "${WATSONX_PROJECT_ID:-}" ] && HAS_LLM=true
[ -n "${OPENAI_API_KEY:-}" ] && HAS_LLM=true
[ -n "${GROQ_API_KEY:-}" ] && HAS_LLM=true
if [ "$HAS_LLM" = false ]; then
  echo "❌ No LLM provider configured. Set one in .env:"
  echo "   WATSONX_API_KEY + WATSONX_PROJECT_ID | OPENAI_API_KEY | GROQ_API_KEY"
  exit 1
fi
echo "✅ LLM credentials configured"

# Activate venv if present
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  echo "✅ Virtual environment activated"
fi

SPEC=${1:-"specs/example-spec.yaml"}
MAX_ITERS=${MAX_ITERATIONS:-5}

echo ""
echo "🚀 Starting ai-repo-builder (Smart Ralph loop)"
echo "📋 Spec: $SPEC"
echo "🔁 Max iterations: $MAX_ITERS"

# ── Start backing services (databases only) ────────────────────
if command -v docker &>/dev/null; then
  echo "🐳 Starting backing services..."
  docker compose -f "$WORKSPACE/docker-compose.yml" up -d \
    postgres-dev redis-dev langfuse-db 2>/dev/null && \
    echo "✅ Database containers started" || \
    echo "⚠️  Docker Compose unavailable — database features may not work"

  # Wait for Postgres (portable: works on macOS without coreutils)
  _timeout() { if command -v timeout &>/dev/null; then timeout "$@"; elif command -v gtimeout &>/dev/null; then gtimeout "$@"; else local secs=$1; shift; local end=$((SECONDS + secs)); while [ $SECONDS -lt $end ]; do "$@" && return 0; sleep 1; done; return 1; fi; }
  _timeout 30 bash -c 'until docker compose exec -T postgres-dev pg_isready -U cuga 2>/dev/null; do sleep 1; done' \
    && echo "✅ PostgreSQL ready" \
    || echo "⚠️  PostgreSQL not ready (non-blocking)"
else
  echo "⚠️  Docker not installed — database features won't work"
fi

# ── Configure paths ────────────────────────────────────────────
export MCP_SERVERS_FILE="$WORKSPACE/mcp_servers_local.yaml"
export SETTINGS_TOML_PATH="$WORKSPACE/src/cuga/settings.toml"
export PYTHONPATH="$WORKSPACE/src:${PYTHONPATH:-}"
export GITHUB_PERSONAL_ACCESS_TOKEN="${GITHUB_TOKEN:-}"

mkdir -p "$WORKSPACE/output"

# ── Run the Python build loop ──────────────────────────────────
LOOP_ARGS=(
    --spec "$SPEC"
    --tools "$MCP_SERVERS_FILE"
    --policy "policies/coding-policy.yaml"
    --output "$WORKSPACE/output"
    --max-iterations "$MAX_ITERS"
)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄 Running Smart Ralph build loop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python -m cuga.build_loop "${LOOP_ARGS[@]}"
BUILD_EXIT=$?

if [ "$BUILD_EXIT" -eq 0 ]; then
    echo ""
    echo "════════════════════════════════════════"
    echo "  ✅ Build complete! Check output/"
    echo "════════════════════════════════════════"

    # Optionally commit + PR (only on feature branches with gh CLI)
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
    if [ "$CURRENT_BRANCH" != "main" ] && command -v gh &>/dev/null; then
        git add -A
        git commit -m "feat: AI-built repo via Smart Ralph loop" --allow-empty
        gh pr create \
            --title "feat: AI-built repo (Smart Ralph)" \
            --body "Autonomously built by ai-repo-builder" \
            --base main \
            --head "$CURRENT_BRANCH" 2>/dev/null || \
            echo "ℹ️  PR creation skipped (already exists or gh not configured)"
    fi
else
    echo ""
    echo "════════════════════════════════════════"
    echo "  ❌ Build loop failed (exit: $BUILD_EXIT)"
    echo "════════════════════════════════════════"
    exit 1
fi
