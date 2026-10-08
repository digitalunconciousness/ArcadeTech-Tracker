#!/bin/bash
# Setup script for the claude.ai/code cloud environment that works on this repo.
# Paste it into the environment's "Setup script" field (claude.ai/code → environment
# selector → gear). It runs as root on Ubuntu 24.04 before Claude starts, and its result
# is cached for about a week, so keep it under ~5 minutes.
#
# The environment also needs, set in the same dialog (never committed):
#   SHOP_PATTERNS="<the lines of .githooks/patterns.local>"     (quoted, multi-line)
#   REFS_TOKEN=<read-only token for the reference repos>          (only if they're private)
# This script writes it to ~/.config/shop-hub/patterns.local, which scripts/privacy_check.sh reads.
set -euo pipefail

# Python 3.13 (prod is Debian 13's 3.13; Ubuntu 24.04 ships 3.12). deadsnakes, because
# uv's own Python download comes from a GitHub release the cloud proxy refuses.
apt-get update
apt-get install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.13 python3.13-venv python3.13-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 shellcheck

# PostgreSQL 17 to match prod (the VM's preinstalled server is 16). Pulled now so the
# image is cached; the container itself is started per session by scripts/devdb.sh.
docker pull docker.io/library/postgres:17 || true

# The reference repos, read-only by construction: cloned outside the workspace and not
# attached to the session, so nothing in the session can push to them. If they're
# private, set REFS_TOKEN in the environment (a fine-grained token with read-only
# Contents on just those two repos); it's used for the clone and not kept in .git/config.
install -d /opt/refs
refs_auth=""
[ -n "${REFS_TOKEN:-}" ] && refs_auth="x-access-token:${REFS_TOKEN}@"
for pair in arcade-tracker:arcade-tracker Gatbox:gatbox; do
  repo=${pair%%:*}; dir=/opt/refs/${pair##*:}
  if git clone --depth 1 "https://${refs_auth}github.com/digitalunconciousness/${repo}.git" "$dir"; then
    git -C "$dir" remote set-url origin "https://github.com/digitalunconciousness/${repo}.git"
  else
    echo "cloud-setup: WARNING: could not clone ${repo} (private? set REFS_TOKEN)" >&2
  fi
done
chmod -R a-w /opt/refs || true

# Owner-specific privacy patterns, from the environment variable, never from git.
install -d -m 700 /root/.config/shop-hub
if [ -n "${SHOP_PATTERNS:-}" ]; then
    printf '%s\n' "$SHOP_PATTERNS" > /root/.config/shop-hub/patterns.local
    chmod 600 /root/.config/shop-hub/patterns.local
fi
# scripts/privacy_check.sh falls back to ~/.config/shop-hub/patterns.local on its own.
