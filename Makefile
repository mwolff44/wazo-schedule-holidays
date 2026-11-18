.PHONY: vendor test lint contract

vendor:
	scripts/vendor.sh

test:
	pytest tests/

lint:
	pre-commit run --all-files

contract:
	scripts/contract-tests.sh
