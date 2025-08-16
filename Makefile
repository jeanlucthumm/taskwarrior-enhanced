.PHONY: default format

default:
	@echo "This package is built with nix flakes and has a devenv environment for development"
	@exit 1

format:
	ruff format .