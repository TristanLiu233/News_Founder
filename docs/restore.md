# Restore runbook

Status (2026-09-30, Deployment-2A): procedure written and rehearsed on an isolated local copy of
the production stack (a dump of the development database restored into a fresh `pgvector:pg16`
volume: all 35 table counts identical, `vector` 0.8.6 and `pg_trgm` 1.6 present, Alembic
`20260930_12`, `data-quality-audit` 0/0, `source-health` 25/25, `--plan` success). **No production
restore has been performed.** Every step here is manual and supervised.

Commands run on the server from the checkout (`/opt/industry-intelligence`) as root; `ii` below is
short for `deploy/bin/ii-compose` (docker compose with the root-only env file).

```bash
alias ii=/opt/industry-intelligence/deploy/bin/ii-compose
```

## 0. What a dump is

- `pg_dump -Fc` of the whole database (PostgreSQL is the only authoritative store: Subjects,
  Companies, content, Events, rank observations, search documents, AI artifacts, redirects,
  retired IDs, `update_runs`, `source_attempts`). About 1.4 MB today.
- Daily dumps: `/var/backups/industry-intelligence/daily/`, first dump of each UTC month also in
  `monthly/` (hard link), plus the off-host copy made by the backup hook.
- A dump starts with the 5 bytes `PGDMP`: `head -c 5 FILE` must print `PGDMP`.
- `pg_restore` reads the dump **from a file inside the postgres container** (copied with
  `docker compose cp`), never from a pipe.

## 1. Stop writers

```bash
systemctl stop industry-intelligence-update.timer industry-intelligence-backup.timer \
    industry-intelligence-freshness.timer
systemctl is-active industry-intelligence-update.service   # must not be "activating"
ii stop backend frontend caddy                             # the site is down from here
```

## 2. Keep the current state

If the database holds data you might need, dump it first (even when restoring over it):

```bash
BACKUP_REMOTE_HOOK=/nonexistent /opt/industry-intelligence/deploy/bin/backup.sh   # exit 3 = local only, expected
```

## 3. Prepare an empty PostgreSQL

Fresh server or new volume (cut-over): just start the database. **Do not run
`alembic upgrade head` first** — the dump brings its own schema and `alembic_version`.

```bash
ii up -d --wait postgres
```

Replacing an existing database (disaster recovery): recreate it empty.

```bash
ii exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" \
    && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

## 4. Restore

```bash
DUMP=/var/backups/industry-intelligence/daily/industry-intelligence-YYYYMMDDTHHMMSSZ.dump
head -c 5 "$DUMP"; echo                                   # PGDMP
ii cp "$DUMP" postgres:/tmp/restore.dump
ii exec -T postgres sh -c 'pg_restore --no-owner --no-privileges --exit-on-error \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/restore.dump && rm /tmp/restore.dump'
```

`--no-owner --no-privileges` makes the production role own everything, whatever role made the
dump. `--exit-on-error` stops at the first error; on failure, recreate the database (step 3) and
investigate before retrying.

## 5. Verify

```bash
# Extensions
ii exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "select extname, extversion from pg_extension order by 1"'      # pg_trgm, plpgsql, vector

# Alembic: current must equal the single head of the deployed code (today 20260930_12)
ii run --rm -T backend alembic current
ii run --rm -T backend alembic heads

# Row counts (compare with the source database when you have it)
ii exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' <<'SQL'
select string_agg(format('select %L||'' ''||count(*) from %I', tablename, tablename),
                  ' union all ' order by tablename)
from pg_tables where schemaname = 'public' \gexec
SQL

# Invariants, source history, freshness, and a read-only update plan (no network, no write)
ii run --rm -T backend python -m industry_intelligence.cli data-quality-audit          # 0 errors
ii run --rm -T backend python -m industry_intelligence.cli source-health
ii run --rm -T backend python -m industry_intelligence.cli update-freshness            # informational
ii run --rm -T backend python -m industry_intelligence.cli intelligence-update --plan --skip-ai
```

If `alembic current` is **older** than the code's head (restoring an old dump into newer code):
keep the dump from step 2, then run the explicit migration `ii run --rm -T backend alembic upgrade
head` and repeat the checks. If it is **newer**, the deployed code is too old: deploy the matching
commit instead. Never edit `alembic_version` by hand.

An error-level `data-quality-audit` finding blocks the restart: investigate first (the daily
update's precheck would refuse to run anyway).

## 6. Restart

```bash
ii up -d --wait
curl -fsS https://$APP_DOMAIN/health && curl -fsS https://$APP_DOMAIN/health/db
systemctl start industry-intelligence-update.timer industry-intelligence-backup.timer \
    industry-intelligence-freshness.timer
systemctl list-timers 'industry-intelligence-*'
```

## 7. Monthly restore test

Once a month and after every schema change, restore the newest dump into a throwaway database on
the same server and check it, without touching production:

```bash
ii cp "$DUMP" postgres:/tmp/check.dump
ii exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" ii_restore_check \
    && pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" \
       -d ii_restore_check /tmp/check.dump \
    && psql -U "$POSTGRES_USER" -d ii_restore_check -Atc "select version_num from alembic_version" \
    ; dropdb -U "$POSTGRES_USER" --if-exists ii_restore_check; rm -f /tmp/check.dump'
```
