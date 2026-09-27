"""Small, stable vocabularies used by the Phase 0 domain model."""

from enum import StrEnum


class Domain(StrEnum):
    COMPUTER = "computer"
    FINANCE = "finance"
    GAMING = "gaming"


class SubjectType(StrEnum):
    EVENT = "event"
    COMPANY = "company"
    STOCK = "stock"
    GAME = "game"
    REPOSITORY = "repository"
    PAPER = "paper"
    MODEL = "model"
    TECHNOLOGY = "technology"


class ContentType(StrEnum):
    RANKING = "ranking"
    FEED = "feed"
    NEWS = "news"
    MARKET_PULSE = "market_pulse"
