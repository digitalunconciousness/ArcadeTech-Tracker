#!/usr/bin/env bash
# Monthly restore drill (shop-hub-restore-drill.timer): prove the latest backup restores.
#
#   restore_drill.sh [BACKUP_DIR]     default: the newest run in $SHOP_BACKUP_DIR/daily
#
# RESTORE_DATABASE_URL: the schema owner's URL for the scratch database (prod:
# shop_restore_test, owned by shop_owner). Refused unless the database name contains
# "restore" or "test", so the drill can never clean the live database.
#
# Checks: checksums; the dump restores in one transaction; every table's row count
# equals what the dump holds; every trigger came back; the file store archive reads;
# and (from Phase 4) every issued invoice and credit memo PDF re-hashes to its sha256.
# Prints PASS or FAIL; exits non-zero on FAIL.
set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is not set}"
BACKUP_DIR=${SHOP_BACKUP_DIR:-/var/lib/shop-hub/backups}
PG=${SHOP_PG_BIN:+$SHOP_PG_BIN/}
URL=${RESTORE_DATABASE_URL/postgresql+psycopg:/postgresql:}

fail() { echo "restore drill: FAIL: $*" >&2; exit 1; }
psql_q() { "${PG}psql" -X -q -At -v ON_ERROR_STOP=1 --no-password "$URL" -c "$1"; }

run=${1:-$(find "$BACKUP_DIR/daily" -mindepth 1 -maxdepth 1 -type d -name '2*Z' | sort | tail -n 1)}
[ -n "$run" ] && [ -d "$run" ] || fail "no backup found under $BACKUP_DIR/daily"
cd "$run"
echo "restore drill: $run"

sha256sum --quiet -c SHA256SUMS || fail "checksum mismatch"
echo "  checksums ok"

db=$(psql_q "SELECT current_database()")
case "$db" in *restore*|*test*) ;; *) fail "refusing to restore into '$db'";; esac

"${PG}pg_restore" --clean --if-exists --no-owner --exit-on-error --single-transaction \
  --no-password -d "$URL" < shop.dump || fail "pg_restore failed"
echo "  restored into $db"

query=$(awk -F'\t' -v q="'" '{ printf "%sSELECT %s%s%s, count(*) FROM %s", (NR > 1 ? " UNION ALL " : ""), q, $1, q, $1 }' counts.tsv)
mismatches=$(LC_ALL=C join -t $'\t' <(LC_ALL=C sort counts.tsv) <(psql_q "$query" | tr '|' '\t' | LC_ALL=C sort) \
  | awk -F'\t' '$2 != $3 { print "    " $1 ": dump " $2 ", restored " $3 }')
[ -z "$mismatches" ] || fail "row counts differ:"$'\n'"$mismatches"
tables=$(wc -l < counts.tsv)
[ "$(psql_q "$query" | wc -l)" -eq "$tables" ] || fail "tables missing after restore"
echo "  row counts match ($tables tables)"

want=$(grep -c ' TRIGGER ' toc.txt || true)
got=$(psql_q "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE NOT t.tgisinternal AND n.nspname = 'public'")
[ "$want" -eq "$got" ] || fail "triggers: dump has $want, restored $got"
echo "  triggers ok ($got)"

tar -tf files.tar > /dev/null || fail "files.tar unreadable"
echo "  file store archive ok ($(tar -tf files.tar | grep -vc '/$' || true) files)"

# Every issued invoice PDF, the service report frozen with it, and every credit memo PDF
# must still be the exact file the customer got: in the archive, and hashing to what the
# database says.
hashes=$(psql_q "SELECT pdf_sha256 FROM invoice WHERE pdf_sha256 IS NOT NULL
                 UNION SELECT report_sha256 FROM invoice WHERE report_sha256 IS NOT NULL
                 UNION SELECT pdf_sha256 FROM credit_memo WHERE pdf_sha256 IS NOT NULL")
if [ -z "$hashes" ]; then
  echo "  pdf re-hash: no issued invoices yet"
else
  scratch=$(mktemp -d)
  trap 'rm -rf "$scratch"' EXIT
  tar -C "$scratch" -xf files.tar --wildcards '*.pdf' 2> /dev/null || true
  bad="" n=0
  while read -r sha; do
    file="$scratch/${sha:0:2}/$sha.pdf"
    if [ ! -f "$file" ]; then
      bad+="    $sha.pdf: not in the archive"$'\n'
      continue
    fi
    got=$(sha256sum "$file" | cut -d' ' -f1)
    [ "$got" = "$sha" ] || bad+="    $sha.pdf: hashes to $got"$'\n'
    n=$((n + 1))
  done <<< "$hashes"
  [ -z "$bad" ] || fail "issued documents don't match:"$'\n'"$bad"
  echo "  pdf re-hash ok ($n files)"
fi

# Receipts behind expenses and purchase orders: a photo (stored as .jpg) or a PDF.
receipts=$(psql_q "SELECT receipt_sha256 || CASE WHEN receipt_type = 'application/pdf'
                          THEN '.pdf' ELSE '.jpg' END FROM expense WHERE receipt_sha256 IS NOT NULL
                   UNION SELECT receipt_sha256 || CASE WHEN receipt_type = 'application/pdf'
                          THEN '.pdf' ELSE '.jpg' END FROM purchase_order
                          WHERE receipt_sha256 IS NOT NULL")
if [ -z "$receipts" ]; then
  echo "  receipt re-hash: no receipts yet"
else
  rscratch=$(mktemp -d)
  trap 'rm -rf "${scratch:-}" "$rscratch"' EXIT
  patterns=()
  while read -r name; do patterns+=("*/$name"); done <<< "$receipts"
  tar -C "$rscratch" -xf files.tar --wildcards "${patterns[@]}" 2> /dev/null || true
  bad="" n=0
  while read -r name; do
    sha=${name%.*}
    file="$rscratch/${sha:0:2}/$name"
    if [ ! -f "$file" ]; then
      bad+="    $name: not in the archive"$'\n'
      continue
    fi
    got=$(sha256sum "$file" | cut -d' ' -f1)
    [ "$got" = "$sha" ] || bad+="    $name: hashes to $got"$'\n'
    n=$((n + 1))
  done <<< "$receipts"
  [ -z "$bad" ] || fail "receipts don't match:"$'\n'"$bad"
  echo "  receipt re-hash ok ($n files)"
fi

echo "restore drill: PASS"
