.PHONY: install install-locked install-dev-locked lock run test backup clean build-secp256k1

PYTHON ?= python3
PIP ?= pip3
PORT ?= 5000

install:
	$(PIP) install -e ".[dev]"

install-locked:
	$(PIP) install --require-hashes -r requirements.lock
	$(PIP) install -e . --no-deps

install-dev-locked:
	$(PIP) install --require-hashes -r requirements-dev.lock
	$(PIP) install -e ".[dev]" --no-deps

lock:
	pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml
	pip-compile --generate-hashes --extra=dev --output-file=requirements-dev.lock pyproject.toml

run:
	$(PYTHON) app.py

run-prod:
	gunicorn -c deploy/gunicorn.conf.py wsgi:app

test:
	$(PYTHON) -m pytest tests/ -v

test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=btpay --cov-report=term-missing

backup:
	$(PYTHON) -c "from btpay.orm.persistence import backup_rotation; backup_rotation('data')"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf build dist *.egg-info .pytest_cache

build-secp256k1:
	@echo "Building libsecp256k1 from source..."
	mkdir -p lib
	cd /tmp && \
		git clone https://github.com/bitcoin-core/secp256k1.git secp256k1-build && \
		cd secp256k1-build && \
		./autogen.sh && \
		./configure --enable-module-recovery && \
		make && \
		cp .libs/libsecp256k1.* $(CURDIR)/lib/
	@echo "Done. Library copied to lib/"
