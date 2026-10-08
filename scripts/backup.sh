#!/usr/bin/env bash
# Nightly backup (shop-hub-backup.timer): the database and the file store, together.
# A database whose signatures and frozen PDFs are gone isn't a complete record.
#
#   backup.sh            uses DATABASE_URL (the app role; it can read every table)
#
# Each run writes <SHOP_BACKUP_DIR>/daily/<UTC stamp>/:
#   shop.dump   pg_dump -Fc        files.tar   the file store
#   toc.txt     pg_restore --list  counts.tsv  rows per table, read back from the dump
#   SHA256SUMS
# The first run of a month / year is also hard-linked into monthly/ and yearly/.
# Kept: 14 daily, 12 monthly, 7 yearly. If SHOP_BACKUP_HOOK (default
# /etc/shop-hub/backup-offsite) is executable, it runs with the new directory as its
# argument: that's where the encrypted off-site copy (restic, …) goes.
#
# pg_* tools come from PATH or SHOP_PG_BIN. Files go through stdin/stdout only.
set -euo pipefail
umask 077

: "${DATABASE_URL:?DATABASE_URL is not set}"
BACKUP_DIR=${SHOP_BACKUP_DIR:-/var/lib/shop-hub/backups}
FILES_DIR=${SHOP_FILES_DIR:-/var/lib/shop-hub/files}
HOOK=${SHOP_BACKUP_HOOK:-/etc/shop-hub/backup-offsite}
KEEP_DAILY=${SHOP_KEEP_DAILY:-14}
KEEP_MONTHLY=${SHOP_KEEP_MONTHLY:-12}
KEEP_YEARLY=${SHOP_KEEP_YEARLY:-7}
PG=${SHOP_PG_BIN:+$SHOP_PG_BIN/}
PGURL=${DATABASE_URL/postgresql+psycopg:/postgresql:}
STAMP_RE='^[0-9]{8}T[0-9]{6}Z$'

# Rows per table, counted from the dump's own COPY blocks (one line per row in COPY
# text format), so the numbers are exactly what the dump holds.
dump_counts() {
  "${PG}pg_restore" --data-only -f - | awk '
    /^COPY / { table = $2; n = 0; copying = 1; next }
    copying && /^\\\.$/ { print table "\t" n; copying = 0; next }
    copying { n++ }'
}

prune() {  # prune DIR KEEP: delete all but the newest KEEP runs
  local dir=$1 keep=$2
  find "$dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -E "$STAMP_RE" | sort \
    | head -n "-$keep" | while read -r old; do rm -rf -- "${dir:?}/$old"; done
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly" "$BACKUP_DIR/yearly"
work="$BACKUP_DIR/daily/.$stamp.partial"
trap 'rm -rf -- "$work"' EXIT
mkdir "$work"

"${PG}pg_dump" -Fc --no-password "$PGURL" > "$work/shop.dump"
"${PG}pg_restore" --list < "$work/shop.dump" > "$work/toc.txt"
dump_counts < "$work/shop.dump" > "$work/counts.tsv"
[ -s "$work/counts.tsv" ] || { echo "backup: no tables in the dump" >&2; exit 1; }
if [ -d "$FILES_DIR" ]; then
  tar -C "$FILES_DIR" -cf "$work/files.tar" .
else
  tar -cf "$work/files.tar" -T /dev/null
fi
(cd "$work" && sha256sum shop.dump files.tar toc.txt counts.tsv > SHA256SUMS)

final="$BACKUP_DIR/daily/$stamp"
mv -- "$work" "$final"
trap - EXIT

if ! find "$BACKUP_DIR/monthly" -mindepth 1 -maxdepth 1 -name "${stamp:0:6}*" | grep -q .; then
  cp -al -- "$final" "$BACKUP_DIR/monthly/$stamp"
fi
if ! find "$BACKUP_DIR/yearly" -mindepth 1 -maxdepth 1 -name "${stamp:0:4}*" | grep -q .; then
  cp -al -- "$final" "$BACKUP_DIR/yearly/$stamp"
fi
prune "$BACKUP_DIR/daily" "$KEEP_DAILY"
prune "$BACKUP_DIR/monthly" "$KEEP_MONTHLY"
prune "$BACKUP_DIR/yearly" "$KEEP_YEARLY"

if [ -x "$HOOK" ]; then
  "$HOOK" "$final"
fi

rows=$(awk -F'\t' '{ s += $2 } END { print s + 0 }' "$final/counts.tsv")
echo "backup: $final ($(du -sh "$final" | cut -f1), $(wc -l < "$final/counts.tsv") tables, $rows rows)"
