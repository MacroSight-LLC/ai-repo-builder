#!/bin/bash
set -e

SPEC=${1:-"specs/example-spec.yaml"}
MAX_ITERS=${MAX_ITERATIONS:-50}
WORKSPACE=${WORKSPACE:-$(pwd)}
PASS=false

echo "🚀 Starting ai-repo-builder"
echo "📋 Spec: $SPEC"
echo "🔁 Max iterations: $MAX_ITERS"

# Start all MCP containers
docker compose up -d --wait
echo "✅ All MCP containers running"

for i in $(seq 1 $MAX_ITERS); do
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔄 Iteration $i / $MAX_ITERS"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Run CUGA agent
  docker exec cuga python -m cuga.main \
    --spec "$SPEC" \
    --tools mcp_servers.yaml \
    --policy policies/coding-policy.yaml \
    --output "$WORKSPACE/output" || true

  # Commit iteration
  git add -A
  git commit -m "iter($i): agent output" --allow-empty

  # Validate
  if bash scripts/validate.sh; then
    echo ""
    echo "✅ Validation passed on iteration $i"
    PASS=true
    break
  else
    echo "⚠️  Validation failed — retrying..."
  fi
done

if [ "$PASS" = true ]; then
  echo ""
  echo "🎉 Build complete! Opening PR..."
  gh pr create \
    --title "feat: AI-built repo (iter $i)" \
    --body "Autonomously built by ai-repo-builder using CUGA + Ralph loop" \
    --base main \
    --head "$(git branch --show-current)"
else
  echo "❌ Max iterations reached without passing validation"
  exit 1
fi
