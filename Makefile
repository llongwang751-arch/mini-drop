PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python))

.PHONY: server agent analyzer test eval coverage lint fmt demo demo-targets native-agent proto deploy deploy-down db-upgrade db-current db-downgrade accept-ebpf accept-backup accept-replicas accept-benchmark

proto:
	cd proto && bash compile.sh

# 导出 OpenAPI 与 TaskKind JSON Schema 版本化契约交付物
contracts:
	$(PYTHON) scripts/export_openapi.py

server:
	$(PYTHON) -m server.app.main

agent:
	$(PYTHON) -m agent.mini_drop_agent.main

analyzer:
	$(PYTHON) -m analyzer.mini_drop_analyzer.hotmethod_analyzer \
		--task-id demo_task \
		--config analyzer/config.example.toml

test:
	$(PYTHON) -m pytest tests -v

eval:
	$(PYTHON) scripts/run_diagnosis_eval.py --output-dir reports/eval
	$(PYTHON) scripts/diagnosis_benchmark.py campaign --output-dir reports/benchmark/campaign

coverage:
	$(PYTHON) -m pytest --cov=server --cov=agent --cov=analyzer --cov-report=term-missing tests

lint:
	$(PYTHON) -m compileall server agent analyzer demo
	@echo "[lint] compileall passed"
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff check server agent analyzer || echo "[lint] ruff not installed (pip install ruff), skipping"
	@which mypy >/dev/null 2>&1 && $(PYTHON) -m mypy server agent analyzer --ignore-missing-imports || echo "[lint] mypy not installed (pip install mypy), skipping"

fmt:
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff format server agent analyzer demo tests || echo "[fmt] ruff not installed, skipping"

demo:
	bash demo/demo.sh

demo-targets:
	docker compose --profile demo-targets up -d --build go-hotspot cpp-hotspot java-hotspot

native-agent:
	docker compose --profile native-agent up -d --build native-agent

db-upgrade:
	$(PYTHON) -m alembic upgrade head

db-current:
	$(PYTHON) -m alembic current

db-downgrade:
	$(PYTHON) -m alembic downgrade -1

deploy:
	docker compose up -d

deploy-down:
	docker compose down

accept-ebpf:
	bash scripts/verify_external_acceptance.sh ebpf

accept-backup:
	bash scripts/verify_external_acceptance.sh backup

accept-replicas:
	bash scripts/verify_external_acceptance.sh replicas

accept-benchmark:
	bash scripts/verify_external_acceptance.sh benchmark
