.PHONY: demo test coverage py-compile frontend-install frontend-typecheck frontend-build check docker-up docker-down

demo:
	python scripts/demo.py

py-compile:
	python -m compileall minidrop

test:
	python -m unittest discover -s tests -v

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
