"""Reviewed company-profile targets: which source records describe which ranked company.

Reviewed on 2026-09-27 against live sources. Each target is located by the Phase 4 identity
``companiesmarketcap:symbol``. Its source identifiers were checked one by one:

* ``cik``: the exact ticker in SEC's company_tickers.json, registrant name checked;
* ``lei``: the GLEIF head-office record (category GENERAL, ACTIVE, exact legal name), never a
  branch, fund, or subsidiary returned by full-text search;
* ``website``: the company's own site, fetched successfully by the application client (sites that
  refused it — abc.xyz 403, tsmc.com 403, ccb.com connection failure — are left out, not retried);
* ``cmc_slug``: the CompaniesMarketCap page linked from the ranking row with that symbol;
* ``localized_names``: (locale, name, official page); ingestion accepts a name only if it appears
  on that page. Names are never machine-translated (Apple, for example, has none: its Chinese
  site uses "Apple").

Adding a target is a reviewed change with the same checks. Targets are representative Phase 4
ranking leaders across the three domains, not the full company list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileTarget:
    symbol: str
    cik: str | None = None
    lei: str | None = None
    website: str | None = None
    cmc_slug: str | None = None
    localized_names: tuple[tuple[str, str, str], ...] = ()


PROFILE_TARGETS: tuple[ProfileTarget, ...] = (
    # Computer — Largest tech companies by market cap
    ProfileTarget(
        "NVDA",
        cik="0001045810",
        lei="549300S4KLFTLO7GSQ80",
        website="https://www.nvidia.com/",
        cmc_slug="nvidia",
        localized_names=(("zh-CN", "英伟达", "https://www.nvidia.cn/"),),
    ),
    ProfileTarget(
        "AAPL",
        cik="0000320193",
        lei="HWUPKR0MPOU8FGXBT394",
        website="https://www.apple.com/",
        cmc_slug="apple",
    ),
    ProfileTarget("GOOG", cik="0001652044", lei="5493006MHB84DD0ZWV18", cmc_slug="alphabet-google"),
    ProfileTarget(
        "MSFT",
        cik="0000789019",
        lei="INR2EJN1ERAN0W5ZP974",
        website="https://www.microsoft.com/",
        cmc_slug="microsoft",
        localized_names=(("zh-CN", "微软", "https://www.microsoft.com/zh-cn/"),),
    ),
    ProfileTarget("TSM", cik="0001046179", lei="549300KB6NK5SBD14S87", cmc_slug="tsmc"),
    # Finance — Largest banks and bank holding companies by market cap
    ProfileTarget(
        "JPM",
        cik="0000019617",
        lei="8I5DZWZKVSZI1NUHU748",
        website="https://www.jpmorganchase.com/",
        cmc_slug="jp-morgan-chase",
        localized_names=(("zh-CN", "摩根大通", "https://www.jpmorgan.com/CN/zh/about-us"),),
    ),
    ProfileTarget("601939.SS", lei="5493001KQW6DM7KEDR62", cmc_slug="china-construction-bank"),
    ProfileTarget(
        "BAC",
        cik="0000070858",
        lei="9DJT3UXIJIZJI4WXO774",
        website="https://about.bankofamerica.com/",
        cmc_slug="bank-of-america",
    ),
    ProfileTarget(
        "HSBC",
        cik="0001089113",
        lei="MLU0ZO3ML4LN2LL2TL39",
        website="https://www.hsbc.com/",
        cmc_slug="hsbc",
        localized_names=(("zh-CN", "汇丰", "https://www.hsbc.com.cn/"),),
    ),
    ProfileTarget(
        "1398.HK",
        lei="5493002ERZU2K9PZDL40",
        website="https://www.icbc.com.cn/",
        cmc_slug="icbc",
        localized_names=(("zh-CN", "中国工商银行", "https://www.icbc.com.cn/"),),
    ),
    # Gaming — Largest video game companies by market cap (Microsoft is listed above)
    ProfileTarget(
        "TCEHY",
        lei="254900N4SLUMW4XUYY11",
        website="https://www.tencent.com/",
        cmc_slug="tencent",
        localized_names=(("zh-CN", "腾讯", "https://www.tencent.com/zh-cn/"),),
    ),
    ProfileTarget(
        "SONY",
        cik="0000313838",
        lei="529900R5WX9N2OI2N910",
        website="https://www.sony.com/",
        cmc_slug="sony",
        localized_names=(("zh-CN", "索尼", "https://www.sony.com.cn/"),),
    ),
    ProfileTarget(
        "NTES",
        cik="0001110646",
        lei="5299004AF4DSJDB0PA32",
        website="https://www.netease.com/",
        cmc_slug="netease",
        localized_names=(("zh-CN", "网易", "https://www.netease.com/"),),
    ),
    ProfileTarget(
        "7974.T",
        lei="353800FEEXU6I9M0ZF27",
        website="https://www.nintendo.co.jp/",
        cmc_slug="nintendo",
        localized_names=(("zh-CN", "任天堂", "https://www.nintendo.com.hk/"),),
    ),
    ProfileTarget(
        "TTWO",
        cik="0000946581",
        lei="BOMSTHHJK882EWYX3334",
        website="https://www.take2games.com/",
        cmc_slug="take-2-interactive",
    ),
)

TARGETS_BY_SYMBOL = {target.symbol: target for target in PROFILE_TARGETS}
