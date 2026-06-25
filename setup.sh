#!/usr/bin/env bash
# =============================================================================
# Sentinel — One-Click Linux / macOS Setup Script
# =============================================================================
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
step() {
    echo -e "\n${CYAN}==>${NC} ${WHITE}$1${NC}"
}

ok() {
    echo -e "  ${GREEN}[OK]${NC} $1"
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Check Python version (>= 3.11)
# ---------------------------------------------------------------------------
step "Checking Python version..."

if ! command -v python3 &>/dev/null; then
    fail "Python3 is not installed. Please install Python >= 3.11."
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "  Found: $PYTHON_VERSION"

MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    fail "Python >= 3.11 is required. Found $MAJOR.$MINOR. Please upgrade Python."
fi

ok "Python version is compatible ($MAJOR.$MINOR)."

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
step "Setting up virtual environment..."

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment in .venv..."
    python3 -m venv .venv
    ok "Virtual environment created."
else
    ok "Virtual environment already exists."
fi

# ---------------------------------------------------------------------------
# 3. Install dependencies
# ---------------------------------------------------------------------------
step "Installing dependencies..."

VENV_PYTHON=".venv/bin/python"
VENV_PIP=".venv/bin/pip"

echo "  Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1

echo "  Installing sentinel with dev dependencies..."
"$VENV_PIP" install -e ".[dev]"

if [ $? -ne 0 ]; then
    fail "Failed to install dependencies. Check the output above for errors."
fi

ok "Dependencies installed successfully."

# ---------------------------------------------------------------------------
# 4. Create .env from .env.example
# ---------------------------------------------------------------------------
step "Setting up environment configuration..."

if [ ! -f ".env" ]; then
    cp .env.example .env
    ok "Created .env from .env.example. Edit .env to configure your settings."
else
    ok ".env already exists. Skipping."
fi

# ---------------------------------------------------------------------------
# 5. Create traces/ directory
# ---------------------------------------------------------------------------
step "Creating traces directory..."

if [ ! -d "traces" ]; then
    mkdir -p traces
    ok "traces/ directory created."
else
    ok "traces/ directory already exists."
fi

# ---------------------------------------------------------------------------
# 6. Run tests
# ---------------------------------------------------------------------------
step "Running tests..."

"$VENV_PYTHON" -m pytest --tb=short

if [ $? -ne 0 ]; then
    fail "Some tests failed. Review the output above."
fi

ok "All tests passed."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  Sentinel setup complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Edit .env to configure your API key and settings"
echo "  2. Start the gateway:  python -m sentinel.gateway"
echo "     or:                  sentinel-gateway"
echo "  3. Start the dashboard: sentinel-dashboard"
echo "     or:                  streamlit run sentinel/dashboard/app.py"
echo "  4. Visit http://localhost:8501 for the dashboard"
echo ""
