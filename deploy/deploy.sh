#!/usr/bin/env bash
# Deploy the latest main on the shop LXC. Run as root.
#
#   deploy.sh --check    read-only preflight; changes nothing
#   deploy.sh            preflight, then:
#                          pg_dump (as shop_owner) → git pull --ff-only →
#                          pip install --require-hashes → flask db upgrade →
#                          systemctl restart → /healthz
#                        On failure after the dump, prints the exact rollback commands.
set -euo pipefail

APP=${SHOP_APP_DIR:-/opt/shop-hub}
VENV=${SHOP_VENV:-/opt/shop-hub-venv}
ETC=${SHOP_ETC:-/etc/shop-hub}
ENV_FILE=$ETC/shop-hub.env
MIGRATE_ENV=$ETC/migrate.env
LISTEN=$ETC/listen.conf
PREDEPLOY=${SHOP_PREDEPLOY_DIR:-/var/lib/shop-hub/backups/predeploy}
DATA=/var/lib/shop-hub
SERVICE=shop-hub
BRANCH=main

CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  "") ;;
  *) echo "usage: deploy.sh [--check]" >&2; exit 2 ;;
esac

failures=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }
note() { printf '  ..    %s\n' "$*"; }

# KEY=value lines, no quoting, no eval (the same rules systemd's EnvironmentFile uses
# for plain values; RUNBOOK.md says to keep values unquoted).
load_env() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    export "${line%%=*}=${line#*=}"
  done < "$1"
}

libpq_url() { printf '%s' "${1/postgresql+psycopg:/postgresql:}"; }

flask_cmd() {
  (cd "$APP" && load_env "$ENV_FILE" && load_env "$MIGRATE_ENV" \
     && "$VENV/bin/flask" --app wsgi "$@")
}

perm_is() {  # perm_is FILE OWNER:GROUP MODE
  [ "$(stat -c '%U:%G %a' "$1" 2>/dev/null)" = "$2 $3" ]
}

preflight() {
  echo "preflight"
  [ "$(id -u)" -eq 0 ] && ok "running as root" || bad "run as root"

  local pyver
  pyver=$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
  [ "$pyver" = "3.13" ] && ok "venv python $pyver" || bad "venv python is '${pyver:-missing}', want 3.13"

  perm_is "$ENV_FILE" root:shop-hub 640 && ok "$ENV_FILE root:shop-hub 640" \
    || bad "$ENV_FILE must exist, root:shop-hub, mode 640"
  perm_is "$MIGRATE_ENV" root:root 600 && ok "$MIGRATE_ENV root:root 600" \
    || bad "$MIGRATE_ENV must exist, root:root, mode 600"
  [ -r "$LISTEN" ] && ok "$LISTEN present" || bad "$LISTEN missing (see listen.conf.example)"

  if [ -n "$(git -C "$APP" status --porcelain 2>/dev/null)" ]; then
    bad "$APP has local changes (git status)"
  else
    ok "$APP clean"
  fi
  local branch
  branch=$(git -C "$APP" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  [ "$branch" = "$BRANCH" ] && ok "on $BRANCH" || bad "on '$branch', want $BRANCH"
  if git -C "$APP" fetch -q origin "$BRANCH" 2>/dev/null; then
    local behind
    behind=$(git -C "$APP" rev-list --count "HEAD..origin/$BRANCH")
    if git -C "$APP" merge-base --is-ancestor HEAD "origin/$BRANCH"; then
      ok "fast-forward possible ($behind new commit(s))"
    else
      bad "HEAD isn't an ancestor of origin/$BRANCH: no fast-forward"
    fi
  else
    bad "git fetch origin $BRANCH failed"
  fi

  (load_env "$ENV_FILE" && psql -X -q -At --no-password "$(libpq_url "$DATABASE_URL")" -c 'SELECT 1' >/dev/null 2>&1) \
    && ok "database reachable as the app role" || bad "can't reach the database with DATABASE_URL"
  (load_env "$MIGRATE_ENV" && psql -X -q -At --no-password "$(libpq_url "$MIGRATE_DATABASE_URL")" -c 'SELECT 1' >/dev/null 2>&1) \
    && ok "database reachable as the owner role" || bad "can't reach the database with MIGRATE_DATABASE_URL"

  local client server
  client=$(pg_dump --version 2>/dev/null | grep -oE '[0-9]+' | head -n 1 || true)
  server=$(load_env "$MIGRATE_ENV" && psql -X -At --no-password "$(libpq_url "$MIGRATE_DATABASE_URL")" \
           -c 'SHOW server_version_num' 2>/dev/null || true)
  server=$(( ${server:-0} / 10000 ))
  if [ -n "$client" ] && [ "$client" -ge "$server" ] && [ "$server" -gt 0 ]; then
    ok "pg_dump $client >= server $server"
  else
    bad "pg_dump ${client:-missing} vs server $server (apt install postgresql-client)"
  fi

  local current heads
  current=$(flask_cmd db current 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -n 1 || true)
  heads=$(flask_cmd db heads 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -n 1 || true)
  if [ -n "$heads" ]; then
    [ "$current" = "$heads" ] && note "migrations: at head $heads (the pull may bring more)" \
      || note "migrations: database at '${current:-none}', code head $heads"
  else
    bad "flask db heads failed (run: deploy.sh --check after fixing the env files)"
  fi

  local avail
  avail=$(df -Pk "$DATA" 2>/dev/null | awk 'NR == 2 { print $4 }' || true)
  [ "${avail:-0}" -ge 1048576 ] && ok "free space in $DATA: $((avail / 1024)) MiB" \
    || bad "less than 1 GiB free in $DATA"

  systemctl is-active -q "$SERVICE" 2>/dev/null && ok "$SERVICE active" || note "$SERVICE not active"
}

health_url() {
  local port
  port=$(grep -oE '127\.0\.0\.1:[0-9]+' "$LISTEN" | head -n 1 | cut -d: -f2)
  printf 'http://127.0.0.1:%s/healthz' "${port:-8080}"
}

preflight
if [ "$failures" -gt 0 ]; then
  echo "preflight: $failures problem(s); nothing changed"
  exit 1
fi
if [ "$CHECK_ONLY" -eq 1 ]; then
  echo "preflight: all clear (--check: nothing changed)"
  exit 0
fi

old_sha=$(git -C "$APP" rev-parse HEAD)
old_rev=$(flask_cmd db current 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -n 1 || true)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dump="$PREDEPLOY/$stamp-${old_sha:0:12}.dump"
step="pre-deploy dump"

rollback() {
  cat >&2 <<EOF

deploy: FAILED during: $step
Roll back with:
  git -C $APP reset --hard $old_sha
  $VENV/bin/pip install --require-hashes -r $APP/requirements.txt
EOF
  if [ "$step" = "migrate" ] || [ "$step" = "restart" ] || [ "$step" = "health check" ]; then
    cat >&2 <<EOF
  # the schema may have moved; either step back down:
  (cd $APP && set -a && . $ENV_FILE && . $MIGRATE_ENV && set +a && $VENV/bin/flask --app wsgi db downgrade ${old_rev:-base})
  # or restore the pre-deploy dump:
  (set -a && . $MIGRATE_ENV && set +a && pg_restore --clean --if-exists --no-owner --single-transaction -d "\${MIGRATE_DATABASE_URL/postgresql+psycopg:/postgresql:}" $dump)
EOF
  fi
  cat >&2 <<EOF
  systemctl restart $SERVICE
  journalctl -u $SERVICE -n 50
EOF
}
trap rollback ERR

echo "deploy"
install -d -m 700 "$PREDEPLOY"
(umask 077 && load_env "$MIGRATE_ENV" && pg_dump -Fc --no-password "$(libpq_url "$MIGRATE_DATABASE_URL")" > "$dump")
ok "dump: $dump ($(du -h "$dump" | cut -f1))"

step="git pull"
git -C "$APP" pull -q --ff-only origin "$BRANCH"
ok "code: ${old_sha:0:12} → $(git -C "$APP" rev-parse --short=12 HEAD)"

step="pip install"
"$VENV/bin/pip" install -q --require-hashes -r "$APP/requirements.txt"
ok "dependencies installed (hash-checked)"

step="migrate"
flask_cmd db upgrade
ok "migrations: $(flask_cmd db current 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -n 1)"

step="restart"
systemctl restart "$SERVICE"
ok "$SERVICE restarted"

step="health check"
url=$(health_url)
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
    ok "healthy: $url"
    trap - ERR
    echo "deploy: done"
    exit 0
  fi
  sleep 1
done
false  # → rollback instructions
