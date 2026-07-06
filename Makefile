WAVES_APP_NAME = "Waves"
WAVES_VERSION=`grep -m 1 '__version__' tidaler/waves_ui/__init__.py | tr -d ' "' | cut -d'=' -f2`
app_path_dist = "dist"

.PHONY: install
install: ## Install the poetry environment and install the pre-commit hooks
	@echo "🚀 Creating virtual environment using pyenv and poetry"
	@poetry install --all-extras --with dev,docs
	@poetry run pre-commit install
	@poetry shell

.PHONY: check
check: ## Run code quality tools.
	@echo "🚀 Checking Poetry lock file consistency with 'pyproject.toml': Running poetry lock --check"
	@poetry check --lock
	@echo "🚀 Linting code: Running pre-commit"
	@poetry run pre-commit run -a
#	@echo "🚀 Static type checking: Running pyright"
#	@poetry run pyright
	@echo "🚀 Checking for obsolete dependencies: Running deptry"
	@poetry run deptry .

.PHONY: test
test: ## Test the code with pytest
	@echo "🚀 Testing code: Running pytest"
	@poetry run pytest --doctest-modules

.PHONY: build
build: clean-build ## Build wheel file using poetry
	@echo "🚀 Creating wheel file"
	@poetry build

.PHONY: clean-build
clean-build: ## clean build artifacts
	@rm -rf dist

.PHONY: publish
publish: ## publish a release to pypi.
	@echo "🚀 Publishing: Dry run."
	@poetry config pypi-token.pypi $(PYPI_TOKEN)
	@poetry publish --dry-run
	@echo "🚀 Publishing."
	@poetry publish

.PHONY: build-and-publish
build-and-publish: build publish ## Build and publish.

.PHONY: docs-test
docs-test: ## Test if documentation can be built without warnings or errors
	@poetry run mkdocs build -s

.PHONY: docs
docs: ## Build and serve the documentation
	@poetry run mkdocs serve

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: gui-waves
gui-waves: ## Build the Waves QML app (standalone). On macOS this yields dist/waves.app
	@poetry run python -m nuitka \
		--macos-app-version=$(WAVES_VERSION) \
		--file-version=$(WAVES_VERSION) \
		--product-version=$(WAVES_VERSION) \
		--macos-app-name=$(WAVES_APP_NAME) \
		--output-filename=$(WAVES_APP_NAME) \
		--product-name=$(WAVES_APP_NAME) \
		tidaler/waves.py
	@# Strip the Qt modules the QML UI never loads (chiefly a ~210 MB bundled
	@# Chromium), see tools/trim_qt_bundle.sh, which auto-detects the per-OS
	@# bundle layout (waves.app on macOS, waves.dist on Linux/Windows).
	@if [ -d "$(app_path_dist)/waves.app" ]; then \
		bash tools/trim_qt_bundle.sh "$(app_path_dist)/waves.app"; \
		echo "🔏 Re-sealing macOS bundle (trim broke Nuitka's ad-hoc signature)"; \
		codesign --force --deep --sign - "$(app_path_dist)/waves.app"; \
	elif [ -d "$(app_path_dist)/waves.dist" ]; then \
		bash tools/trim_qt_bundle.sh "$(app_path_dist)/waves.dist"; \
	fi

# Per-OS aliases used by CI (release-or-test-build.yml). The build + trim already
# happens in gui-waves; CI zips the result (macOS = waves.app, Linux/Windows =
# waves.dist). They exist as named entry points so each matrix leg reads clearly.
.PHONY: gui-waves-linux
gui-waves-linux: gui-waves ## Build + trim the Waves app (Linux); artifact is dist/waves.dist

.PHONY: gui-waves-windows
gui-waves-windows: gui-waves ## Build + trim the Waves app (Windows); artifact is dist/waves.dist
