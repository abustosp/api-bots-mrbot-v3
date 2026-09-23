SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: help install test verify check compose-config clean-pyc

help:
	@printf '%s\n' \
		'install         prepara el entorno virtual e instala los tres paquetes' \
		'test            ejecuta la suite de pruebas' \
		'verify          ejecuta las verificaciones de documentación, DDL y Compose' \
		'check           ejecuta test y verify' \
		'compose-config  valida la configuración Compose de desarrollo' \
		'clean-pyc       elimina cachés Python generadas localmente'

install:
	$(PYTHON) -m venv --upgrade-deps $(VENV)
	$(VENV_PYTHON) -m pip install -e packages/mrbot-contracts -e services/central-api -e services/bot-worker
	$(VENV_PYTHON) -m pip install pytest

test:
	$(VENV_PYTHON) -m pytest -q

verify:
	./infra/verify-all.sh

check: test verify

compose-config:
	docker compose -f infra/compose/docker-compose.yml config >/dev/null
	@echo 'Compose de desarrollo válido'

clean-pyc:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +

