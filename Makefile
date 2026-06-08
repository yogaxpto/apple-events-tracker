.PHONY: post-create test-tools init sync lint format type-check gitignore freeze dev-tools ensure-ipykernel preview

# Render the site from data/events.json and serve it locally for preview. The page
# re-renders on each reload, so template/CSS edits show on refresh. Output goes to a
# temp dir; your committed docs/ is left untouched. Override the port with
# `PORT=8080 make preview`.
PORT ?= 8000
preview:
	@uv run python -m apple_events_tracker.preview --port $(PORT)

# Post-create command: verify tools/interpreter and sync dependencies
post-create: test-tools init ensure-ipykernel sync

# Verify the native Python interpreter is present (provided by the base image, not uv)
init:
	@echo "Using native Python interpreter:"
	@python --version
	@command -v python

# Ensure ipykernel is installed as a dev dependency (optional)
ensure-ipykernel:
	@if [ "${INSTALL_IPYKERNEL}" = "true" ]; then \
		( \
			if ! uv run python -c "import ipykernel" >/dev/null 2>&1; then \
				echo "Installing ipykernel (dev dependency)..."; \
				uv add --dev ipykernel; \
			else \
				echo "ipykernel already present; skipping installation."; \
			fi \
		) > /tmp/jupyter-kernel.log 2>&1; \
		echo "✓ ipykernel check complete (log: /tmp/jupyter-kernel.log)"; \
	else \
		echo "Skipping ipykernel installation (INSTALL_IPYKERNEL != true)"; \
	fi

# Verify installed tools
test-tools:
	@echo "Running tool verification..."
	@bash .devcontainer/test_tools.sh > /tmp/test-tools.log 2>&1
	@echo "✓ Tool verification complete (log: /tmp/test-tools.log)"

# Sync dependencies with uv
sync:
	@echo "Syncing dependencies..."
	@uv sync > /tmp/uv-sync.log 2>&1
	@echo "✓ Dependency sync complete (log: /tmp/uv-sync.log)"

# Run ruff linter
lint:
	@uv run ruff check .

# Run ruff formatter
format:
	@uv run ruff format .

# Run mypy type checker
type-check:
	@uv run mypy .

# Download Python .gitignore from GitHub
gitignore:
	@if [ -f .gitignore ]; then \
		echo "⚠️  .gitignore already exists, skipping"; \
	else \
		( \
			echo "📥 Downloading Python .gitignore from GitHub..."; \
			curl -fsSL https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore -o .gitignore; \
			echo "✅ .gitignore created"; \
		) > /tmp/gitignore.log 2>&1; \
		echo "✓ .gitignore download complete (log: /tmp/gitignore.log)"; \
	fi

# Install dev tools (ruff, mypy)
dev-tools:
	@echo "Installing dev tools (ruff, mypy)..."
	@uv add --dev ruff mypy > /tmp/dev-tools.log 2>&1
	@echo "✓ Dev tools installed (log: /tmp/dev-tools.log)"

# Freeze dependencies to tmp folder
freeze:
	@echo "Freezing dependencies..."
	@echo "# Generated on $$(date)" > /tmp/requirements.txt
	@uv pip freeze >> /tmp/requirements.txt
	@echo "✓ Dependencies frozen (log: /tmp/requirements.txt)"
