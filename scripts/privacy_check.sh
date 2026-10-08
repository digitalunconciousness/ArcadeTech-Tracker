#!/usr/bin/env bash
# The privacy check: what a range of commits would publish, checked before it leaves
# this machine. Run by hand before every push (and the output shown to the owner, clean
# or not), and by .githooks/pre-push, which refuses the push when anything is found.
#
#   scripts/privacy_check.sh                         # origin/main..HEAD (all of HEAD if no origin/main)
#   scripts/privacy_check.sh <rev-list args...>      # e.g. main..phase/0-foundation
#   SHOP_PRIVACY_CI=1 scripts/privacy_check.sh ...   # CI: no owner patterns exist there, so
#                                                    # section 4 is skipped (and says so)
#
# Every commit in the range is checked on its own (git log -p), not just the net diff:
# a secret added in one commit and deleted in the next is still in the history.
#
# Owner-specific values (names, hostnames, the LAN) live in .githooks/patterns.local,
# which is git-ignored: one extended regex per line, # comments and blanks skipped. The
# current login name and home directory are checked without being written anywhere.
# A match from the local file is never echoed, only where it is.
set -uo pipefail

ROOT=$(git rev-parse --show-toplevel) || exit 2
cd "$ROOT" || exit 2
# Local checkout: .githooks/patterns.local. Cloud session: docs/cloud-setup.sh writes it
# under ~/.config, outside the clone. An explicit SHOP_HOOK_PATTERNS beats both.
PATTERNS="${SHOP_HOOK_PATTERNS:-$ROOT/.githooks/patterns.local}"
[ -z "${SHOP_HOOK_PATTERNS:-}" ] && [ ! -r "$PATTERNS" ] && PATTERNS="$HOME/.config/shop-hub/patterns.local"

if [ "$#" -gt 0 ]; then
  RANGE=("$@")
elif git rev-parse -q --verify origin/main >/dev/null; then
  RANGE=(origin/main..HEAD)
else
  RANGE=(HEAD)          # nothing published yet: the whole history is about to be
fi

# The only addresses allowed in commits and content. RFC 2606 / 6761 names are
# reserved and can never belong to anyone, so synthetic data uses them.
readonly EMAIL_OK='^(240414701\+digitalunconciousness@users\.noreply\.github\.com|noreply@anthropic\.com|[^@]+@([a-z0-9-]+\.)*(example\.(com|org|net)|example|test|invalid|localhost))$'

found=0
section() { printf '\n[%s]\n' "$1"; }
clean()   { printf '  clean\n'; }
hit()     { found=1; printf '  %s\n' "$*"; }

mapfile -t COMMITS < <(git rev-list --no-merges "${RANGE[@]}" 2>/dev/null)
printf 'privacy check: %s (%d commit(s))\n' "${RANGE[*]}" "${#COMMITS[@]}"
if [ "${#COMMITS[@]}" -eq 0 ]; then
  printf '  nothing to check\n'
  exit 0
fi

# Added lines as "<sha7>\t<path>\t<line>\t<text>", one commit at a time.
ADDED=$(git log -p -U0 --no-color --no-ext-diff --no-merges --format='commit %h' "${RANGE[@]}" | awk '
  /^commit [0-9a-f]+$/      { sha = $2; next }
  /^\+\+\+ /                { path = substr($0, 5); sub(/^b\//, "", path); next }
  /^@@ /                    { split($3, a, ","); line = substr(a[1], 2) + 0; next }
  /^\+/                     { printf "%s\t%s\t%d\t%s\n", sha, path, line, substr($0, 2); line++; next }
')
MESSAGES=$(git log --no-merges --format='%h%x09%B%x1e' "${RANGE[@]}")

# grep_added LABEL ECHO PATTERN [EXCLUDE]: report added lines matching PATTERN (ERE,
# case-insensitive). ECHO=1 shows the match; 0 shows only where it is.
grep_added() {
  local label=$1 show=$2 pat=$3 excl=${4:-} out
  out=$(printf '%s\n' "$ADDED" | awk -F'\t' -v OFS='\t' '{ t=$4; for (i=5;i<=NF;i++) t=t FS $i; print $1, $2, $3, t }' \
        | while IFS=$'\t' read -r sha path line text; do
            m=$(printf '%s\n' "$text" | grep -oiE -- "$pat") || continue
            if [ -n "$excl" ]; then
              m=$(printf '%s\n' "$m" | grep -viE -- "$excl") || continue
            fi
            if [ "$show" = 1 ]; then
              printf '%s:%s (%s): %s\n' "$path" "$line" "$sha" "$(printf '%s' "$m" | paste -sd' ')"
            else
              printf '%s:%s (%s): [match not shown]\n' "$path" "$line" "$sha"
            fi
          done)
  section "$label"
  if [ -n "$out" ]; then while IFS= read -r l; do hit "$l"; done <<<"$out"; else clean; fi
}

# --- 1. commit identity and time ------------------------------------------------
section "commit author/committer: noreply email, UTC timestamps"
bad_meta=0
for c in "${COMMITS[@]}"; do
  read -r ae ce < <(git show -s --format='%ae %ce' "$c")
  for e in "$ae" "$ce"; do
    printf '%s\n' "$e" | grep -qE "$EMAIL_OK" || { hit "$(git rev-parse --short "$c"): email $e"; bad_meta=1; }
  done
  for d in $(git show -s --format='%ad%n%cd' --date=iso-strict "$c"); do
    case "$d" in *+00:00|*Z) ;; *) hit "$(git rev-parse --short "$c"): non-UTC time $d (use git c)"; bad_meta=1 ;; esac
  done
done
[ "$bad_meta" = 0 ] && clean

# --- 2. paths that must never be committed --------------------------------------
section "forbidden paths (.env, *.sql, *.dump, *.db, files/, backups/, instance/, keys, *.local*)"
FORBIDDEN_PATHS='(^|/)\.env(\.|$)|\.(sql|dump|db|sqlite3?|pem|key|p12|pfx)$|(^|/)(files|backups|instance)/|\.local|(^|/)id_(rsa|ed25519|ecdsa)'
paths=$(git log --no-merges --format= --name-only --diff-filter=ACMR "${RANGE[@]}" | sort -u \
        | grep -E -- "$FORBIDDEN_PATHS" | grep -vE '(^|/)(\.env\.example|patterns\.local\.example)$')
if [ -n "$paths" ]; then while IFS= read -r p; do hit "$p"; done <<<"$paths"; else clean; fi

# --- 3. added content -----------------------------------------------------------
grep_added "private IP addresses (RFC 1918, CGNAT, IPv6 ULA)" 1 \
  '(^|[^0-9.])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}|f[cd][0-9a-f]{2}:[0-9a-f:]+)([^0-9]|$)'

emails=$(printf '%s\n' "$ADDED" | while IFS=$'\t' read -r sha path line text; do
           printf '%s\n' "$text" | grep -oiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
             | grep -viE "$EMAIL_OK" | sed "s|^|$path:$line ($sha): |"
         done)
section "email addresses (allowed: the noreply ones and reserved example/test domains)"
if [ -n "$emails" ]; then while IFS= read -r l; do hit "$l"; done <<<"$emails"; else clean; fi

# The login name identifies the owner only on the owner's machine. In CI (runner) or a
# cloud session (root) it's a generic word that appears in legitimate content, so there
# only the generic home-path patterns are checked.
me=$(id -un)
if [ -n "${SHOP_PRIVACY_CI:-}" ] || [ "$me" = root ]; then
  grep_added "home paths (login name not checked: CI or root)" 1 \
    "(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+)"
else
  grep_added "login name and home paths" 1 \
    "(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|${HOME//./\\.}|(^|[^A-Za-z0-9])${me}([^A-Za-z0-9]|$))"
fi

grep_added "token- and key-shaped strings" 0 \
  'gbx_[0-9a-f]{12}\.[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|sk-(ant-)?[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{30,}\.?|postgres(ql)?(\+psycopg[0-9]?)?://[^:/@[:space:]]+:[^<${@[:space:]][^@[:space:]]*@|(secret[_-]?key|api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|totp[_-]?secret)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{8,}["'"'"']' \
  '(test|example|dummy|fake|changeme|not-a-real|synthetic|placeholder)'

# --- 4. owner-specific patterns -------------------------------------------------
section "local patterns (.githooks/patterns.local)"
if [ -n "${SHOP_PRIVACY_CI:-}" ]; then
  printf '  skipped (CI: owner patterns are local-only; the pre-push hook checks them)\n'
elif [ -r "$PATTERNS" ]; then
  n=0; lhits=0
  while IFS= read -r pat; do
    case "$pat" in ''|\#*) continue ;; esac
    n=$((n + 1))
    where=$(printf '%s\n' "$ADDED" | while IFS=$'\t' read -r sha path line text; do
              printf '%s\n' "$text" | grep -qE -- "$pat" && printf '%s:%s (%s)\n' "$path" "$line" "$sha"
            done)
    msg=$(printf '%s' "$MESSAGES" | grep -cE -- "$pat")
    [ -n "$where" ] && { while IFS= read -r w; do hit "pattern #$n in $w"; done <<<"$where"; lhits=1; }
    [ "$msg" -gt 0 ] && { hit "pattern #$n in a commit message"; lhits=1; }
  done < "$PATTERNS"
  printf '  (%d pattern(s) loaded)\n' "$n"
  [ "$lhits" = 0 ] && clean
else
  found=1
  hit "MISSING: $PATTERNS. Copy .githooks/patterns.local.example and fill it in."
fi

# --- 5. commit messages ---------------------------------------------------------
section "commit messages (IPs, emails, home paths)"
mhits=$(printf '%s' "$MESSAGES" | tr '\036' '\n' \
        | grep -oiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(10|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3})?|/home/[A-Za-z0-9._-]+' \
        | grep -viE "$EMAIL_OK")
if [ -n "$mhits" ]; then while IFS= read -r l; do hit "$l"; done <<<"$mhits"; else clean; fi

printf '\n'
if [ "$found" -ne 0 ]; then
  printf 'privacy check: FOUND SOMETHING (see above)\n'
  exit 1
fi
printf 'privacy check: clean\n'
exit 0
