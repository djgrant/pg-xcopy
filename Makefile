.PHONY: install lint format publish

install:
	pip install -e .[dev]

lint:
	ruff check .

format:
	ruff format .

publish:
	python -m build
	python -m twine upload dist/*
