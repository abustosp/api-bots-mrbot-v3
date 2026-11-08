SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: help install test verify check compose-config clean-pyc

help:
	@printf '%s\n' \
		'install         prepara el entorno virtual e instala los tres paquetes' \
		'test            ejecuta la suite de pruebas' \
		'verify          ejecuta las verificaciones de documentación, aislamiento y Compose' \
		'check           ejecuta test y verify' \
		'compose-config  valida la configuración Compose de desarrollo' \
		'clean-pyc       elimina cachés Python generadas localmente'

install:
	$(PYTHON) -m venv --upgrade-deps $(VENV)
	$(VENV_PYTHON) -m pip install -e packages/mrbot-contracts -e services/central-api -e services/bot-worker
	$(VENV_PYTHON) -m pip install pytest

# Un pytest por paquete: dos test_worker_capacity.py comparten basename y colisionan en una sola colección.
TEST_DIRS := packages/mrbot-contracts services/central-api services/bot-worker services/worker-updater tests

test:
	@rc=0; for d in $(TEST_DIRS); do echo "== $$d"; $(VENV_PYTHON) -m pytest -q $$d || rc=1; done; exit $$rc

verify:
	./infra/verify-all.sh

check: test verify

compose-config:
	docker compose --env-file /dev/null -f infra/compose/docker-compose.yml config --no-env-resolution --quiet
	@echo 'Compose de desarrollo válido'

clean-pyc:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +

