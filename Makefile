.PHONY: help venv install lint lint-fix test test-cov clean docker-build docker-run

PYTHON := python3
VENV := .venv
VENV_BIN := $(VENV)/bin

help:
	@echo "Targets: venv install lint lint-fix test test-cov clean docker-build docker-run"

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install --upgrade pip

install: venv
	$(VENV_BIN)/pip install -e ".[dev]"
	$(VENV_BIN)/playwright install chromium

lint:
	$(VENV_BIN)/ruff check src tests
	$(VENV_BIN)/ruff format --check src tests
	$(VENV_BIN)/mypy src
	$(VENV_BIN)/pylint src

lint-fix:
	$(VENV_BIN)/ruff check --fix src tests
	$(VENV_BIN)/ruff format src tests

test:
	$(VENV_BIN)/pytest

test-cov:
	$(VENV_BIN)/pytest --cov=adult_sub_monitor --cov-report=term-missing --cov-fail-under=80

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +

docker-build:
	docker build -t adult-sub-monitor:dev .

docker-run:
	docker compose up --build
