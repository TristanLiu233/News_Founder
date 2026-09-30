"""Local-1: the native PostgreSQL launchd service template and its cluster settings keep the
local runtime loopback-only, on its own port, and free of secrets."""

import plistlib
from pathlib import Path

POSTGRES_DIR = Path(__file__).resolve().parents[3] / "ops" / "postgres"
TEMPLATE = POSTGRES_DIR / "local.industry-intelligence.postgresql.plist.template"
CONF = POSTGRES_DIR / "postgresql.local.conf"


def test_plist_template_keeps_postgres_running_under_launchd():
    plist = plistlib.loads(TEMPLATE.read_bytes())

    assert plist["Label"] == "local.industry-intelligence.postgresql"
    assert plist["ProgramArguments"] == ["__PG_BIN__/postgres", "-D", "__PGDATA__"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == "__LOG_DIR__/postgresql.log"
    assert "EnvironmentVariables" not in plist


def test_cluster_settings_are_loopback_only_on_port_5433():
    settings = {}
    for line in CONF.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            key, value = (part.strip() for part in line.split("=", 1))
            settings[key] = value

    assert settings["listen_addresses"] == "'localhost'"
    assert settings["port"] == "5433"
    assert settings["timezone"] == "'Etc/UTC'"
    assert settings["password_encryption"] == "scram-sha-256"


def test_templates_hold_no_credentials():
    for path in (TEMPLATE, CONF):
        text = path.read_text().lower()
        assert "password=" not in text.replace(" ", "")
        assert "postgresql://" not in text and "postgresql+psycopg://" not in text
