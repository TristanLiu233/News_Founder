"""Deployment-2A templates: the production compose file, Caddy routing, systemd units, Dockerfiles,
and the env template keep the operational contract (daily --skip-ai update at 10:00
Australia/Sydney, real exit codes, private database, explicit migrations, no secrets)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
SYSTEMD = DEPLOY / "systemd"
CHECKOUT = "/opt/industry-intelligence"


def unit(name):
    """Parse a systemd unit into {section: {key: [values]}} (keys may repeat)."""

    sections, current = {}, None
    for raw in (SYSTEMD / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
            continue
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((DEPLOY / "docker-compose.production.yml").read_text())


@pytest.mark.parametrize(
    ("timer", "calendar", "persistent"),
    [
        ("industry-intelligence-update.timer", "*-*-* 10:00:00 Australia/Sydney", "true"),
        ("industry-intelligence-backup.timer", "*-*-* 11:00:00 Australia/Sydney", "true"),
        ("industry-intelligence-freshness.timer", "*-*-* 12:30:00 Australia/Sydney", "false"),
    ],
)
def test_timers_use_the_sydney_zone_not_a_utc_offset(timer, calendar, persistent):
    timer_unit = unit(timer)
    assert timer_unit["Timer"]["OnCalendar"] == [calendar]
    assert timer_unit["Timer"]["Persistent"] == [persistent]
    assert timer_unit["Install"]["WantedBy"] == ["timers.target"]
    assert (SYSTEMD / timer.replace(".timer", ".service")).exists()


def test_daily_update_runs_exactly_skip_ai_and_keeps_its_exit_code():
    service = unit("industry-intelligence-update.service")
    [command] = service["Service"]["ExecStart"]
    assert command == (
        f"{CHECKOUT}/deploy/bin/ii-compose run --rm -T backend "
        "python -m industry_intelligence.cli intelligence-update --skip-ai"
    )
    assert "--ai-limit" not in command
    assert service["Service"]["Type"] == ["oneshot"]
    assert "SuccessExitStatus" not in service["Service"]
    assert service["Unit"]["OnFailure"] == ["industry-intelligence-notify@%n.service"]


@pytest.mark.parametrize(
    ("name", "command"),
    [
        ("industry-intelligence-backup.service", f"{CHECKOUT}/deploy/bin/backup.sh"),
        (
            "industry-intelligence-freshness.service",
            f"{CHECKOUT}/deploy/bin/ii-compose run --rm -T backend "
            "python -m industry_intelligence.cli update-freshness",
        ),
        ("industry-intelligence-notify@.service", f"{CHECKOUT}/deploy/bin/notify-failure.sh %i"),
    ],
)
def test_operational_units_call_repository_scripts(name, command):
    service = unit(name)
    assert service["Service"]["ExecStart"] == [command]
    assert "SuccessExitStatus" not in service["Service"]
    script = command.split()[0].removeprefix(f"{CHECKOUT}/")
    assert (ROOT / script).stat().st_mode & 0o111, f"{script} is not executable"
    if "notify@" not in name:
        assert service["Unit"]["OnFailure"] == ["industry-intelligence-notify@%n.service"]


def test_only_caddy_publishes_ports_and_the_database_is_private(compose):
    services = compose["services"]
    assert set(services) == {"postgres", "backend", "frontend", "caddy"}
    assert [name for name, service in services.items() if "ports" in service] == ["caddy"]
    targets = [re.sub(r"\$\{[^}]*\}", "P", p).split(":")[-1] for p in services["caddy"]["ports"]]
    assert sorted(targets) == ["443", "443/udp", "80"]
    assert services["postgres"]["networks"] == ["db"]
    assert compose["networks"]["db"]["internal"] is True
    assert "edge" in services["backend"]["networks"]  # CLI jobs need outbound HTTPS
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert all(service["restart"] == "unless-stopped" for service in services.values())


def test_backend_is_production_and_never_migrates_on_start(compose):
    backend = compose["services"]["backend"]
    assert backend["environment"]["ENVIRONMENT"] == "production"
    assert "command" not in backend and "entrypoint" not in backend
    assert backend["depends_on"]["postgres"]["condition"] == "service_healthy"
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    cmd = dockerfile.split("\nCMD ", 1)[1]
    assert "alembic" not in cmd and "--reload" not in cmd
    assert '"--workers", "2"' in cmd and "industry_intelligence.main:app" in cmd
    assert "constraints.txt" in dockerfile and "\nUSER app\n" in dockerfile


def test_frontend_is_built_for_the_public_origin_and_runs_npm_start(compose):
    frontend = compose["services"]["frontend"]
    assert "NEXT_PUBLIC_API_BASE_URL" in frontend["build"]["args"]
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text()
    assert "FROM node:24" in dockerfile and "npm ci" in dockerfile
    assert 'CMD ["npm", "start"' in dockerfile and "\nUSER node\n" in dockerfile


def test_caddy_routes_backend_paths_without_rewriting():
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    assert "{$APP_DOMAIN} {" in caddyfile
    assert "@backend path /api/* /health /health/db" in caddyfile
    assert "reverse_proxy backend:8000" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile
    assert "uri strip_prefix" not in caddyfile and "rewrite" not in caddyfile
    assert "postgres" not in caddyfile


def test_env_template_holds_placeholders_only():
    values = dict(
        line.split("=", 1)
        for line in (DEPLOY / "production.env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["APP_DOMAIN"] == "your-domain.invalid"
    assert (
        values["NEXT_PUBLIC_API_BASE_URL"]
        == values["CORS_ORIGINS"]
        == "https://your-domain.invalid"
    )
    assert values["POSTGRES_PASSWORD"] == "change-me"
    assert "change-me" in values["DATABASE_URL"] and "@postgres:5432/" in values["DATABASE_URL"]
    assert values["ENVIRONMENT"] == "production"
    assert not any(key.startswith("AI_") for key in values)  # AI stays optional and unset
    assert not (DEPLOY / "production.env").exists()
