#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/srv/grok-patched-node}"
cd "$PROJECT_DIR"

cleanup() {
  docker compose -f compose.grok.yaml down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

docker compose -f compose.grok.yaml up -d --build egress-proxy
docker compose -f compose.grok.yaml run --rm grok-tui
