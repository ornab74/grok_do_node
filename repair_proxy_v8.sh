#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo 'run as root: sudo ./repair_proxy_v8.sh' >&2; exit 1; }

SERVICE_USER=grokbrowser
SERVICE_HOME=/home/grokbrowser
APP_DIR=/srv/grok-secure-node
[[ -d "$APP_DIR" ]] || { echo "missing installed app: $APP_DIR" >&2; exit 1; }
id "$SERVICE_USER" >/dev/null 2>&1 || { echo "missing service user: $SERVICE_USER" >&2; exit 1; }
UID_N="$(id -u "$SERVICE_USER")"
RUNTIME_DIR="/run/user/$UID_N"
DOCKER_HOST="unix://$RUNTIME_DIR/docker.sock"

run_service(){
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME_DIR/bus" \
    DOCKER_HOST="$DOCKER_HOST" \
    PATH="/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin" "$@"
}

write_proxy_files(){
  local root="$1"
  mkdir -p "$root/docker"
  cat > "$root/docker/squid-grok.conf" <<'SQUID'
visible_hostname grok-vault-egress
http_port 3128
pid_filename /run/squid/squid.pid
coredump_dir /tmp

acl CONNECT method CONNECT
acl SSL_ports port 443
acl Safe_ports port 443

# Email-based xAI/Grok account creation/login and Grok itself.
# OAuth providers are intentionally absent.
acl approved_domains dstdomain grok.com .grok.com x.ai .x.ai accounts.x.ai challenges.cloudflare.com

# Block loopback, RFC1918, link-local/cloud metadata, documentation ranges,
# multicast and IPv6 local ranges even if DNS is malicious or misconfigured.
acl forbidden_destination dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10

http_access deny forbidden_destination
http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access allow approved_domains
http_access deny all

cache deny all
# No cache_dir on Squid 3.1+: the legacy "null" store is optional and is not
# built by default by Debian's Squid package.
access_log stdio:/dev/stdout
cache_log /dev/stderr
forwarded_for delete
via off
request_header_access Proxy-Authorization deny all
reply_header_access Server deny all
shutdown_lifetime 1 seconds
SQUID

  cat > "$root/docker/Dockerfile.proxy" <<'DOCKER'
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates squid \
 && rm -rf /var/lib/apt/lists/*
COPY docker/squid-grok.conf /etc/squid/squid.conf
RUN /usr/sbin/squid -k parse -f /etc/squid/squid.conf
USER 13:13
ENTRYPOINT ["/usr/sbin/squid", "-N", "-C", "-d", "1", "-f", "/etc/squid/squid.conf"]
DOCKER
}

cat > "$APP_DIR/run-grok.sh" <<'RUN'
#!/usr/bin/env bash
set -Eeuo pipefail
ulimit -c 0 || true
cd /srv/grok-secure-node
proxy_logs(){ docker compose -f compose.yaml logs --no-color --tail=120 egress-proxy >&2 || true; }
if ! docker compose -f compose.yaml up -d --wait --wait-timeout 30 egress-proxy; then
  printf '\n--- egress-proxy logs ---\n' >&2
  proxy_logs
  exit 1
fi
docker compose -f compose.yaml run --rm vault-init >/dev/null
exec docker compose -f compose.yaml run --rm grok-tui
RUN
chmod 0755 "$APP_DIR/run-grok.sh"
write_proxy_files "$APP_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/docker" "$APP_DIR/run-grok.sh"

# Also repair the checkout used to invoke this script when it looks like the
# source bundle. This keeps future reinstalls from restoring the bad config.
SRC_DIR="$(pwd -P)"
if [[ -f "$SRC_DIR/install.sh" && -f "$SRC_DIR/SHA256SUMS" && -d "$SRC_DIR/docker" ]]; then
  write_proxy_files "$SRC_DIR"
  if [[ -f "$SRC_DIR/run-grok.sh" ]]; then
    cp -f "$APP_DIR/run-grok.sh" "$SRC_DIR/run-grok.sh"
    chmod 0755 "$SRC_DIR/run-grok.sh"
  fi
  for rel in docker/Dockerfile.proxy docker/squid-grok.conf run-grok.sh; do
    [[ -f "$SRC_DIR/$rel" ]] || continue
    h="$(sha256sum "$SRC_DIR/$rel" | awk '{print $1}')"
    # Preserve every unrelated manifest line verbatim and emit the target in
    # canonical GNU sha256sum format: 64 hex chars + two spaces + path.
    # The V8 updater used `$1=h; print`, which collapsed that separator and
    # created three "improperly formatted" entries.
    awk -v rel="./$rel" -v h="$h" '{
      if ($2 == rel) { print h "  " rel; next }
      print
    }' "$SRC_DIR/SHA256SUMS" > "$SRC_DIR/SHA256SUMS.tmp"
    mv "$SRC_DIR/SHA256SUMS.tmp" "$SRC_DIR/SHA256SUMS"
  done
  (cd "$SRC_DIR" && sha256sum --check --strict SHA256SUMS >/dev/null)
  echo 'source checkout manifest updated and verified'
fi

cat > /usr/local/bin/grok-proxy-logs <<EOF2
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-proxy-logs' >&2; exit 1; }
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml logs --no-color --tail=200 egress-proxy"
EOF2
chmod 0755 /usr/local/bin/grok-proxy-logs

run_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml down --remove-orphans >/dev/null 2>&1 || true"
echo '[V8.1] rebuilding proxy with build-time Squid config parse...'
run_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml build --no-cache egress-proxy"
echo '[V8.1] parsing Squid policy under runtime container constraints...'
run_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm --no-deps --entrypoint /usr/sbin/squid egress-proxy -k parse -f /etc/squid/squid.conf"
echo '[V8.1] starting proxy and waiting for health...'
if ! run_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml up -d --wait --wait-timeout 30 egress-proxy"; then
  echo '--- egress-proxy logs ---' >&2
  run_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml logs --no-color --tail=200 egress-proxy" >&2 || true
  exit 1
fi

echo
echo 'V8.1 PROXY REPAIR PASSED'
echo 'Next: sudo grok-tui'
echo 'Logs: sudo grok-proxy-logs'
