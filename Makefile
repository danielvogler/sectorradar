#  sectorradar — developer entry points.
#
#  The two targets that carry weight are `check` and `verify`.
#
#    make check   repo-only health. No database, no API keys, no network.
#                 MUST pass from a bare `git clone` + `uv sync --frozen`.
#                 That property is what makes CI meaningful.
#
#    make verify  the completion condition. Runs `check`, then every
#                 data-dependent phase gate against the local data/radar.db.
#                 Lives in scripts/verify.sh because the gate logic needs a
#                 single shell process to count SKIPs, and the macOS system
#                 make (GNU Make 3.81) predates .ONESHELL.
#
#  Rule about `verify`: it is append-only in spirit. Checks get added, never
#  removed or softened to go green. A verify that passes because it stopped
#  checking is worse than an honest failure.

.DEFAULT_GOAL := help
.PHONY: help setup lint fmt typecheck test check verify run app clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install deps and git hooks
	uv sync
	uv run pre-commit install --install-hooks
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill it in.")

lint: ## Lint (no changes written)
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-fix lint and format in place
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy --strict over src/ and tests/
	uv run mypy

test: ## Unit tests, excluding network- and money-spending tests
	uv run pytest -m "not network and not llm" -q

check: ## Repo-only health check — must pass from a bare clone
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -m "not network and not llm" --cov --cov-report=term-missing --cov-fail-under=80
	uv run pre-commit run --all-files
	gitleaks detect --no-git --redact
	@test -z "$$(git ls-files data/)" \
		|| (echo "FAIL: files under data/ are tracked by git — see DATA.md"; exit 1)
	@test -z "$$(git log --format='%B' | grep -i 'co-authored-by' || true)" \
		|| (echo "FAIL: an AI Co-Authored-By trailer is present in the history"; exit 1)
	@echo "CHECK_PASS"

verify: ## The completion condition — check plus every phase gate
	@./scripts/verify.sh

run: ## Full pipeline over the first segment
	uv run sectorradar run --segment agentic-ai-ch

app: ## Serve the Streamlit explorer
	uv run --extra app streamlit run app/Home.py

clean: ## Remove caches and build artefacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
