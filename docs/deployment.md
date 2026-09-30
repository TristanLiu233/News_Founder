# Deployment

Status (2026-09-30): **Deployment-2A — Production Packaging & Infrastructure templates complete.**
Nothing is provisioned or deployed. The development Mac remains the valid *local operation* (daily
launchd update at 10:00 Australia/Sydney) until the supervised cut-over
([cutover.md](cutover.md), Deployment-2B). Deployment-1 (audit) findings are kept below; §8 lists
what Deployment-2A built and §9 the production operations; restores follow [restore.md](restore.md).

**Local-1 (2026-09-30): local runtime ≠ production packaging.** The Mac's database is now native
PostgreSQL 16 (launchd service, `127.0.0.1:5433`) and daily local operation no longer needs Docker
Desktop (README "Local runtime"). Docker Compose remains the planned production mechanism; every
Deployment-2A artifact below is unchanged. Where §1, §5, and §7 mention Docker Desktop for the
local database, they record the Deployment-1 state.

## 1. Current runtime (development Mac)

| Component | How it starts | What keeps it alive | State it owns | After reboot | Developer-machine dependency |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL 16.15 + pgvector 0.8.6 + pg_trgm (since Local-1) | native: launchd agent `local.industry-intelligence.postgresql` (`postgres -D ~/Library/Application Support/industry-intelligence/postgresql16`, conda env `ii-pg16`), `127.0.0.1:5433` | launchd (`RunAtLoad`, `KeepAlive`) | everything (data directory above) | starts at login | logged-in user; conda env `ii-pg16` with a locally built pgvector |
| (before Local-1; optional/rollback) PostgreSQL in Docker | `docker compose up -d` (`pgvector/pgvector:pg16`, container `app-db-1`, `127.0.0.1:5432`) | Docker Desktop; container restart policy `no` | named volume `app_postgres_data` (kept as rollback, stopped) | down until Docker Desktop is opened | Docker Desktop |
| FastAPI | manually: `uvicorn industry_intelligence.main:app --reload` from `backend/` | nothing (terminal) | none (stateless) | down | manual start, `.venv` in the checkout |
| Next.js | manually: `npm run dev` from `frontend/` | nothing (terminal) | none | down | manual start |
| Daily update | launchd agent `local.industry-intelligence.daily-update`, 10:00 system time zone | launchd (per user) | writes PostgreSQL only | runs at the next 10:00 if the user is logged in | per-user LaunchAgent, logged-in session, `~/Documents` access of the Python app, root `.env` |
| AI provider | never at page load; CLI only | — | artifacts in PostgreSQL | — | `AI_*` in root `.env` (scheduled AI is off) |
| External sources | only inside `intelligence-update` / manual CLI | — | — | — | outbound HTTPS from the Mac's network |

- Configuration: `Settings` reads environment variables, then the repository-root `.env`
  (resolved relative to the source tree, so it works only from an editable install or checkout);
  the frontend reads `NEXT_PUBLIC_API_BASE_URL` from `frontend/.env.local` **at build time**.
- Request flow: browser → Next.js (all pages are client components) → the browser itself calls
  the FastAPI origin in `NEXT_PUBLIC_API_BASE_URL` (CORS) → PostgreSQL. The API exposes only
  `GET`/`HEAD`; there is no write endpoint.
- **Only PostgreSQL holds production state.** Logs (`~/Library/Logs/industry-intelligence/`) and
  `frontend/.next` are disposable. Run history lives in `update_runs` / `source_attempts`.

### Local operation vs production

Current operation is **valid local operation**, not production.

| Local assumption | Classification |
| --- | --- |
| Mac powered on, awake, user logged in at 10:00 | local-only limitation → irrelevant after deployment |
| Docker Desktop started by hand; container restart policy `no` | production blocker if kept → replaced by a server runtime; locally resolved by Local-1 (native PostgreSQL launchd service) |
| Python must reach the checkout under `~/Documents` (macOS privacy) | local-only limitation → irrelevant after deployment |
| Per-user LaunchAgent | local-only limitation → replaced by a server scheduler |
| Backend/frontend started from terminals, `--reload` / `next dev` | production blocker → process manager needed |
| PostgreSQL published on `0.0.0.0:5432` with the placeholder password | local security issue; fixed in the compose file (loopback only, takes effect when the container is next recreated) |

## 2. Configuration

Secrets live only in an untracked env file (locally the root `.env`; in production a root-only
file on the server). `.env` and `.env.*` are gitignored; `.env.example` holds placeholders, and
blank values, `example.com`/`.invalid`/`.test` contacts, and `your-…`/`change-me`/`<…>` AI values
count as unset.

| Variable | Class | Notes |
| --- | --- | --- |
| `DATABASE_URL` | required production secret | contains the DB password |
| `POSTGRES_PASSWORD` | required production secret | only for the compose-managed database container |
| `POSTGRES_DB`, `POSTGRES_USER` | required production non-secret | compose database container |
| `ENVIRONMENT` | required production non-secret | `production` hides `/docs`, `/redoc`, `/openapi.json` and rejects `*` in CORS |
| `CORS_ORIGINS` | required production non-secret | the public frontend origin |
| `NEXT_PUBLIC_API_BASE_URL` | required production non-secret, **build-time** | the public origin; changing it requires a rebuild |
| `LOG_LEVEL`, `APP_NAME` | optional | defaults `INFO`, app name |
| `SOURCE_FETCH_LIMIT`, `SOURCE_HTTP_TIMEOUT` | optional | defaults 30 items, 20 s |
| `SEARCH_*`, `RRF_K`, `FRESHNESS_HALF_LIFE`, `RANK_LIST_WEIGHTS` | optional | ranking/search tuning; defaults are the tested values |
| `EMBEDDING_*` | optional | no embedding provider is configured |
| `SOURCE_CONTACT` | optional secret (personal contact) | Wikipedia rankings are manual-only and still HTTP 403; not needed for the daily run |
| `SEC_CONTACT` | optional secret (personal contact) | only for manual `company-profile-ingest` |
| `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`, `AI_*` | optional (key is a secret) | scheduled AI is off; not needed in production until the owner decides |
| `POSTGRES_PORT` | development-only | local port mapping |
| `DATABASE_URL=…_test` override | test-only | integration tests that write |

## 3. Production commands (verified from a clean checkout, 2026-09-30)

Deployment-1 verified these from a plain checkout; the production containers of Deployment-2A
run the same commands inside the images (§8, §9).

```bash
# Backend (Python 3.12): reproducible install with the tested versions
python3.12 -m venv .venv
.venv/bin/python -m pip install -c backend/constraints.txt -e ./backend   # add [ai] only if AI is enabled

# Database bootstrap: PostgreSQL 16 with the pgvector package installed on the server.
# The first migration runs CREATE EXTENSION vector / pg_trgm, so the migrating role needs that right.
cd backend && ../.venv/bin/alembic upgrade head        # single head 20260930_12

# API (no --reload; stateless, so several workers are safe: advisory locks are CLI-only)
../.venv/bin/uvicorn industry_intelligence.main:app --host 127.0.0.1 --port 8000 --workers 2
curl -fsS http://127.0.0.1:8000/health && curl -fsS http://127.0.0.1:8000/health/db

# Frontend (Node >= 20.9; verified on Node 24 / npm 11): three routes are dynamic, so a Node server
# is required (no static export)
cd frontend && npm ci && NEXT_PUBLIC_API_BASE_URL=https://your-domain npm run build && npm start -- -p 3000

# Daily update (the only scheduled command) and its health report
cd backend && ../.venv/bin/python -m industry_intelligence.cli intelligence-update --skip-ai
../.venv/bin/python -m industry_intelligence.cli source-health
```

Packaging constraint: `Settings` (root `.env`) and the update precheck (`backend/alembic.ini`)
locate files relative to the source tree, so the backend must run from a checkout with an
editable install (or the equivalent source layout in an image). A non-editable
`pip install ./backend` makes the precheck fail closed (exit 1, nothing written).

## 4. Backup and restore

- PostgreSQL is the only authoritative store (Subjects, Companies, content, Events, rank
  observations, search documents, AI artifacts, redirects, retired IDs, `update_runs`,
  `source_attempts`).
- Size (2026-09-30): database 23 MB; `pg_dump -Fc` 1.4 MB, dumped in 0.2 s.
- Verified 2026-09-30: dump of the development database → restore into a disposable database →
  all 35 table row counts identical, `alembic_version` `20260930_12`, `vector`/`pg_trgm` present,
  `data-quality-audit` 0 errors / 0 warnings on the copy; disposable database dropped.
  `pg_restore` needs a seekable file (restoring from a pipe fails with "did not find magic string").

```bash
pg_dump -Fc -d industry_intelligence > ii-$(date +%F).dump
createdb ii_restore_check && pg_restore -d ii_restore_check --no-owner --exit-on-error ii-YYYY-MM-DD.dump
```

Policy (personal project scale):

| Item | Value |
| --- | --- |
| Type | logical `pg_dump -Fc` of the whole database |
| Frequency | daily at 11:00 Australia/Sydney (after the 10:00 update), plus a manual dump before every migration or repair `--apply` |
| Retention | 14 daily + 6 monthly (first dump of each month) — about 30 MB today |
| Storage | on the server **and** one off-host copy (object storage or a pull to the owner's Mac); a backup on the same disk only is not a backup |
| Restore test | monthly and after any schema change: restore into a disposable database, compare table counts, run `data-quality-audit` |

## 5. Production architecture (chosen)

**Single Linux VPS running Docker Engine + Docker Compose, Caddy in front, systemd timers for the
schedule and backups.**

```text
Internet ──HTTPS──> Caddy (80/443, automatic Let's Encrypt)
                     ├── /api/*, /health, /health/db ──> backend  (uvicorn, 2 workers, internal network)
                     └── everything else ─────────────> frontend (next start, internal network)
backend ──> db (pgvector/pgvector:pg16, named volume, no published port)
systemd timer 10:00 Australia/Sydney ──> docker compose run --rm backend … intelligence-update --skip-ai
systemd timer 11:00 Australia/Sydney ──> pg_dump -Fc → /var/backups + off-host copy
systemd timer 12:30 Australia/Sydney ──> docker compose run --rm backend … update-freshness
any non-zero exit ──> OnFailure= → notification hook
```

(As built in Deployment-2A: the backend also joins an egress-capable network because the CLI jobs
fetch sources; PostgreSQL alone sits on the internal network.)

- **Same-origin reverse proxy**: the frontend is built with `NEXT_PUBLIC_API_BASE_URL=https://<domain>`;
  browser API calls stay on one origin, the backend is never directly public, and
  `CORS_ORIGINS=https://<domain>`.
- **Secrets**: `/etc/industry-intelligence/production.env` (root, mode 600) passed to compose by
  `deploy/bin/ii-compose`; nothing in the repository or images.
- **Scheduler**: systemd `OnCalendar=*-*-* 10:00:00 Australia/Sydney` (systemd ≥ 235 accepts an
  IANA zone, so 10:00 follows AEST/AEDT with no UTC offset; DST switches happen at 02:00/03:00, so
  10:00 is never skipped or doubled), `Persistent=true` (a missed run after downtime starts at
  boot). It runs exactly `intelligence-update --skip-ai`; the PostgreSQL advisory lock remains the
  only concurrency control (no second lock). Exit codes: 0 success, 3 degraded, 1 failed,
  4 already running.
- **Restart**: compose `restart: unless-stopped` on db/backend/frontend/caddy; Docker Engine
  enabled at boot; backend and the timer depend on the db health check.

### Options compared

| | A. Current Mac | B. Single VPS (chosen) | C. Managed platform |
| --- | --- | --- | --- |
| FastAPI / Next.js | manual processes | containers, restart policy | native services |
| PostgreSQL + pgvector | Docker Desktop | same image as today, private network | managed; pgvector availability and extension rights vary by provider |
| Daily 10:00 Sydney | launchd (verified) | systemd timer with IANA zone | cron schedules are usually UTC-only → two entries or a 1-hour seasonal drift |
| Backups | manual | own timer + off-host copy | usually included on paid tiers |
| HTTPS / exposure | none (localhost) | Caddy, automatic | included |
| Restart after reboot | needs login + Docker Desktop | automatic | automatic |
| Cost | none | one small VM (≈ 2 vCPU, 2–4 GB RAM, 40 GB disk) | several billed services + database |
| Complexity | lowest, not public | one box to patch | least ops, most platform coupling |

## 6. Observability and failure visibility

Existing signals: `/health` (process), `/health/db` (database), `intelligence-update` exit codes and
stage summary, `update_runs` / `source_attempts`, `source-health`, `data-quality-audit`, the
scheduler log/journal. They report; nothing alerts.

| Failure | Detected (Deployment-1) by | Added in Deployment-2A (§9) unless noted |
| --- | --- | --- |
| frontend / backend / database down | `/health`, `/health/db` if someone looks | container health checks + `restart: unless-stopped`; an external uptime check on `https://<domain>/health/db` stays an owner step at cut-over |
| daily update exit 1 / 3 / 4 | exit code in the log / journal | systemd `OnFailure=` notification for any non-zero exit (real status kept) |
| job never ran (host down, timer broken) | absence of an `update_runs` row | `update-freshness` at 12:30 Sydney (Deployment-2A): fails when no successful run started in the last 26 h, wired to the same notification |
| source failing / degraded | `source-health` | exit 3 notifies; read `source-health` after each notification |
| data-quality error | pre/postcheck fail the run (exit 1) | covered by the exit-code notification |
| backup failed / not copied off-host | — | backup timer with the same `OnFailure=` notification (exit 1 / 3) |

Security headers are set by Caddy (Deployment-2A). Nice-to-have later: basic rate limiting at the proxy, log shipping. No
Prometheus/Grafana/ELK/Sentry is needed at this scale.

## 7. Readiness (Deployment-1 result)

| Area | State | Evidence / blocker → minimum action |
| --- | --- | --- |
| Backend runtime | BLOCKED | runs cleanly from a fresh venv with 2 workers, health 200, empty DB OK; no process manager or image → Deployment-2 backend image + compose service |
| Frontend runtime | BLOCKED | clean `npm ci`/lint/typecheck/build/`npm start` pass; no process manager or image, API origin baked at build → frontend image built with the public origin |
| Database | BLOCKED | PG16 + pgvector works, 23 MB; runs only under Docker Desktop with no restart policy → db service on the server, private network, volume |
| Migrations | READY | single head `20260930_12`; empty DB → `alembic upgrade head` → audit 0/0 verified |
| Secrets | READY (process) | none tracked or in history; production env file is Deployment-2 work |
| Backups | BLOCKED | dump/restore verified; nothing scheduled or off-host → backup timer + off-host copy |
| Scheduler | BLOCKED (for production) | verified locally only → systemd timer on the server |
| Observability | BLOCKED | no alerting for missed/failed runs or downtime → uptime check, `OnFailure=`, freshness check |
| Recovery | BLOCKED | restore procedure verified; no server to restore to, no runbook → Deployment-2 runbook |
| Security | BLOCKED | API read-only, docs hidden in production, PG loopback-only locally; no HTTPS, proxy, or firewall → Caddy + firewall (22/80/443) |
| Reproducibility | READY | clean checkout → fresh venv (`constraints.txt` reproduces the tested set exactly) → fresh DB → API → `npm ci` build |
| AI | OPTIONAL / DISABLED | page loads never call a provider; daily command stays `--skip-ai` |

After Deployment-2A the packaging blockers are resolved in templates (backend/frontend images,
database service, backup timer, systemd scheduler, `OnFailure=` + freshness alerting, restore and
cut-over runbooks, Caddy with HTTPS and firewall procedure), all verified locally (§8). What remains
needs the server itself and owner decisions: provisioning, DNS, a real certificate, the
notification and off-host backup destinations, source reachability from the server's IP, and the
first scheduled production run (Deployment-2B).

## 8. Deployment-2A — what was built (2026-09-30)

Architecture unchanged from §5 (no concrete blocker found). Files:

| File | Purpose |
| --- | --- |
| `backend/Dockerfile`, `backend/.dockerignore` | one backend image (Python 3.12 slim) for the API **and** every CLI job: source layout at `/app/backend`, editable install with `constraints.txt` (so `Settings` and the precheck's `backend/alembic.ini` resolve exactly as in a checkout; `/app/.env` does not exist, configuration comes only from the container environment), `ENVIRONMENT=production`, user `app` (uid 10001), `uvicorn industry_intelligence.main:app --host 0.0.0.0 --port 8000 --workers 2` (no `--reload`), health check on `/health`. `--build-arg EXTRAS=[ai]` adds the optional Anthropic SDK (not needed; scheduled AI is off). |
| `frontend/Dockerfile`, `frontend/.dockerignore` | Node 24: `npm ci` → `npm run build` with build arg `NEXT_PUBLIC_API_BASE_URL` (required) → runtime stage with `npm ci --omit=dev`, the built `.next`, user `node`, `npm start -- --hostname 0.0.0.0 --port 3000`. `.env*` never enters the image. |
| `deploy/docker-compose.production.yml` | `postgres` (`pgvector/pgvector:pg16`, named volume, health check, **no published port**, network `db` which is `internal: true`), `backend` (env file, forced `ENVIRONMENT=production`, networks `db` + `edge` because CLI jobs need outbound HTTPS, no published port, never migrates on start), `frontend` (network `edge`), `caddy` (the only published ports: 80, 443, 443/udp); `restart: unless-stopped`, `init: true`, json-file log rotation (5 × 10 MB). |
| `deploy/Caddyfile` | site `{$APP_DOMAIN}`, automatic HTTPS; `/api/*`, `/health`, `/health/db` → `backend:8000` **without rewriting** (FastAPI already serves `/api/v1/*` and the health routes at the root); everything else → `frontend:3000`; HSTS, `nosniff`, `DENY` framing, referrer policy, no `Server` header. |
| `deploy/production.env.example` | placeholders for `APP_DOMAIN`, `NEXT_PUBLIC_API_BASE_URL`, `CORS_ORIGINS`, `POSTGRES_*`, `DATABASE_URL`, `ENVIRONMENT`; AI and contacts commented out. `deploy/*.env` is gitignored. |
| `deploy/bin/ii-compose` | `docker compose` with project `industry-intelligence`, `--env-file /etc/industry-intelligence/production.env` (override `II_ENV_FILE`, `II_PROJECT`); refuses with 78 when the env file is unreadable. |
| `deploy/bin/backup.sh` | daily `pg_dump -Fc` (see §9). |
| `deploy/bin/notify-failure.sh`, `deploy/hooks/*.example` | `OnFailure=` notification through an owner-installed hook (see §9). |
| `deploy/systemd/*` | update 10:00, backup 11:00, freshness 12:30 (all `Australia/Sydney`), and `industry-intelligence-notify@.service`. |
| `industry_intelligence.services.update_freshness`, CLI `update-freshness` | read-only freshness check over `update_runs` (see §9). |

**Same-origin API URL.** `lib/api.ts` joins `NEXT_PUBLIC_API_BASE_URL` with `/api/v1/…` and
refuses an empty value, so the production value is the full public origin
`https://<APP_DOMAIN>` (not a path); Caddy routes `/api/*` on that same origin to the backend.
**CORS:** browser requests are same-origin, so CORS is not needed for the site itself;
`CORS_ORIGINS` is set to the one exact origin `https://<APP_DOMAIN>` (verified: a foreign
`Origin` gets no `Access-Control-Allow-Origin`), never `*` (rejected in production anyway).

**Migrations** are one explicit operation, never part of container start:
`ii-compose run --rm backend alembic upgrade head`. Order for any schema change or restore:
backup → migration → `data-quality-audit` → start/restart the application.

**Verification (local, isolated, 2026-09-30).** Images built from the final Dockerfiles
(backend 368 MB, 33 packages, `pip check` clean; frontend 779 MB, 11 routes — most of the size is
`next` and its optional native compiler package, which npm installs with `next`); `docker compose
config` valid (only Caddy has ports; `db` network internal); `caddy validate` (Caddy 2.11.4)
valid and `caddy fmt` clean; `systemd-analyze verify` (systemd 257, Debian 13 container) clean for
all units; `systemd-analyze calendar` shows 10:00 local every day across both DST switches
(2026-10-03 00:00Z AEST → 2026-10-03 23:00Z AEDT; 2027-04-02 23:00Z AEDT → 2027-04-04 00:00Z AEST).
Isolated production-style stack (project `ii-d2a-smoke`, own volumes, ports 18080/18443,
`APP_DOMAIN=localhost` with Caddy's internal CA, trusted by `curl --cacert`): fresh database →
`alembic upgrade head` (12 revisions) → `vector` 0.8.6 / `pg_trgm` 1.6 → audit 0/0 →
`update-freshness` stale (exit 1, no runs) → `--plan --skip-ai` success → all four services
healthy; `/health`, `/health/db`, `/api/v1/*` 200 through Caddy; `/`, `/today`, `/events`,
`/rankings`, `/event/<id>` 200; `/docs`, `/redoc`, `/openapi.json` 404; HTTP → HTTPS 308; security
headers present; the bundle contains the configured origin; postgres and backend have no host
port bindings. Restored-data smoke: a `pg_dump -Fc` of the development database (1.35 MB) restored
into a fresh smoke volume — all 35 table counts identical, Alembic `20260930_12`, audit 0/0,
`source-health` 25/25 healthy, `update-freshness` fresh, `--plan` success; API reads through Caddy
(783 Events, Search, company rankings, Event page and `/ai`) 200. `docker compose run` returned the
container's exit code for 0, 1, 3, and 4. `backup.sh` against the smoke stack: exit 3 without a
hook, exit 0 with a hook (copy made), exit 1 with the database stopped (nothing kept); the dump
listed 35 table-data entries. Stack, volumes, dump, and temporary images were deleted; the
development database was only read (`pg_dump`).

## 9. Production operations

Scheduled jobs (systemd on the server; units in `deploy/systemd/`, installed as copies in
`/etc/systemd/system/`; the checkout is expected at `/opt/industry-intelligence`):

| Unit | When (Australia/Sydney) | Runs | Non-zero exit |
| --- | --- | --- | --- |
| `industry-intelligence-update` | 10:00, `Persistent=true` | `ii-compose run --rm -T backend python -m industry_intelligence.cli intelligence-update --skip-ai` | 1 failed, 3 degraded, 4 already running |
| `industry-intelligence-backup` | 11:00, `Persistent=true` | `deploy/bin/backup.sh` | 1 failed, 3 local only (no off-host copy) |
| `industry-intelligence-freshness` | 12:30, not persistent | `ii-compose run --rm -T backend python -m industry_intelligence.cli update-freshness` | 1 stale |

- **Exit codes are preserved**: `docker compose run` returns the container's exit code, and no
  unit sets `SuccessExitStatus`, so every non-zero exit leaves the unit failed with its real
  status (`systemctl status` shows `status=3/…`) and triggers `OnFailure=`.
- **No second lock**: the PostgreSQL advisory lock inside `intelligence-update` stays the only
  concurrency control; a manual run during the timer's run exits 4.
- **Time zone**: `OnCalendar=… Australia/Sydney` is evaluated by systemd in that zone whatever the
  server's own zone is; no UTC offset appears anywhere.

**Failure notification.** Each unit has `OnFailure=industry-intelligence-notify@%n.service`, which
runs `deploy/bin/notify-failure.sh <unit>`: it builds a subject (`[industry-intelligence] <unit>
failed on <host> (result=…, exit=…)`) and a body (exit-code legend and the unit's last 40 journal
lines from the past 3 h — the CLI output contains no secrets) and pipes it to
`/etc/industry-intelligence/hooks/notify` (`$1` = subject). The hook is provider-specific and
installed on the server only (`deploy/hooks/notify.example` shows email and webhook shapes; the
address, webhook URL, or token lives in a root-only file). Without a hook the notification unit
fails and logs `event=unsent reason=no_hook`, so the failure stays visible in `systemctl --failed`.

**Freshness check** (`update-freshness [--max-age-hours 26] [--json]`): SELECT-only in a read-only
transaction over `update_runs`; no network or provider request; exit 0 when a run with status
`success` started within the last 26 hours, else 1. Policy: only `success` counts — `degraded`
(exit 3) is not a successful update (its own exit already notified; one degraded day stays fresh
on the previous day's success), and `running`, `failed`, `interrupted` never count. It uses run
history, not scheduler logs, so it detects a job that never started, a broken timer, Docker or the
host being down, and repeated failures. 12:30 is more than 26 h after the previous day's 10:00
start, so a missed run is reported the same day (on the 23-hour day when daylight saving starts,
the next day).

**Backups** (`deploy/bin/backup.sh`): `pg_dump -Fc` through the postgres container's local socket
(no password handled or printed) into `daily/.<name>.partial` (umask 077), then header (`PGDMP`)
and size checks, then an atomic no-clobber publish as
`daily/industry-intelligence-YYYYMMDDTHHMMSSZ.dump`. The first dump of each UTC month is also
hard-linked into `monthly/`, so pruning `daily/` never deletes it. Retention: newest 14 daily and 6
monthly (`BACKUP_DAILY_KEEP`, `BACKUP_MONTHLY_KEEP`). Off-host copy: the new file is passed to
`/etc/industry-intelligence/hooks/backup-remote` (`BACKUP_REMOTE_HOOK`; examples for rclone/S3-
compatible storage and rsync in `deploy/hooks/backup-remote.example`); a missing or failing hook
exits 3 after the local dump is safely stored, so the gap is notified daily until a destination is
configured. Restore procedure: [restore.md](restore.md).

**Everyday commands** (server, as root):

```bash
alias ii=/opt/industry-intelligence/deploy/bin/ii-compose
ii ps; ii logs --tail 100 backend
systemctl list-timers 'industry-intelligence-*'
journalctl -u industry-intelligence-update.service --since today
ii run --rm -T backend python -m industry_intelligence.cli source-health
ii run --rm -T backend python -m industry_intelligence.cli update-freshness
ii run --rm -T backend python -m industry_intelligence.cli data-quality-audit
# Code update: new checkout → ii build → backup → (migration if any) → ii up -d
```

## 10. Deployment-2B — supervised cut-over

Not started. Runbook: [cutover.md](cutover.md) (20 steps: provision → Docker → firewall → env and
hooks → PostgreSQL → restore → Alembic → audit → start → HTTPS/health → plan → one supervised
`--skip-ai` run → `source-health` → Chinese/international source gate → timers → notification
test → first scheduled run → only then the owner decides about the Mac agent). Owner decisions
first: VPS provider, region, domain, notification channel, off-host backup destination, and
restore-the-development-database (recommended) vs. start empty.
