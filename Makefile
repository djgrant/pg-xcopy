.PHONY: install lint format build publish

install:
	pip install -e .[dev]

lint:
	ruff check .

format:
	ruff format .

build:
	rm -rf dist/
	python -m build

publish: lint build
	python -m twine upload dist/*
