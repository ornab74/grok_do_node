#!/bin/sh
set -eu

docker compose build --pull browser-audit
docker compose run --rm browser-audit
docker compose run --rm test
docker compose run --rm assignment8-results
docker compose run --rm capstone-results
