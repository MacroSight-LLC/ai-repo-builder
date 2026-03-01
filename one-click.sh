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
# ──────────────────────────────────────────────────────────────────

# Clear any stale shell env vars that conflict with .env
unset OPENAI_API_KEY OPENAI_BASE_URL MODEL_NAME AGENT_SETTING_CONFIG IBMCLOUD_API_KEY WATSONX_API_KEY WATSONX_APIKEY WATSONX_PROJECT_ID WATSONX_URL 2>/dev/null || true

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

SPEC=${1:-"specs/example-spec.yaml"}
MAX_ITERS=${MAX_ITERATIONS:-5}
WORKSPACE=$(pwd)

echo "🚀 Starting ai-repo-builder (Smart Ralph loop)"
echo "📋 Spec: $SPEC"
echo "🔁 Max iterations: $MAX_ITERS"

# Start MCP containers (only the ones with valid images)
MCP_SERVICES="context7-mcp filesystem-mcp github-mcp langfuse-db"
echo "🐳 Starting MCP services: $MCP_SERVICES"
docker compose up -d $MCP_SERVICES 2>/dev/null || echo "⚠️  Docker Compose not available — using local MCP servers"
echo "✅ MCP containers started"

# Wait for health endpoints
echo "⏳ Waiting for MCP servers to be ready..."
for endpoint in "http://localhost:8004/healthz" "http://localhost:8007/healthz"; do
  for attempt in $(seq 1 30); do
    if curl -sf "$endpoint" > /dev/null 2>&1; then
      break
    fi
    sleep 1
  done
done
echo "✅ MCP servers ready"

# Use localhost URLs for local execution (containers expose ports to host)
export MCP_SERVERS_FILE="$WORKSPACE/mcp_servers_local.yaml"
export SETTINGS_TOML_PATH="$WORKSPACE/src/cuga/settings.toml"
export PYTHONPATH="$WORKSPACE/src"

# Generate local MCP config pointing to localhost instead of container names
cat > "$WORKSPACE/mcp_servers_local.yaml" << 'EOF'
mcpServers:
  github:
    url: http://localhost:8003
    transport: http
    description: GitHub - repos, PRs, issues, branches, commits

  context7:
    url: http://localhost:8004/sse
    transport: sse
    description: Context7 - accurate library docs, anti-hallucination

  filesystem:
    url: http://localhost:8007/sse
    transport: sse
    description: Filesystem - read, write, search files in workspace
EOF

# ── Run the Python build loop ──────────────────────────────────
# This replaces the old bash for-loop that cold-restarted the agent.
# The Python loop keeps the agent warm and feeds validation errors
# back as context for self-correction.

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

# Cleanup temp config
rm -f "$WORKSPACE/mcp_servers_local.yaml"

if [ "$BUILD_EXIT" -eq 0 ]; then
    echo ""
    echo "🎉 Build complete! Creating PR..."

    # Commit and push
    git add -A
    git commit -m "feat: AI-built repo via Smart Ralph loop" --allow-empty

    gh pr create \
        --title "feat: AI-built repo (Smart Ralph)" \
        --body "Autonomously built by ai-repo-builder using CUGA + Smart Ralph build loop" \
        --base main \
        --head "$(git branch --show-current)" 2>/dev/null || \
        echo "ℹ️  Skipped PR creation (gh CLI not available or already on main)"
else
    echo "❌ Build loop failed"
    exit 1
fi
