PYTHON ?= python3

.PHONY: demo demo-local test coverage py-compile frontend-install frontend-typecheck frontend-build check docker-up docker-down

demo:
	docker compose exec -T server python scripts/demo.py

demo-local:
	$(PYTHON) scripts/demo.py

py-compile:
	$(PYTHON) -m compileall minidrop

test:
	$(PYTHON) -m unittest discover -s tests -v

coverage:
	coverage run --source=minidrop -m unittest discover -s tests
	coverage report --fail-under=50

frontend-install:
	cd frontend && npm install

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-build:
	cd frontend && npm run build

check: py-compile test coverage frontend-typecheck frontend-build

docker-up:
	docker compose up --build

docker-down:
	docker compose down
