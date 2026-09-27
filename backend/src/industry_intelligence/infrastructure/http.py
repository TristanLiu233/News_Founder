"""Shared bounded HTTP transport; parsers and 4xx failures are never retried.

Maintainer contacts are scoped per publisher. A client opts in to a contact, and the contact is
then attached only to requests whose host belongs to that publisher, re-checked on every request
including redirects: ``contact`` (SOURCE_CONTACT) reaches Wikimedia hosts only and
``sec_contact`` (SEC_CONTACT) reaches SEC hosts only. Every other host gets no contact.
"""

import asyncio

import httpx

from industry_intelligence.config import Settings

WIKIMEDIA_DOMAINS = ("wikipedia.org", "wikimedia.org", "wikidata.org")
SEC_DOMAINS = ("sec.gov",)
# SEC's fair-access policy asks for a declared "Product/Version contact" User-Agent.
SEC_PRODUCT = "IndustryIntelligenceRankingPlatform/0.1"


def host_in(host: str | None, domains: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def user_agent(contact: str | None = None) -> str:
    """Honest client identification; a contact is appended only when a caller passes one."""

    details = "personal research MVP" + (f"; {contact}" if contact else "")
    return f"IndustryIntelligence/0.1 ({details})"


def sec_user_agent(contact: str) -> str:
    return f"{SEC_PRODUCT} {contact}"


class HTTPClient:
    def __init__(
        self,
        settings: Settings,
        transport=None,
        contact: str | None = None,
        sec_contact: str | None = None,
    ):
        self.contact = contact
        self.sec_contact = sec_contact
        self.client = httpx.AsyncClient(
            timeout=settings.source_http_timeout,
            headers={"User-Agent": user_agent()},
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [self._identify]},
        )

    async def _identify(self, request: httpx.Request) -> None:
        host = request.url.host
        if self.sec_contact and host_in(host, SEC_DOMAINS):
            agent = sec_user_agent(self.sec_contact)
        elif self.contact and host_in(host, WIKIMEDIA_DOMAINS):
            agent = user_agent(self.contact)
        else:
            agent = user_agent()
        request.headers["User-Agent"] = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def get(self, url, **kwargs):
        for attempt in range(3):
            try:
                response = await self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
                )
                if not retryable or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * 2**attempt)
        raise AssertionError("Unreachable")
