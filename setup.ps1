# =============================================================================
# Sentinel — One-Click Windows Setup Script
# =============================================================================
# Usage:
#   .\setup.ps1                # run normally
#   .\setup.ps1 -WhatIf       # preview actions without executing
#   .\setup.ps1 -Verbose       # show detailed output
# =============================================================================

param(
    [switch]$WhatIf,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Step {
    param([string]$Message)
    Write-Host "`n==> " -ForegroundColor Cyan -NoNewline
    Write-Host $Message -ForegroundColor White
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] " -ForegroundColor Red -NoNewline
    Write-Host $Message
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Check Python version (>= 3.11)
# ---------------------------------------------------------------------------
Write-Step "Checking Python version..."

try {
    $pythonCmd = Get-Command python -ErrorAction Stop
    $pythonVersion = & python --version 2>&1
    Write-Host "  Found: $pythonVersion at $($pythonCmd.Source)"

    $versionMatch = $pythonVersion -match "Python (\d+)\.(\d+)"
    if ($versionMatch) {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
            Write-Fail "Python >= 3.11 is required. Found $major.$minor. Please upgrade Python."
        }
    }
    Write-Ok "Python version is compatible."
}
catch {
    Write-Fail "Python is not installed or not in PATH. Please install Python >= 3.11 from https://www.python.org/"
}

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
Write-Step "Setting up virtual environment..."

if (-not $WhatIf) {
    if (-not (Test-Path ".venv")) {
        Write-Host "  Creating virtual environment in .venv..."
        & python -m venv .venv
        Write-Ok "Virtual environment created."
    }
    else {
        Write-Ok "Virtual environment already exists."
    }
}
else {
    Write-Host "  [WhatIf] Would create virtual environment in .venv"
}

# ---------------------------------------------------------------------------
# 3. Install dependencies
# ---------------------------------------------------------------------------
Write-Step "Installing dependencies..."

if (-not $WhatIf) {
    $venvPython = ".\.venv\Scripts\python.exe"
    $venvPip = ".\.venv\Scripts\pip.exe"

    Write-Host "  Upgrading pip..."
    & $venvPython -m pip install --upgrade pip setuptools wheel 2>&1 | Out-Null

    Write-Host "  Installing sentinel with dev dependencies..."
    & $venvPip install -e ".[dev]" 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to install dependencies. Check the output above for errors."
    }
    Write-Ok "Dependencies installed successfully."
}
else {
    Write-Host "  [WhatIf] Would install dependencies via pip"
}

# ---------------------------------------------------------------------------
# 4. Create .env from .env.example
# ---------------------------------------------------------------------------
Write-Step "Setting up environment configuration..."

if (-not $WhatIf) {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Ok "Created .env from .env.example. Edit .env to configure your settings."
    }
    else {
        Write-Ok ".env already exists. Skipping."
    }
}
else {
    Write-Host "  [WhatIf] Would copy .env.example to .env"
}

# ---------------------------------------------------------------------------
# 5. Create traces/ directory
# ---------------------------------------------------------------------------
Write-Step "Creating traces directory..."

if (-not $WhatIf) {
    if (-not (Test-Path "traces")) {
        New-Item -ItemType Directory -Path "traces" | Out-Null
        Write-Ok "traces/ directory created."
    }
    else {
        Write-Ok "traces/ directory already exists."
    }
}
else {
    Write-Host "  [WhatIf] Would create traces/ directory"
}

# ---------------------------------------------------------------------------
# 6. Run tests
# ---------------------------------------------------------------------------
Write-Step "Running tests..."

if (-not $WhatIf) {
    $venvPython = ".\.venv\Scripts\python.exe"
    & $venvPython -m pytest --tb=short 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Some tests failed. Review the output above."
    }
    Write-Ok "All tests passed."
}
else {
    Write-Host "  [WhatIf] Would run pytest"
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  Sentinel setup complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Edit .env to configure your API key and settings"
Write-Host "  2. Start the gateway:  python -m sentinel.gateway"
Write-Host "     or:                  sentinel-gateway"
Write-Host "  3. Start the dashboard: sentinel-dashboard"
Write-Host "     or:                  streamlit run sentinel/dashboard/app.py"
Write-Host "  4. Visit http://localhost:8501 for the dashboard"
Write-Host ""
