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

#  Two audiences, two entry points:
#
#    make app     what a colleague runs. Pulls the published dataset from
#                 Google Cloud Storage and opens it. No database, no API keys,
#                 no crawl — a Google account with read access is the whole
#                 requirement.
#
#    make serve   what the person collecting the data runs. Exports from the
#                 local database instead of pulling.

# The small shipped segment. Override per invocation: `make serve SEGMENT=x`.
SEGMENT ?= pilates-zurich

.DEFAULT_GOAL := help
.PHONY: help setup lint fmt typecheck test check verify run app serve seo audit deepen publish clean \
	bucket bucket-status bucket-grant bucket-revoke bucket-destroy \
        data web web-dev web-serve web-install verify-data verify-data-live

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
	uv run pytest -m "not network and not llm" --cov --cov-report=term-missing
	uv run pre-commit run --all-files
	gitleaks detect --no-git --redact
	@test -z "$$(git ls-files data/)" \
		|| (echo "FAIL: files under data/ are tracked by git — see DATA.md"; exit 1)
	@# The front end is typed too, and a type error there is invisible to
	@# pytest and to mypy. It found a real one: a nullable dataset reached for
	@# inside a fragment, reported as a syntax error at the closing tag.
	@# Skipped rather than failed when the web deps are not installed, so
	@# `check` still passes from a bare clone without Node.
	@if [ -d web/node_modules ]; then \
		echo "astro check"; cd web && pnpm exec astro check || exit 1; \
	else \
		echo "astro check: skipped, run 'make web-install' to enable"; \
	fi
	@test -z "$$(git log --format='%B' | grep -i 'co-authored-by' || true)" \
		|| (echo "FAIL: an AI Co-Authored-By trailer is present in the history"; exit 1)
	@echo "CHECK_PASS"

verify: ## The completion condition — check plus every phase gate
	@./scripts/verify.sh

verify-data: ## Check the collected data against the promises made about it
	@uv run python scripts/verify_data.py

verify-data-live: ## As above, plus re-fetch a sample and confirm the quotes
	@uv run python scripts/verify_data.py --live --sample 15

run: ## Full pipeline over the first segment
	uv run sectorradar run --segment $(SEGMENT)

deepen: ## Keep discovering until the market stops giving (costs money, capped)
	uv run sectorradar deepen --segment $(SEGMENT)

audit: ## Report what the dataset is probably missing (free, offline)
	uv run sectorradar audit --segment $(SEGMENT)

seo: ## Recompute search visibility from stored markup (free, no network)
	uv run sectorradar seo --segment $(SEGMENT)

app: ## Pull the published dataset and open it — what a colleague runs
	@# `--segment` is passed only when you actually named one. SEGMENT has a
	@# default, and forcing it here meant a colleague whose bucket holds one
	@# market was told there was "no published data" for a different one they
	@# had never heard of. With no segment, `pull` lists what the bucket holds
	@# and takes it if there is only one.
	@echo "Pulling the published dataset..."
	uv run sectorradar pull $(if $(filter command line,$(origin SEGMENT)),--segment $(SEGMENT),)
	@cd web && pnpm build >/dev/null
	@echo
	@uv run python scripts/serve.py

bucket: ## Create the GCS bucket, correctly configured, from .env
	@./scripts/bucket.sh create

bucket-status: ## Show the bucket, who can read it, and what is published
	@./scripts/bucket.sh status

bucket-grant: ## Give somebody read access: make bucket-grant EMAIL=a@b.ch
	@test -n "$(EMAIL)" || { echo "usage: make bucket-grant EMAIL=someone@example.com"; exit 2; }
	@./scripts/bucket.sh grant "$(EMAIL)"

bucket-revoke: ## Take read access away: make bucket-revoke EMAIL=a@b.ch
	@test -n "$(EMAIL)" || { echo "usage: make bucket-revoke EMAIL=someone@example.com"; exit 2; }
	@./scripts/bucket.sh revoke "$(EMAIL)"

bucket-destroy: ## Delete the bucket and everything in it (asks first)
	@./scripts/bucket.sh destroy

publish: web ## Show what would be published to GCS (add EXECUTE=1 to do it)
	uv run sectorradar publish --segment $(SEGMENT) $(if $(EXECUTE),--execute,)

# --- the standalone web build ------------------------------------------------
#
# Building the page needs Node and pnpm. Opening the result needs nothing at
# all — web/dist/ is a folder of static files that works from the filesystem,
# which is what makes it something you can hand to somebody.

data: ## Export the database for the web build
	uv run sectorradar export --segment $(SEGMENT) --format web
	@mkdir -p web/src/data
	@cp data/exports/$(SEGMENT).web.json web/src/data/
	@echo "exported for the web build"

web-install: ## Install the web build's dependencies (once)
	cd web && pnpm install

web-dev: data ## Live-reloading web build at http://localhost:4321
	cd web && SECTORRADAR_SEGMENT=$(SEGMENT) pnpm dev

web: data ## Build the standalone site into web/dist/
	@# SEGMENT has to reach the build, not just the export. Without it the page
	@# globs every exported document and takes the first alphabetically, so
	@# `make serve SEGMENT=b` exported b and then rendered a.
	cd web && SECTORRADAR_SEGMENT=$(SEGMENT) pnpm build
	@echo
	@echo "Built web/dist/ - open web/dist/index.html, or run 'make web-serve'."

serve: ## Export from the local database, build and open at localhost:8080
	@$(MAKE) --no-print-directory web-serve SEGMENT=$(SEGMENT)

web-serve: web ## Same as `serve`, kept for older muscle memory
	@uv run python scripts/serve.py

clean: ## Remove caches and build artefacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
