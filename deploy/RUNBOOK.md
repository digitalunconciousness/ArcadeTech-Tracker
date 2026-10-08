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

Three roles (see `deploy/sql/create_roles.psql` for why):

- `shop_owner` owns the database and every object. Migrations run as it. It's the only
  role that could `DISABLE TRIGGER`, so the app never connects as it.
- `shop_app` is a NOLOGIN group holding exactly the privileges the migrations grant.
- `shop` is the app's login, a member of `shop_app`.

Copy `deploy/sql/create_roles.psql` to the Postgres container from the Proxmox host
(`pct pull <SHOP_CTID> /opt/shop-hub/deploy/sql/create_roles.psql /tmp/create_roles.psql`,
then `pct push <PG_CTID> /tmp/create_roles.psql /tmp/create_roles.psql`), then inside it,
reading the two passwords without echoing them or leaving them in shell history:

```
read -rsp 'shop_owner password: ' OWNER_PW; echo
read -rsp 'shop password: ' APP_PW; echo
runuser -u postgres -- psql -X -v "owner_password=$OWNER_PW" -v "app_password=$APP_PW" \
  -f /tmp/create_roles.psql
unset OWNER_PW APP_PW; rm /tmp/create_roles.psql
```

That also creates `shop_restore_test` (owned by `shop_owner`) for the monthly drill.
There are no grants between `shop` and the barcade's tracker role, in either direction.

`pg_hba.conf` (`runuser -u postgres -- psql -Atc 'SHOW hba_file'`): admit the shop roles
only from the shop LXC, only to their databases, over TLS if the host has it
(`SHOW ssl`; if `off`, write `host` instead of `hostssl` in the two allow lines).

pg_hba is **first match**: put these lines *above* any broader line already there (a
`host all all <subnet> …` for other apps, say). Otherwise the broad line matches first,
and the shop roles can log in from anywhere on that subnet, to any database, without TLS.
The reject lines stop the shop roles at any other database or address:

```
hostssl  shop               shop,shop_owner  <LXC_LAN_IP>/32  scram-sha-256
hostssl  shop_restore_test  shop_owner       <LXC_LAN_IP>/32  scram-sha-256
host     all                shop,shop_owner  0.0.0.0/0        reject
host     all                shop,shop_owner  ::/0             reject
```

Reload and check the order (the shop rows must have lower line numbers than any broader
`host` row, and `error` must be empty):

```
runuser -u postgres -- psql -c 'SELECT pg_reload_conf()'
runuser -u postgres -- psql -c "SELECT line_number, type, database, user_name, address, auth_method, error FROM pg_hba_file_rules ORDER BY line_number"
```

From the shop LXC once it exists: `psql "postgresql://shop@<PG_HOST>/shop?sslmode=require" -c 'SELECT 1'`
must work, and the same with `/postgres` instead of `/shop` must be rejected.

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
# optional: silence perl's locale warning from pg_dump when LANG comes in from the host
# apt install -y locales && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen
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
address from `CF-Connecting-IP`; rate limits are then per real client. Run it anywhere
else and every visitor shares one limit.

Install from Cloudflare's apt repository (it's not in Debian's). If the key URL 404s, use
the Debian block shown at https://pkg.cloudflare.com:

```
mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  > /etc/apt/sources.list.d/cloudflared.list
apt update && apt install -y cloudflared
```

Create the tunnel (`login` prints a URL to open in a browser; pick the zone):

```
cloudflared tunnel login
cloudflared tunnel create shop-hub          # prints the tunnel ID and its credentials .json
cloudflared tunnel route dns shop-hub <SHOP_HOSTNAME>
```

The DNS record is live from here on, so set up **Access before starting the service**.
Zero Trust → Access → Applications → Add → Self-hosted, three times:

1. `<SHOP_HOSTNAME>`: policy **Allow**, Emails = the two owners (later the accountant),
   login method one-time PIN.
2. `<SHOP_HOSTNAME>`, path `api/v1/*`: policy **Bypass**, Everyone (GATBOX devices carry
   their own tokens).
3. `<SHOP_HOSTNAME>`, path `d/*`: policy **Bypass**, Everyone (customer links carry their
   own 32-byte token).

The more specific path wins, so 2 and 3 carve holes in 1.

Config and service. `<TUNNEL_ID>` is from `tunnel create`. The running tunnel needs only
its `.json`; `cert.pem` can manage every tunnel on the zone, so keep it root-only or move
it off the box:

```
chmod 600 /root/.cloudflared/*
install -d -m 755 /etc/cloudflared
install -m 600 /root/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/
cat > /etc/cloudflared/config.yml <<'YAML'
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: <SHOP_HOSTNAME>
    service: http://127.0.0.1:8080
  - service: http_status:404
YAML
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://<SHOP_HOSTNAME>
cloudflared service install
systemctl enable --now cloudflared
```

Check from a private window: `https://<SHOP_HOSTNAME>/` shows the Access PIN page first,
then the app's sign-in; `https://<SHOP_HOSTNAME>/d/x` reaches the app (its 404 page, not
Cloudflare's). Customer estimate links (`/d/<token>`) and calendar feeds
(`/d/cal/<token>.ics`, fetched by Google's servers) only work through app 3: if `/d/x`
shows the Access PIN page, app 3 is missing. What the errors mean: Cloudflare **1033** = no connector running
(`systemctl status cloudflared`); **502/503** = the connector runs but nothing answers on
127.0.0.1:8080 (`systemctl status shop-hub`, `curl -s http://127.0.0.1:8080/healthz`).

## 8. Backups and the restore drill ⏸

```
cp /opt/shop-hub/deploy/systemd/shop-hub-backup.{service,timer} \
   /opt/shop-hub/deploy/systemd/shop-hub-restore-drill.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shop-hub-backup.timer shop-hub-restore-drill.timer
systemctl start shop-hub-backup.service && journalctl -u shop-hub-backup -n 5
systemctl start shop-hub-restore-drill.service && journalctl -u shop-hub-restore-drill -n 20
```

The drill must end `restore drill: PASS`. Timers use the LXC's time zone (UTC on a
stock template, so 03:15 is evening in the US); `timedatectl set-timezone <Area/City>`
moves them. The app always displays in `SHOP_TZ` regardless. Backups land in
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
