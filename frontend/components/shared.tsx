"use client";

import type { ReactNode } from "react";

import type { Domain, NewsItem, Trend } from "../lib/api";
import { common, formatDateTime, type Locale } from "../lib/i18n";

export const DOMAINS: Domain[] = ["computer", "finance", "gaming"];

export function isDomain(value: string | null): value is Domain {
  return value === "computer" || value === "finance" || value === "gaming";
}

/** External source links always open the original in a new tab and say so. */
export function ExternalLink({ href, locale, children }: { href: string; locale: Locale; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}<span aria-hidden="true" className="external-mark"> ↗</span><span className="visually-hidden"> {common[locale].newTab}</span></a>;
}

export function TrendBadge({ trend, change, locale }: { trend: Trend | null; change?: number | null; locale: Locale }) {
  if (!trend) return null;
  const t = common[locale];
  const amount = Math.abs(change ?? 0);
  const symbol = trend === "up" ? `↑${amount}` : trend === "down" ? `↓${amount}` : trend === "new" ? t.newLabel : "–";
  return <span className={`trend trend-${trend}`}><span aria-hidden="true">{symbol}</span><span className="visually-hidden">{t.trend[trend]}{amount ? ` ${amount}` : ""}</span></span>;
}

/** Composite attention domain label; Finance composite rankings are Market Pulse only. */
export function trendingDomainLabel(domain: Domain, locale: Locale) {
  const t = common[locale];
  return domain === "finance" ? `${t.domains.finance} · ${t.marketPulse}` : t.domains[domain];
}

export function DomainTabs({ value, onChange, locale, label = trendingDomainLabel }: {
  value: Domain; onChange: (domain: Domain) => void; locale: Locale;
  label?: (domain: Domain, locale: Locale) => string;
}) {
  return <div className="domain-tabs" role="group" aria-label={common[locale].domainFilter}>
    {DOMAINS.map(domain => <button key={domain} type="button" aria-pressed={domain === value} onClick={() => onChange(domain)}>{label(domain, locale)}</button>)}
  </div>;
}

export function NewsList({ items, locale, showLanguage = false }: { items: NewsItem[]; locale: Locale; showLanguage?: boolean }) {
  const t = common[locale];
  return <ul className="ranking-list news-list">{items.map(item => <li key={item.id}><div>
    <ExternalLink href={item.url} locale={locale}>{item.title}</ExternalLink>
    <p>{item.source} · <time dateTime={item.published_at ?? item.observed_at}>{formatDateTime(item.published_at ?? item.observed_at, locale)}</time>{showLanguage && ` · ${t.languages[item.language] ?? item.language}`}</p>
  </div></li>)}</ul>;
}

export function PageState({ loading, error, locale }: { loading: boolean; error?: string; locale: Locale }) {
  if (error) return <p role="alert" className="notice error">{common[locale].unavailable} <small>{error}</small></p>;
  if (loading) return <p className="notice" aria-live="polite">{common[locale].loading}</p>;
  return null;
}
