# Production cut-over runbook (Deployment-2B)

Status (2026-09-30): **not started.** Deployment-2A delivered the images, the production compose
file, Caddy, systemd units, backup/freshness/notification scripts, and this runbook. Nothing has
been provisioned, no domain or DNS exists, and the development Mac's launchd agent remains the
operational daily update until step 20.

Every step is supervised by the owner. Stop at any failed check; the Mac keeps running unchanged,
so stopping is always safe (rollback = do nothing further; delete the server if abandoned).

## Decisions needed before step 1

| Decision | Needed for |
| --- | --- |
| VPS provider and region | step 1; Chinese and international sources must answer from its IP (step 14) |
| Domain / hostname (`APP_DOMAIN`) | steps 4, 10 (DNS A/AAAA record to the server before Caddy starts) |
| Notification channel | step 18 (`/etc/industry-intelligence/hooks/notify`) |
| Off-host backup destination | step 16 (`/etc/industry-intelligence/hooks/backup-remote`) |
| Database: restore the development dump (recommended) or start empty | step 6 |

## Server requirements

- Linux with systemd ≥ 235 (IANA zone in `OnCalendar`; verified with systemd 257), e.g. Debian 13
  or Ubuntu 24.04 LTS; Docker Engine with the Compose v2 plugin (docker.com packages).
- Public IPv4 (IPv6 optional); inbound 22, 80, 443 (TCP) and 443/UDP (HTTP/3, optional);
  outbound HTTPS to US and Chinese publishers.
- Size (measured on the Deployment-2A local production stack with the restored development data):
  at rest backend 170 MiB, frontend 90 MiB, Caddy 22 MiB, PostgreSQL 30 MiB plus its cache
  (default `shared_buffers` 128 MB); a daily-update container adds a Python process for about a
  minute; images 1.9 GB; database 73 MB on disk; dumps 1.4 MB each. Building the Next.js image on
  the server needs roughly 1–1.5 GB of memory. **Minimum 2 vCPU / 2 GB RAM + 2 GB swap / 40 GB
  disk; 4 GB RAM is comfortable.**

Conventions: repository at `/opt/industry-intelligence`, env file
`/etc/industry-intelligence/production.env`, hooks in `/etc/industry-intelligence/hooks/`,
backups in `/var/backups/industry-intelligence/`; commands as root; `ii` =
`/opt/industry-intelligence/deploy/bin/ii-compose`.

## Steps

### 1. Provision the server
Create the VPS (owner, with the chosen provider/region). Log in with an SSH key; disable password
login; apply OS updates.

### 2. Install Docker Engine and Compose
Install `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin` from Docker's apt
repository; `systemctl enable --now docker`; check `docker compose version`.

Copy the approved commit (the repository has no Git remote):

```bash
# on the Mac
git archive --format=tar.gz -o ii-<commit>.tar.gz <commit>
scp ii-<commit>.tar.gz root@SERVER:/root/
# on the server
install -d /opt/industry-intelligence && tar -xzf /root/ii-<commit>.tar.gz -C /opt/industry-intelligence
```

### 3. Configure the firewall
Allow only SSH, HTTP, HTTPS (plus the provider's cloud firewall with the same rules, if any):

```bash
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw enable && ufw status verbose
```

Docker's published ports bypass ufw; that is acceptable here only because Caddy (80/443) is the
sole service with published ports. Never add a `ports:` entry to postgres, backend, or frontend.

### 4. Configure the production env file and hooks

```bash
install -d -m 700 /etc/industry-intelligence /etc/industry-intelligence/hooks
install -m 600 /opt/industry-intelligence/deploy/production.env.example \
    /etc/industry-intelligence/production.env
openssl rand -hex 24          # use as POSTGRES_PASSWORD and inside DATABASE_URL
editor /etc/industry-intelligence/production.env
```

Set `APP_DOMAIN`, `NEXT_PUBLIC_API_BASE_URL=https://<APP_DOMAIN>`, `CORS_ORIGINS=https://<APP_DOMAIN>`,
`POSTGRES_PASSWORD`, `DATABASE_URL=postgresql+psycopg://industry:<password>@postgres:5432/industry_intelligence`.
Leave AI variables unset. Install the notification and off-host hooks from
`deploy/hooks/*.example` (mode 700) once the channel/destination is chosen. Check nothing leaks:
`ii config --quiet` (prints nothing on success; never paste `ii config` output anywhere, it
contains the resolved secrets).

### 5. Start PostgreSQL

```bash
cd /opt/industry-intelligence
ii build                 # backend and frontend images (frontend bakes https://<APP_DOMAIN>)
ii up -d --wait postgres
```

### 6. Restore the development database (if chosen)
On the Mac, after its latest 10:00 run, take the dump and copy it over:

```bash
docker exec app-db-1 sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > ii-cutover.dump
head -c 5 ii-cutover.dump; echo    # PGDMP
scp ii-cutover.dump root@SERVER:/root/
```

On the server, restore it as in [restore.md](restore.md) §3–4 (empty database, **no** migration
before the restore). The production database's history starts from this dump; later Mac runs are
never merged into it.

If starting empty instead: `ii run --rm -T backend alembic upgrade head` (the explicit migration).

### 7. Verify the Alembic revision

```bash
ii run --rm -T backend alembic current     # == heads, 20260930_12 today
ii run --rm -T backend alembic heads
```

Later schema changes follow the same explicit order: backup → `ii run --rm -T backend alembic
upgrade head` → `data-quality-audit` → `ii up -d`. Migrations never run on container start.

### 8. Run the data-quality audit

```bash
ii run --rm -T backend python -m industry_intelligence.cli data-quality-audit   # 0 errors
```

### 9. Start backend, frontend, and Caddy
The DNS record for `APP_DOMAIN` must already point at the server (Caddy requests the certificate
on start).

```bash
ii up -d --wait && ii ps
```

### 10. Verify HTTPS and health

```bash
curl -fsS https://$APP_DOMAIN/health            # {"status":"ok"}
curl -fsS https://$APP_DOMAIN/health/db         # {"status":"ok","database":"connected"}
curl -fsS -o /dev/null -w '%{http_code}\n' https://$APP_DOMAIN/          # 200
curl -fsS -o /dev/null -w '%{http_code}\n' https://$APP_DOMAIN/docs      # 404 (hidden)
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://$APP_DOMAIN/   # 308 → https
```

Open the site in a browser (English and 简体中文; Home, Today, an Event, Rankings, Search).
From outside the server, `nc -vz SERVER 5432` and `nc -vz SERVER 8000` must fail.

### 11. Plan the update (read-only)

```bash
ii run --rm -T backend python -m industry_intelligence.cli intelligence-update --plan --skip-ai
```

### 12. One supervised real update

```bash
BACKUP_REMOTE_HOOK=/nonexistent deploy/bin/backup.sh     # local pre-run dump (exit 3 expected)
ii run --rm -T backend python -m industry_intelligence.cli intelligence-update --skip-ai; echo "exit=$?"
```

Exit 0 success, 3 degraded, 1 failed, 4 already running.

### 13. Inspect source health

```bash
ii run --rm -T backend python -m industry_intelligence.cli source-health
ii run --rm -T backend python -m industry_intelligence.cli update-freshness
```

### 14. Chinese and international source gate
This is the first proof that the publishers answer from the server's IP. All 25 automatic
sources should be `healthy`; in particular the Chinese publishers (IT之家, 虎嗅, 东方财富,
新浪财经, 游民星空, 3DM, GameLook) and the international ones (Hacker News, GitHub, CoinGecko,
CoinPaprika, Steam, itch.io, CNBC, Nasdaq, TechCrunch, Ars Technica, GamesIndustry.biz, Gematsu,
CompaniesMarketCap).

If a source fails for network reasons (timeouts, connection resets, HTTP 403/451 from a WAF):
do **not** weaken parsers or evidence rules, do not switch clients or fake a browser User-Agent,
and do not bypass the publisher's restriction. Investigate the network instead (provider,
region, IPv4 vs IPv6); a different region or provider is the remedy. Do not enable the timers
until every source is healthy or the owner accepts a documented exception.

### 15. Enable the daily update timer

```bash
install -m 644 deploy/systemd/industry-intelligence-*.service deploy/systemd/industry-intelligence-*.timer \
    /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/industry-intelligence-*
systemd-analyze calendar '*-*-* 10:00:00 Australia/Sydney'
systemctl enable --now industry-intelligence-update.timer
```

### 16. Enable the backup timer

```bash
deploy/bin/backup.sh; echo "exit=$?"    # 0 once the off-host hook works
systemctl enable --now industry-intelligence-backup.timer
```

### 17. Enable the freshness timer

```bash
systemctl enable --now industry-intelligence-freshness.timer
systemctl list-timers 'industry-intelligence-*'
```

### 18. Verify notifications
Force one failure through the same `OnFailure=` path and confirm the message arrives:

```bash
systemd-run --unit=ii-notify-test \
    --property=OnFailure=industry-intelligence-notify@ii-notify-test.service.service /bin/false
journalctl -u 'industry-intelligence-notify@*' -n 20 --no-pager     # notify: event=sent …
```

Also add an external uptime check on `https://<APP_DOMAIN>/health/db` (owner's choice of service).

### 19. Observe the first scheduled production run
Next day after 10:00 Sydney: `systemctl status industry-intelligence-update.service`,
`journalctl -u industry-intelligence-update.service --since today`, `source-health`; after 11:00
the new dump and its off-host copy; after 12:30 `journalctl -u industry-intelligence-freshness`.

### 20. Decide about the Mac launchd agent
Only after step 19 succeeded, the owner decides whether to disable the Mac agent
(`launchctl bootout gui/$(id -u)/local.industry-intelligence.daily-update`). Until then both run
independently against their own databases (twice the source requests per day, no shared state).
