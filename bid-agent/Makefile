.PHONY: help install test test-unit test-integration lint clean run

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies
	pip install -r requirements.txt
	pip install -e .

test:  ## Run all tests
	pytest tests/ -v

test-unit:  ## Run unit tests only
	pytest tests/unit/ -v

test-integration:  ## Run integration tests only
	pytest tests/integration/ -v

test-contract:  ## Run contract tests only
	pytest tests/contract/ -v

lint:  ## Run linter
	ruff check core/ gui/ config/ adapters/

clean:  ## Clean generated files
	rm -rf data/uploads/* data/exports/* data/faiss_index/*.faiss data/faiss_index/*.pkl
	rm -rf __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

run:  ## Run Streamlit app
	streamlit run gui/app.py
