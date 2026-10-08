#!/usr/bin/env bash
# Dev Postgres 17 in a container, on 127.0.0.1:5433 only. Rootless podman on the
# workstation, docker in a cloud session (whichever is installed; podman preferred).
#
#   scripts/devdb.sh start     create on first run, then start; wait until ready
#   scripts/devdb.sh init      create roles shop_owner / shop_app / shop and database shop
#   scripts/devdb.sh env       print the exports for .env and the test suite
#   scripts/devdb.sh status | stop | psql [args] | destroy
#
# Data and generated passwords live under ~/.local/share/shop-hub/ (mode 700), never in
# the checkout. Connections find passwords through PGPASSFILE, so no URL carries one.
set -euo pipefail

NAME=shop-hub-pg
IMAGE=docker.io/library/postgres:17
PORT=5433
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/shop-hub"
PGDATA_DIR="$DATA/pg17"
SECRETS="$DATA/devdb.secrets"     # KEY=value, mode 600
PGPASS="$DATA/pgpass"             # libpq password file, mode 600
ROOT=$(cd "$(dirname "$0")/.." && pwd)

if command -v podman >/dev/null 2>&1; then
  ENGINE=podman; VOLUME_OPTS=":U"
elif command -v docker >/dev/null 2>&1; then
  ENGINE=docker; VOLUME_OPTS=""
else
  echo "devdb: neither podman nor docker is installed" >&2; exit 1
fi

die() { echo "devdb: $*" >&2; exit 1; }

exists()  { "$ENGINE" container inspect "$NAME" >/dev/null 2>&1; }
running() { [ "$("$ENGINE" container inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ]; }

secrets() {
  install -d -m 700 "$DATA"
  if [ ! -s "$SECRETS" ]; then
    umask 077
    {
      echo "POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
      echo "OWNER_PASSWORD=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
      echo "APP_PASSWORD=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    } > "$SECRETS"
  fi
  # shellcheck disable=SC1090
  . "$SECRETS"
  umask 077
  {
    for host in 127.0.0.1 localhost; do
      echo "$host:$PORT:*:postgres:$POSTGRES_PASSWORD"
      echo "$host:$PORT:*:shop_owner:$OWNER_PASSWORD"
      echo "$host:$PORT:*:shop:$APP_PASSWORD"
    done
  } > "$PGPASS"
}

wait_ready() {
  for _ in $(seq 1 60); do
    if "$ENGINE" exec "$NAME" pg_isready -q -U postgres 2>/dev/null; then return 0; fi
    sleep 1
  done
  die "Postgres didn't become ready in 60 s; see: $ENGINE logs $NAME"
}

cmd_start() {
  if [ "$ENGINE" = docker ] && ! docker info >/dev/null 2>&1; then
    die "the docker daemon isn't running (cloud session: start dockerd first)"
  fi
  secrets
  if ! exists; then
    install -d -m 700 "$PGDATA_DIR"
    "$ENGINE" run -d --name "$NAME" \
      -p "127.0.0.1:$PORT:5432" \
      --env-file "$SECRETS" \
      -v "$PGDATA_DIR:/var/lib/postgresql/data$VOLUME_OPTS" \
      "$IMAGE" >/dev/null
  elif ! running; then
    "$ENGINE" start "$NAME" >/dev/null
  fi
  wait_ready
  echo "devdb: $IMAGE up on 127.0.0.1:$PORT ($ENGINE)"
}

cmd_init() {
  running || die "not running; scripts/devdb.sh start"
  secrets
  "$ENGINE" exec -i "$NAME" psql -X -q -U postgres -d postgres \
    -v "owner_password=$OWNER_PASSWORD" -v "app_password=$APP_PASSWORD" \
    < "$ROOT/deploy/sql/create_roles.sql"
  echo "devdb: roles shop_owner, shop_app, shop and databases shop, shop_restore_test ready"
}

cmd_env() {
  secrets
  cat <<EOF
# Put these in .env (git-ignored) or export them in your shell.
PGPASSFILE=$PGPASS
DATABASE_URL=postgresql+psycopg://shop@127.0.0.1:$PORT/shop
MIGRATE_DATABASE_URL=postgresql+psycopg://shop_owner@127.0.0.1:$PORT/shop
SHOP_TEST_ADMIN_URL=postgresql+psycopg://postgres@127.0.0.1:$PORT/postgres
EOF
}

case "${1:-}" in
  start)   cmd_start ;;
  init)    cmd_init ;;
  env)     cmd_env ;;
  status)  if running; then echo "devdb: running"; "$ENGINE" exec "$NAME" pg_isready -U postgres;
           elif exists; then echo "devdb: stopped"; else echo "devdb: not created"; fi ;;
  stop)    exists && "$ENGINE" stop "$NAME" >/dev/null; echo "devdb: stopped" ;;
  psql)    shift; exec "$ENGINE" exec -it "$NAME" psql -X -U postgres "$@" ;;
  destroy) read -r -p "Delete the dev container and $PGDATA_DIR? [y/N] " a
           [ "$a" = y ] || exit 1
           exists && "$ENGINE" rm -f "$NAME" >/dev/null
           "$ENGINE" unshare rm -rf "$PGDATA_DIR" 2>/dev/null || rm -rf "$PGDATA_DIR"
           echo "devdb: destroyed" ;;
  *)       sed -n '2,11p' "$0"; exit 2 ;;
esac
