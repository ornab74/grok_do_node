.PHONY: install build audit test assignment8 capstone results query live-assignment8 live-owasp live-capstone dashboard down local-check checksums image-verify image-backup image-restore runtime-refresh

install:
	./install.sh

build:
	DOCKER_BUILDKIT=1 docker compose build --pull browser-audit

audit:
	docker compose run --rm browser-audit

test:
	docker compose run --rm test

assignment8:
	docker compose run --rm assignment8-books
	docker compose run --rm assignment8-owasp

capstone:
	docker compose run --rm capstone

results:
	docker compose run --rm assignment8-results
	docker compose run --rm capstone-results

query:
	docker compose run --rm capstone-query

live-assignment8:
	docker compose --profile live run --rm assignment8-books-live

live-owasp:
	docker compose --profile live run --rm assignment8-owasp-live

live-capstone:
	docker compose --profile live run --rm capstone-live

dashboard:
	docker compose --profile dashboard up dashboard

down:
	docker compose --profile live --profile dashboard down

local-check:
	python -m unittest discover -s tests -v

checksums:
	./scripts/generate-sha256.sh

image-verify:
	./scripts/ghcr-image.sh verify

image-backup:
	./scripts/ghcr-image.sh push

image-restore:
	./scripts/ghcr-image.sh restore

runtime-refresh:
	./scripts/refresh-runtime-image.sh
