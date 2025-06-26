.PHONY: install format lint test build publish

install:
	pip install -e .[dev]

format:
	ruff format .

lint:
	ruff check .

test:
	python3 -m unittest discover -s tests -t .

build:
	rm -rf dist/
	python -m build

publish: install lint test build
	python -m twine upload dist/*
