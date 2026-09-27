"""Central logging configuration."""

from __future__ import annotations

import logging.config
from typing import Any

from industry_intelligence.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure concise console logs without ever serializing application settings."""

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
            }
        },
        "root": {"handlers": ["console"], "level": settings.log_level},
    }
    logging.config.dictConfig(config)
