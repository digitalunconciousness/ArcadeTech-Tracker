# shop-hub runbook

How the shop LXC is built, deployed, backed up and restored. Every command here is run
by the owner (⏸). Placeholders are in `<ANGLE_BRACKETS>`; nothing site-specific is
committed.

| Placeholder | What |
|---|---|
| `<CTID>` | the new container's Proxmox ID |
| `<LXC_LAN_IP>` | the shop LXC's LAN address |
| `<PG_HOST>` | the existing Postgres host (17.x) |
| `<SHOP_HOSTNAME>` | the shop's public hostname on the tunnel |

Layout on the LXC:

```
/opt/shop-hub            the code (git checkout), owned by root
/opt/shop-hub-venv       the venv, owned by root
/etc/shop-hub/           shop-hub.env  root:shop-hub 640   app role, SECRET_KEY, SHOP_*
                         migrate.env   root:root     600   owner role (migrations, deploy dump)
                         restore.env   root:root     600   owner role on shop_restore_test
                         listen.conf   root:root     644
/var/lib/shop-hub/       files/  backups/  logs/     owned by shop-hub, 750
```

Env files are plain `KEY=value` lines with **no quotes**. Generate passwords and keys as
hex so nothing needs escaping in a URL or a shell:
`python3 -c 'import secrets; print(secrets.token_hex(32))'`.

---

## 1. Postgres host: roles and databases ⏸

Three roles (see `deploy/sql/create_roles.sql` for why):

- `shop_owner` owns the database and every object. Migrations run as it. It's the only
  role that could `DISABLE TRIGGER`, so the app never connects as it.
- `shop_app` is a NOLOGIN group holding exactly the privileges the migrations grant.
- `shop` is the app's login, a member of `shop_app`.

Copy `deploy/sql/create_roles.sql` to the Postgres host, then:

```
sudo -u postgres psql -X -v owner_password=<HEX1> -v app_password=<HEX2> -f create_roles.sql
```

That also creates `shop_restore_test` (owned by `shop_owner`) for the monthly drill.
There are no grants between `shop` and the barcade's tracker role, in either direction.

`pg_hba.conf`: admit only the shop LXC, only to its databases, over TLS if the host
has it (use `host` instead of `hostssl` if not):

```
hostssl  shop               shop,shop_owner  <LXC_LAN_IP>/32  scram-sha-256
hostssl  shop_restore_test  shop_owner       <LXC_LAN_IP>/32  scram-sha-256
```

```
sudo -u postgres psql -c 'SELECT pg_reload_conf()'
```

## 2. Create the LXC ⏸

An unprivileged Debian 13 container. Nesting on, so systemd's sandboxing works
(without it, `shop-hub.service` fails with status 226/NAMESPACE).

```
pveam update
pveam available --section system | grep debian-13
pct create <CTID> local:vztmpl/<debian-13-template>.tar.zst \
  --hostname shop-hub --unprivileged 1 --features nesting=1 \
  --cores 2 --memory 1024 --swap 512 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=<LXC_LAN_IP>/<PREFIX>,gw=<GATEWAY> \
  --onboot 1
pct start <CTID>
pct enter <CTID>
```

## 3. Packages and the Python check ⏸

```
apt update && apt full-upgrade -y
apt install -y git curl ca-certificates python3 python3-venv postgresql-client \
  libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core
python3 --version        # must print 3.13.x; stop and tell Claude if not
pg_dump --version        # 17.x (Debian 13's client matches the 17.9 server)
```

The Pango and HarfBuzz libraries are WeasyPrint's (PDF documents). Record the
`python3 --version` result in PROGRESS.md (Session 1's open box).

## 4. Service user, directories, code, venv ⏸

```
adduser --system --group --home /var/lib/shop-hub --no-create-home shop-hub
install -d -o shop-hub -g shop-hub -m 750 /var/lib/shop-hub \
  /var/lib/shop-hub/files /var/lib/shop-hub/backups /var/lib/shop-hub/logs
install -d -m 755 /etc/shop-hub

git clone https://github.com/digitalunconciousness/ArcadeTech-Tracker.git /opt/shop-hub
python3 -m venv /opt/shop-hub-venv
/opt/shop-hub-venv/bin/pip install --require-hashes -r /opt/shop-hub/requirements.txt
```

## 5. Configuration ⏸

```
umask 027
cat > /etc/shop-hub/shop-hub.env <<'EOF'
DATABASE_URL=postgresql+psycopg://shop:<HEX2>@<PG_HOST>:5432/shop?sslmode=require
SECRET_KEY=<HEX3>
SHOP_TZ=<Area/City>
SHOP_BASE_URL=https://<SHOP_HOSTNAME>
EOF
chown root:shop-hub /etc/shop-hub/shop-hub.env && chmod 640 /etc/shop-hub/shop-hub.env

umask 077
cat > /etc/shop-hub/migrate.env <<'EOF'
MIGRATE_DATABASE_URL=postgresql+psycopg://shop_owner:<HEX1>@<PG_HOST>:5432/shop?sslmode=require
EOF
cat > /etc/shop-hub/restore.env <<'EOF'
RESTORE_DATABASE_URL=postgresql+psycopg://shop_owner:<HEX1>@<PG_HOST>:5432/shop_restore_test?sslmode=require
EOF
chmod 600 /etc/shop-hub/migrate.env /etc/shop-hub/restore.env

cp /opt/shop-hub/deploy/listen.conf.example /etc/shop-hub/listen.conf
editor /etc/shop-hub/listen.conf          # put in <LXC_LAN_IP>
```

Drop `?sslmode=require` if the Postgres host has no TLS (and use `host` in pg_hba).

## 6. Schema, first owner, service ⏸

```
cd /opt/shop-hub
set -a; . /etc/shop-hub/shop-hub.env; . /etc/shop-hub/migrate.env; set +a
/opt/shop-hub-venv/bin/flask --app wsgi db upgrade
/opt/shop-hub-venv/bin/python scripts/create_owner.py        # prompts; nothing on argv
set +a; unset DATABASE_URL MIGRATE_DATABASE_URL SECRET_KEY

cp deploy/systemd/shop-hub.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shop-hub
systemctl status shop-hub
curl -s http://127.0.0.1:8080/healthz      # {"db":true,"ok":true}
```

From the LAN, `http://<LXC_LAN_IP>:8080/login` works before the tunnel does (cookies
need https, so signing in waits for step 7, or set `SHOP_COOKIE_SECURE=0` temporarily).

## 7. Cloudflare tunnel and Access ⏸

cloudflared runs **in this LXC**, so the app sees it as 127.0.0.1 and takes the client
address from `CF-Connecting-IP` (rate limits are per real client). Install it from
Cloudflare's apt repository (pkg.cloudflare.com, per their current instructions), then:

```
cloudflared tunnel login
cloudflared tunnel create shop-hub
cloudflared tunnel route dns shop-hub <SHOP_HOSTNAME>
```

Config: start from `deploy/cloudflared-ingress.yml.example` (hostname →
`http://127.0.0.1:8080`), then `cloudflared service install` and
`systemctl enable --now cloudflared`.

**Cloudflare Access** (decided 2026-10-07): Zero Trust → Access → Applications.

1. Self-hosted app `<SHOP_HOSTNAME>`, policy **Allow**: emails = the two owners
   (and later the accountant), login method one-time PIN.
2. Self-hosted app `<SHOP_HOSTNAME>/api/v1/*`, policy **Bypass**: Everyone (GATBOX
   devices authenticate with their own tokens).
3. Self-hosted app `<SHOP_HOSTNAME>/d/*`, policy **Bypass**: Everyone (customer
   document links carry their own 32-byte token).

The more specific path wins, so 2 and 3 carve holes in 1. Check: a private window on
`https://<SHOP_HOSTNAME>/` gets the Access PIN page; `https://<SHOP_HOSTNAME>/d/x`
reaches the app (a 404 from the app, not Cloudflare's page).

## 8. Backups and the restore drill ⏸

```
cp /opt/shop-hub/deploy/systemd/shop-hub-backup.{service,timer} \
   /opt/shop-hub/deploy/systemd/shop-hub-restore-drill.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shop-hub-backup.timer shop-hub-restore-drill.timer
systemctl start shop-hub-backup.service && journalctl -u shop-hub-backup -n 5
systemctl start shop-hub-restore-drill.service && journalctl -u shop-hub-restore-drill -n 20
```

The drill must end `restore drill: PASS`. Backups land in
`/var/lib/shop-hub/backups/{daily,monthly,yearly}/<UTC stamp>/` (14 / 12 / 7 kept).

**Off-site copy (⏸, decide the target):** a cluster isn't a backup against a house
fire. `backup.sh` runs `/etc/shop-hub/backup-offsite <run dir>` when that file is
executable (as `shop-hub`). The usual shape is restic to object storage with the
repository password in a root-owned file readable by `shop-hub`:

```
#!/bin/sh
exec restic --repo <REPO> --password-file /etc/shop-hub/restic.pass backup --tag shop-hub "$1"
```

## 9. Deploying a release ⏸

After a PR is merged to `main`:

```
/opt/shop-hub/deploy/deploy.sh --check     # read-only; fix anything marked FAIL
/opt/shop-hub/deploy/deploy.sh
```

It dumps the database to `backups/predeploy/` as `shop_owner`, fast-forwards `main`,
installs the hash-pinned requirements, runs migrations, restarts and checks `/healthz`.
On any failure it prints the exact rollback commands (reset to the previous commit,
reinstall, and `flask db downgrade` or `pg_restore` of the pre-deploy dump).

## 10. Last-resort fixes ⏸

- **Owner locked out of 2FA, no other owner available:**
  `cd /opt/shop-hub && set -a && . /etc/shop-hub/shop-hub.env && set +a && /opt/shop-hub-venv/bin/python scripts/reset_2fa.py`
- **Restore the live database from a backup** (stop the app first):

  ```
  systemctl stop shop-hub
  set -a; . /etc/shop-hub/migrate.env; set +a
  pg_restore --clean --if-exists --no-owner --single-transaction \
    -d "${MIGRATE_DATABASE_URL/postgresql+psycopg:/postgresql:}" < /var/lib/shop-hub/backups/daily/<STAMP>/shop.dump
  tar -C /var/lib/shop-hub/files -xf /var/lib/shop-hub/backups/daily/<STAMP>/files.tar
  systemctl start shop-hub
  ```
