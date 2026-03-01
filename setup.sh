#!/bin/bash
# ============================================================
# AI Repo Builder — First-Time Setup
# ============================================================
# Run once after cloning.  Handles everything a non-technical
# user needs to get started.
#
# Usage:
#   chmod +x setup.sh && ./setup.sh
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "🏗️  AI Repo Builder — First-Time Setup"
echo "════════════════════════════════════════"
echo ""

ERRORS=()

# ── 1. Check Python ────────────────────────────────────────
echo "1/7  Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        echo "     ✅ Python $PY_VERSION"
    else
        echo "     ❌ Python $PY_VERSION found — need 3.11+"
        ERRORS+=("Install Python 3.11+: brew install python@3.11")
    fi
else
    echo "     ❌ Python not found"
    ERRORS+=("Install Python: brew install python@3.11")
fi

# ── 2. Check Node.js ──────────────────────────────────────
echo "2/7  Checking Node.js..."
if command -v node &>/dev/null; then
    NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 18 ]; then
        echo "     ✅ Node.js v$(node -v | sed 's/v//')"
    else
        echo "     ❌ Node.js v$NODE_MAJOR — need 18+"
        ERRORS+=("Upgrade Node.js: brew install node")
    fi
else
    echo "     ❌ Node.js not found"
    ERRORS+=("Install Node.js 18+: brew install node")
fi

# ── 3. Check Docker ───────────────────────────────────────
echo "3/7  Checking Docker..."
if command -v docker &>/dev/null; then
    if docker info &>/dev/null 2>&1; then
        echo "     ✅ Docker running"
    else
        echo "     ⚠️  Docker installed but not running — start Docker Desktop"
    fi
else
    echo "     ⚠️  Docker not installed — database features won't work"
    echo "        Install: brew install --cask docker"
fi

# ── 4. Create virtual environment ─────────────────────────
echo "4/7  Setting up Python environment..."
if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "     ✅ Created .venv"
else
    echo "     ✅ .venv exists"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ── 5. Install dependencies ───────────────────────────────
echo "5/7  Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]" 2>/dev/null || pip install --quiet -e .
echo "     ✅ Dependencies installed"

# ── 6. Set up .env ────────────────────────────────────────
echo "6/7  Checking environment..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "     ✅ Created .env from .env.example"
        echo "     ⚠️  Edit .env and add your API keys"
    else
        echo "     ❌ No .env or .env.example found"
        ERRORS+=("Create .env with your API keys (see README)")
    fi
else
    echo "     ✅ .env exists"
fi

# Check required keys
set -a
# shellcheck disable=SC1091
source .env 2>/dev/null || true
set +a
MISSING_KEYS=()
[ -z "${WATSONX_API_KEY:-}" ] && MISSING_KEYS+=("WATSONX_API_KEY")
[ -z "${WATSONX_PROJECT_ID:-}" ] && MISSING_KEYS+=("WATSONX_PROJECT_ID")

if [ ${#MISSING_KEYS[@]} -gt 0 ]; then
    echo ""
    echo "     ⚠️  Missing API keys in .env:"
    for key in "${MISSING_KEYS[@]}"; do
        echo "        - $key"
    done
    echo ""
    echo "     Get WatsonX credentials:"
    echo "       1. Go to https://cloud.ibm.com/watsonx"
    echo "       2. Create a project"
    echo "       3. Go to project settings → API key"
    echo "       4. Copy API key and project ID into .env"
fi

[ -z "${GITHUB_TOKEN:-}" ] && echo "     ⚠️  GITHUB_TOKEN not set — repo publishing will be skipped"

# ── 7. Verify installation ────────────────────────────────
echo "7/7  Verifying installation..."
VERIFY_FAILED=0
python -c "from cuga.generate import main; print('     ✅ Pipeline imports OK')" 2>/dev/null || {
    echo "     ❌ Pipeline import failed"
    ERRORS+=("Run: pip install -e '.[dev]'")
    VERIFY_FAILED=1
}
if [ "$VERIFY_FAILED" -eq 0 ]; then
    python -c "from cuga.spec_generator import build_spec_prompt; print('     ✅ Spec generator OK')" 2>/dev/null || {
        echo "     ❌ Spec generator import failed"
        ERRORS+=("Check src/cuga/spec_generator.py")
    }
    python -c "from cuga.post_build import post_build_validate; print('     ✅ Post-build validator OK')" 2>/dev/null || {
        echo "     ❌ Post-build validator import failed"
        ERRORS+=("Check src/cuga/post_build.py")
    }
fi

# ── Summary ───────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
if [ ${#ERRORS[@]} -gt 0 ]; then
    echo "  ⚠️  Setup incomplete — fix these:"
    echo ""
    for err in "${ERRORS[@]}"; do
        echo "    → $err"
    done
    echo ""
    echo "  Then re-run: ./setup.sh"
else
    echo "  ✅ Setup complete!"
    echo ""
    echo "  Quick start:"
    echo "    ./build.sh \"Build me a REST API for managing invoices\""
    echo ""
    echo "  With GitHub repo:"
    echo "    ./build.sh --github \"Build me a task manager API\""
    echo ""
    echo "  Dry run (preview prompt):"
    echo "    ./build.sh --dry-run \"A CLI calculator\""
    echo ""
    echo "  See all options:"
    echo "    ./build.sh --help"
fi
echo "════════════════════════════════════════"
echo ""
