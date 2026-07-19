.PHONY: help venv install lint lintfix test test-cov clean docker-build docker-run

PYTHON := python3
VENV   := .venv

# Use venv tools when the venv exists (local dev); fall back to PATH otherwise (CI).
ifneq ($(wildcard $(VENV)/bin/ruff),)
BIN := $(VENV)/bin/
else
BIN :=
endif

help:
	@echo "Targets: venv install lint lintfix test test-cov clean docker-build docker-run"

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip

install: venv
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/playwright install chromium

lint:
	$(BIN)ruff check src tests
	$(BIN)ruff format --check src tests
	$(BIN)mypy src
	$(BIN)pylint src

lintfix:
	$(BIN)ruff check --fix src tests
	$(BIN)ruff format src tests

test:
	$(BIN)pytest

test-cov:
	$(BIN)pytest --cov=adult_sub_monitor --cov-report=term-missing --cov-fail-under=80

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +

docker-build:
	docker build -t adult-sub-monitor:dev .

docker-run:
	docker compose up --build
