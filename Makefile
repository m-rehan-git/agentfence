# =============================================================================
# Sentinel — Makefile
# =============================================================================
# Common development and deployment commands.
#
# Usage:
#   make              # show all available targets
#   make install      # install dependencies
#   make test         # run tests with coverage
# =============================================================================

.PHONY: help install test run dashboard docker-build docker-up docker-down clean lint format all

# Default target
.DEFAULT_GOAL := help

# Python / venv detection
PYTHON       := python3
VENV_DIR     := .venv
VENV_PYTHON  := $(VENV_DIR)/bin/python
VENV_PIP     := $(VENV_DIR)/bin/pip

# Detect OS for Windows compatibility
ifeq ($(OS),Windows_NT)
    PYTHON     := python
    VENV_PYTHON := $(VENV_DIR)/Scripts/python.exe
    VENV_PIP    := $(VENV_DIR)/Scripts/pip.exe
endif

# =============================================================================
# Help
# =============================================================================
help: ## Show this help message
	@echo "Sentinel — Available commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# =============================================================================
# Installation
# =============================================================================
install: ## Install all dependencies (production + dev)
	@echo "Installing dependencies..."
	$(PYTHON) -m pip install --upgrade pip setuptools wheel
	$(PYTHON) -m pip install -e ".[dev]"
	@echo "Done."

# =============================================================================
# Testing
# =============================================================================
test: ## Run tests with coverage report
	@echo "Running tests..."
	$(PYTHON) -m pytest -v --cov=sentinel --cov-report=term-missing --cov-report=html:htmlcov
	@echo "Coverage report: htmlcov/index.html"

# =============================================================================
# Running
# =============================================================================
run: ## Start the gateway server
	@echo "Starting Sentinel gateway on http://0.0.0.0:8000..."
	uvicorn sentinel.gateway:app --host 0.0.0.0 --port 8000 --reload

dashboard: ## Start the Streamlit dashboard
	@echo "Starting Sentinel dashboard on http://localhost:8501..."
	streamlit run sentinel/dashboard/app.py --server.port 8501 --server.address 0.0.0.0

# =============================================================================
# Docker
# =============================================================================
docker-build: ## Build Docker images
	@echo "Building Docker images..."
	docker compose build
	@echo "Done."

docker-up: ## Start full stack with Docker Compose
	@echo "Starting Sentinel stack..."
	docker compose up -d
	@echo "Gateway:    http://localhost:8000"
	@echo "Dashboard:  http://localhost:8501"
	@echo "Redis:      localhost:6379"

docker-down: ## Stop Docker Compose stack
	@echo "Stopping Sentinel stack..."
	docker compose down
	@echo "Done."

docker-logs: ## Show Docker Compose logs
	docker compose logs -f

# =============================================================================
# Code Quality
# =============================================================================
lint: ## Run linter (ruff)
	@echo "Running ruff..."
	ruff check sentinel/ tests/

format: ## Auto-format code (ruff)
	@echo "Formatting with ruff..."
	ruff format sentinel/ tests/
	ruff check --fix sentinel/ tests/

# =============================================================================
# Cleanup
# =============================================================================
clean: ## Remove cache files, database, traces, and build artifacts
	@echo "Cleaning up..."
	rm -rf __pycache__ .pytest_cache htmlcov .coverage coverage.xml
	rm -rf *.egg-info dist build
	rm -f *.db *.sqlite *.sqlite3
	rm -rf traces/*
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "Done."

clean-all: clean ## Full clean including virtual environment
	@echo "Removing virtual environment..."
	rm -rf $(VENV_DIR)
	@echo "Done."

# =============================================================================
# All-in-one
# =============================================================================
all: install test lint ## Install, test, and lint
	@echo "All checks passed!"
