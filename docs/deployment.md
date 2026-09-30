# Deployment

Status (2026-09-30): **Deployment-1 — Production Readiness Audit complete.** Nothing is deployed.
The application runs as a valid *local operation* on the development Mac (daily launchd update at
10:00 Australia/Sydney). This document records the audited runtime, the production requirements,
the chosen production architecture, and the exact scope of the next phase,
**Deployment-2 — Production Packaging & Infrastructure**.

## 1. Current runtime (development Mac)

| Component | How it starts | What keeps it alive | State it owns | After reboot | Developer-machine dependency |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL 16.15 + pgvector 0.8.6 + pg_trgm | `docker compose up -d` (`pgvector/pgvector:pg16`, container `app-db-1`) | Docker Desktop; container restart policy `no` | everything (named volume `app_postgres_data`) | down until Docker Desktop is opened and the container started (Docker Desktop is not a login item) | Docker Desktop, logged-in user |
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
| Docker Desktop started by hand; container restart policy `no` | production blocker if kept → replaced by a server runtime |
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
```

- **Same-origin reverse proxy**: the frontend is built with `NEXT_PUBLIC_API_BASE_URL=https://<domain>`;
  browser API calls stay on one origin, the backend is never directly public, and
  `CORS_ORIGINS=https://<domain>`.
- **Secrets**: `/etc/industry-intelligence/.env` (root, mode 600) referenced by compose; nothing
  in the repository or images.
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

| Failure | Detected today by | Deployment-2 must add |
| --- | --- | --- |
| frontend / backend / database down | `/health`, `/health/db` if someone looks | an external uptime check on `https://<domain>/` and `/health/db` |
| daily update exit 1 / 3 / 4 | exit code in the log / journal | systemd `OnFailure=` notification for any non-zero exit |
| job never ran (host down, timer broken) | absence of an `update_runs` row | a daily freshness check (e.g. 12:00 Sydney) that fails when no successful run started in the last 26 h, wired to the same notification |
| source failing / degraded | `source-health` | read after each notification; nice-to-have: include its summary in the notification |
| data-quality error | pre/postcheck fail the run (exit 1) | covered by the exit-code notification |
| backup failed | — | backup timer with the same `OnFailure=` notification |

Nice-to-have later: security headers and basic rate limiting at the proxy, log shipping. No
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

## 8. Deployment-2 — Production Packaging & Infrastructure (scope)

1. `backend/Dockerfile` (Python 3.12 slim, source layout + editable install with
   `constraints.txt`, non-root user, uvicorn 2 workers, no `--reload`).
2. `frontend/Dockerfile` (Node 24 LTS, `npm ci`, build arg `NEXT_PUBLIC_API_BASE_URL`, `npm start`).
3. `deploy/compose.production.yml`: db (pgvector:pg16, volume, health check, no published port),
   backend, frontend, caddy (only published service), `restart: unless-stopped`, env file outside
   the repository.
4. `deploy/Caddyfile`: same-origin routing (`/api/*`, `/health*` → backend; rest → frontend).
5. systemd units + timers (checked into `deploy/`): daily update 10:00 Australia/Sydney
   (`--skip-ai`, `Persistent=true`), backup 11:00, freshness check 12:00, shared `OnFailure=`
   notification.
6. Backup script with retention and off-host copy; restore runbook.
7. Owner decisions before provisioning: VPS provider and region (Chinese publishers must be
   reachable from it), domain name, notification channel, off-host backup target, and whether
   to migrate the current database (recommended: `pg_dump`/`pg_restore`, verified above) or
   start empty.
8. Cut-over: restore → `alembic current` → `data-quality-audit` → `--plan` → one supervised
   `intelligence-update --skip-ai` → `source-health` (first proof that every source works from
   the server's IP) → enable the timers → disable the Mac LaunchAgent (the owner decides when).
