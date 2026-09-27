"""The company's own official website: website URL, self-description, and verified local names.

Each target's website and Chinese-language page are reviewed URLs on the company's own domains.
robots.txt is honoured (RFC 9309: a 4xx robots file allows access, a 5xx or unreachable one
disallows it). The homepage's published self-description (``<meta name="description">``,
Open Graph, or schema.org JSON-LD) is kept as a short, attributed excerpt in its own language;
it is the company's description of itself, not a platform assessment. A reviewed localized
name is accepted only if it literally appears on the official page it cites.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from industry_intelligence.adapters.company_profile.base import (
    ExternalCompanyProfile,
    LocalizedName,
)
from industry_intelligence.adapters.company_profile.text import clean_text, detect_language

ROBOTS_AGENT = "IndustryIntelligence"
DESCRIPTION_LIMIT = 400
ORGANIZATION_TYPES = {"Organization", "Corporation"}


class RobotsDisallowed(PermissionError):
    """robots.txt disallows the page (or could not be read because of a server error)."""


def _meta(soup, attribute, value):
    tag = soup.find("meta", attrs={attribute: lambda v: v and v.lower() == value})
    return tag.get("content") if tag else None


def _json_ld_description(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            kinds = item.get("@type") if isinstance(item, dict) else None
            kinds = {kinds} if isinstance(kinds, str) else set(kinds or ())
            if kinds & ORGANIZATION_TYPES and item.get("description"):
                return item["description"]
    return None


def parse_homepage(text):
    """Return (description, description_field, language) from the official homepage."""

    soup = BeautifulSoup(text, "html.parser")
    candidates = (
        ("meta:description", _meta(soup, "name", "description")),
        ("meta:og:description", _meta(soup, "property", "og:description")),
        ("json-ld:description", _json_ld_description(soup)),
    )
    for field, value in candidates:
        description = clean_text(value, DESCRIPTION_LIMIT)
        if description:
            declared = soup.html.get("lang") if soup.html else None
            return description, field, detect_language(description, declared)
    return None, None, None


def page_contains(text, name):
    """True when ``name`` appears in the page's title, meta content, or visible text."""

    soup = BeautifulSoup(text, "html.parser")
    for noise in soup(["script", "style", "noscript"]):
        noise.decompose()
    meta = " ".join(tag.get("content") or "" for tag in soup.find_all("meta"))
    return name in " ".join((soup.get_text(" "), meta))


class OfficialWebsiteProfileAdapter:
    def __init__(self, http, definition, now=None):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.failed_count = 0
        self._robots = {}

    async def _allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                response = await self.http.get(f"{origin}/robots.txt")
                parser.parse(response.text.splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    parser.disallow_all = True
                else:
                    parser.allow_all = True
            except httpx.TransportError:
                parser.disallow_all = True
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(ROBOTS_AGENT, url)

    async def _page(self, url):
        if not await self._allowed(url):
            raise RobotsDisallowed(url)
        return await self.http.get(url)

    async def fetch(self, target) -> ExternalCompanyProfile:
        retrieved_at = self.now()
        response = await self._page(target.website)
        description, field, language = parse_homepage(response.text)
        facts = {"website": target.website}
        if description:
            facts.update(description=description, description_language=language)
        names, verified, unverified = [], [], []
        for locale, name, url in target.localized_names:
            try:
                page = await self._page(url)
                found = page_contains(page.text, name)
            except (httpx.HTTPError, RobotsDisallowed) as exc:
                found, page = False, None
                unverified.append({"name": name, "url": url, "reason": type(exc).__name__})
            if found:
                names.append(LocalizedName(name, locale, "localized_name"))
                verified.append({"name": name, "locale": locale, "url": str(page.url)})
            else:
                self.failed_count += 1
                if page is not None:
                    unverified.append({"name": name, "url": url, "reason": "not_found"})
        return ExternalCompanyProfile(
            source_key=self.definition.key,
            source_url=target.website,
            retrieved_at=retrieved_at,
            facts=facts,
            names=tuple(names),
            metadata={
                "final_url": str(response.url),
                "description_field": field,
                "verified_names": verified,
                "unverified_names": unverified,
            },
        )
