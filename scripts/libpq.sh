# Sourced by deploy.sh, backup.sh and restore_drill.sh: connect libpq tools without the
# password on their command line, where anyone on the box can read it (ps, /proc/*/cmdline).
#
#   pg_split "$SOME_DATABASE_URL"
#   PGPASSWORD=$PG_PW psql ... "$PG_NOPW"
#
# pg_split sets PG_NOPW (the URL as libpq takes it, without its password) and PG_PW (the
# password, decoded, or empty). PGPASSWORD set for one command lives in that process's
# environment, which only its own user (and root) can read. The URL reaches Python
# through the environment too, never argv.

pg_split() {
  local out
  out=$(PG_SPLIT_URL="$1" python3 -I -c '
import os, urllib.parse as u
url = os.environ["PG_SPLIT_URL"].replace("postgresql+psycopg:", "postgresql:", 1)
p = u.urlsplit(url)
host = p.hostname or ""
if ":" in host:
    host = "[" + host + "]"
user = (u.quote(u.unquote(p.username), safe="") + "@") if p.username else ""
port = (":%d" % p.port) if p.port else ""
print(u.urlunsplit((p.scheme, user + host + port, p.path, p.query, p.fragment)))
print(u.unquote(p.password or ""))') || return 1
  PG_NOPW=${out%%$'\n'*}
  PG_PW=${out#*$'\n'}
  [ "$PG_PW" != "$out" ] || PG_PW=""
}
