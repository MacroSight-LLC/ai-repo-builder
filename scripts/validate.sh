#!/bin/bash
set -e

echo "🔍 Running validation..."
PASS=true

# Python tests
echo "→ Running pytest..."
if ! python -m pytest tests/ -q --tb=short 2>/dev/null; then
  echo "❌ Tests failed"
  PASS=false
fi

# Linting
echo "→ Running linting..."
if ! python -m ruff check src/ 2>/dev/null; then
  echo "⚠️  Linting issues found"
fi

# Type check
echo "→ Running type check..."
if ! python -m mypy src/ --ignore-missing-imports -q 2>/dev/null; then
  echo "⚠️  Type check issues found"
fi

# Docker build check
echo "→ Checking Docker build..."
if ! docker build -q . 2>/dev/null; then
  echo "❌ Docker build failed"
  PASS=false
fi

if [ "$PASS" = true ]; then
  echo "✅ All validation checks passed"
  exit 0
else
  echo "❌ Validation failed"
  exit 1
fi
