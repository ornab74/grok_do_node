#!/bin/sh
set -eu

docker compose run --rm assignment8-results
docker compose run --rm capstone-results
