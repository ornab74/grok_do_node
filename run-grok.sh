#!/usr/bin/env bash
set -Eeuo pipefail
ulimit -c 0 || true
cd /srv/grok-secure-node

proxy_logs(){
  docker compose -f compose.yaml logs --no-color --tail=120 egress-proxy >&2 || true
}

# Compose's generic "dependency failed" message hides Squid's actual fatal line.
# Wait for the proxy here and always surface its logs if startup fails.
if ! docker compose -f compose.yaml up -d --wait --wait-timeout 30 egress-proxy; then
  printf '\n--- egress-proxy logs ---\n' >&2
  proxy_logs
  exit 1
fi

docker compose -f compose.yaml run --rm vault-init >/dev/null
exec docker compose -f compose.yaml run --rm grok-tui
