PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python))

.PHONY: proto contracts diagnosis-worker analyzer-worker test coverage lint fmt demo-target native-agent gperftools-bridge diagnosis-benchmark-v2 profile-aggregation-benchmark deploy deploy-down db-upgrade db-current db-downgrade accept-ebpf accept-backup accept-replicas

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

gperftools-bridge:
	cmake -S native/gperftools_bridge -B build/gperftools-bridge -DCMAKE_BUILD_TYPE=Release
	cmake --build build/gperftools-bridge --parallel

diagnosis-benchmark-v2:
	$(PYTHON) scripts/generate_diagnosis_benchmark_v2.py
	$(PYTHON) scripts/run_diagnosis_benchmark_v2.py

profile-aggregation-benchmark:
	$(PYTHON) scripts/benchmark_profile_aggregation.py

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
