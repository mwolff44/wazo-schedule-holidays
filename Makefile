.PHONY: vendor test lint

vendor:
	scripts/vendor.sh

test:
	pytest tests/

lint:
	pre-commit run --all-files
