.PHONY: demo test coverage
demo:
	python scripts/demo.py
test:
	python -m unittest discover -s tests -v
coverage:
	coverage run --source=minidrop -m unittest discover -s tests
	coverage report --fail-under=50
