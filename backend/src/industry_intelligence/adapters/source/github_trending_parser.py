"""Pure HTML parsing independent of HTTP, clocks and persistence."""

import re

from bs4 import BeautifulSoup


def parse_github_trending(html: str):
    soup = BeautifulSoup(html, "html.parser")
    entries = soup.select("article.Box-row")
    if not entries and not (
        soup.select_one("h1") and soup.select_one("h1").get_text(strip=True) == "Trending"
    ):
        raise ValueError("Unexpected GitHub Trending structure")
    output, failed = [], 0
    for rank, entry in enumerate(entries, 1):
        link = entry.select_one("h2 a")
        path = link.get("href", "") if link else ""
        if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
            failed += 1
            continue
        identity = path.strip("/")
        language = entry.select_one('[itemprop="programmingLanguage"]')
        description = entry.select_one("p")
        stars = entry.select_one('a[href$="/stargazers"]')
        today = re.search(r"([\d,]+) stars? today", entry.get_text(" ", strip=True))
        output.append(
            dict(
                external_id=identity.casefold(),
                title=identity,
                url=f"https://github.com/{identity}",
                rank=rank,
                raw_metric=int(today[1].replace(",", "")) if today else None,
                metadata=dict(
                    owner=identity.split("/")[0],
                    repository=identity.split("/")[1],
                    language=language.get_text(strip=True) if language else None,
                    description=(description.get_text(" ", strip=True) if description else None),
                    stars=stars.get_text(strip=True) if stars else None,
                    metric_label="stars today",
                ),
            )
        )
    if entries and not output:
        raise ValueError("No valid GitHub entries")
    return output, failed
