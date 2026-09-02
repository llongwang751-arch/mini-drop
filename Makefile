PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python))

.PHONY: proto contracts diagnosis-worker analyzer-worker test coverage lint fmt demo-target native-agent skill-benchmark skill-ab-large skill-stability rcaeval-skill-ab quantitative-report deploy deploy-down db-upgrade db-current db-downgrade accept-ebpf accept-backup accept-replicas

proto:
	$(PYTHON) proto/compile.py

contracts:
	$(PYTHON) scripts/generate_taskkind_contracts.py
	$(PYTHON) scripts/generate_status_contracts.py
	$(PYTHON) scripts/generate_error_code_contracts.py

diagnosis-worker:
	$(PYTHON) -m server.app.diagnosis_worker

analyzer-worker:
	$(PYTHON) -m server.app.analysis_jobs

test:
	$(PYTHON) -m pytest tests -v

coverage:
	$(PYTHON) -m pytest --cov=server --cov=analyzer --cov-report=term-missing tests

lint:
	$(PYTHON) -m compileall -q server analyzer scripts
	@echo "[lint] compileall passed"
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff check server analyzer scripts || echo "[lint] ruff not installed, skipping"

fmt:
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff format server analyzer scripts tests || echo "[fmt] ruff not installed, skipping"

demo-target:
	docker compose --profile demo-target up -d --build python-hotspot

native-agent:
	docker compose up -d --build native-agent

skill-benchmark:
	$(PYTHON) scripts/run_skill_evolution_benchmark.py

skill-ab-large:
	$(PYTHON) scripts/run_scaled_skill_ab.py

skill-stability:
	$(PYTHON) scripts/run_scaled_skill_ab.py --stability-seconds 21600 --stability-iterations 5

rcaeval-skill-ab:
	$(PYTHON) scripts/run_rcaeval_skill_ab.py --download

quantitative-report:
	$(PYTHON) scripts/build_quantitative_test_report.py

db-upgrade:
	$(PYTHON) -m alembic upgrade head

db-current:
	$(PYTHON) -m alembic current

db-downgrade:
	$(PYTHON) -m alembic downgrade -1

deploy:
	docker compose up -d --build

deploy-down:
	docker compose down

accept-ebpf:
	bash scripts/verify_external_acceptance.sh ebpf

accept-backup:
	bash scripts/verify_external_acceptance.sh backup

accept-replicas:
	bash scripts/verify_external_acceptance.sh replicas
