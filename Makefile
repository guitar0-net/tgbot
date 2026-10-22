install-pdm:
	pip install --user pdm

install-dev:
	pdm install -G dev

install:
	pdm install

run:
	python main.py

check:
	ruff check .
	ruff format --check .

fix:
	ruff check --fix .
	ruff format .
