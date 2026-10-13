#!/usr/bin/env bash
# Publish docs/wiki/ to the repository's GitHub wiki, exactly as it is on origin/main.
#
#   scripts/publish_wiki.sh          # run by the owner, from a checkout of this repo ⏸
#
# The wiki is a git repository of its own (<repo>.wiki.git). Nothing pushed there passes
# the pre-push privacy check, so its pages only ever come from origin/main, which has:
# edit docs/wiki/ through a pull request, merge it, then run this. Pages made or edited
# on GitHub are replaced, and pages no longer in docs/wiki/ are removed.
#
# First time only: open the repository's Wiki tab on GitHub and save any page, so the
# wiki repository exists. SHOP_WIKI_URL overrides where it is (the tests use it).
set -euo pipefail

die() { echo "publish_wiki: $*" >&2; exit 1; }

ROOT=$(git rev-parse --show-toplevel) || exit 2
cd "$ROOT"
git fetch -q origin main || die "can't fetch origin main"
git cat-file -e origin/main:docs/wiki/Home.md 2>/dev/null \
  || die "origin/main has no docs/wiki/Home.md: merge the wiki pages first"

origin=$(git remote get-url origin)
wiki=${SHOP_WIKI_URL:-${origin%.git}.wiki.git}
sha=$(git rev-parse --short origin/main)
name=$(git config user.name) || die "set git user.name in this repo first"
email=$(git config user.email) || die "set git user.email in this repo first"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git clone -q "$wiki" "$tmp/wiki" \
  || die "can't clone $wiki (first time? save any page on the Wiki tab to create it)"
git archive origin/main docs/wiki | tar -x -C "$tmp"

find "$tmp/wiki" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -R "$tmp/docs/wiki/." "$tmp/wiki/"

cd "$tmp/wiki"
git add -A
if git diff --cached --quiet; then
  echo "publish_wiki: the wiki already matches docs/wiki/ at $sha"
  exit 0
fi
git diff --cached --stat
TZ=UTC git -c user.name="$name" -c user.email="$email" commit -q \
  -m "Publish docs/wiki/ from main at $sha"
git push -q origin HEAD
echo "publish_wiki: published docs/wiki/ from main at $sha"
