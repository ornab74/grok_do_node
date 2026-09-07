#!/usr/bin/env bash
set -Eeuo pipefail
ulimit -c 0 || true
cd /srv/grok-secure-node
docker compose -f compose.yaml up -d egress-proxy
docker compose -f compose.yaml run --rm vault-init >/dev/null
exec docker compose -f compose.yaml run --rm grok-tui
