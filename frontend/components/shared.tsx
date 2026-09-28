"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { fetchEvents, type Domain, type EventSummary, type EventsPage, type NewsItem, type Trend } from "../lib/api";
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
  /** Undefined when no domain is selected (an "All" control is pressed instead). */
  value?: Domain; onChange: (domain: Domain) => void; locale: Locale;
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

/** Canonical Events as compact rows: title, latest-report time (the list order), coverage, type, and a few entities. */
export function EventList({ items, locale, showDomain = false }: { items: EventSummary[]; locale: Locale; showDomain?: boolean }) {
  const t = common[locale];
  return <ul className="event-list">{items.map(event => <li key={event.id}>
    <Link className="event-title" href={`/event/${event.id}`} lang={event.title_language}>{event.canonical_title}</Link>
    <p className="meta">
      <span>{t.lastReported} <time dateTime={event.time.last_published_at}>{formatDateTime(event.time.last_published_at, locale)}</time></span>
      {showDomain && <span>{t.shortDomains[event.domain]}</span>}
      <span>{t.reports(event.content_count)}</span>
      <span>{t.sources(event.source_count)}</span>
      {event.event_type !== "general" && <span className="tag">{t.eventTypes[event.event_type] ?? event.event_type}</span>}
    </p>
    <p className="event-sources">{event.sources.map(source => source.name).join(" · ")}{event.source_count > event.sources.length && " …"}</p>
    {(event.subjects.length > 0 || event.companies.length > 0) && <ul className="chips event-entities">
      {event.subjects.map(subject => <li key={`s-${subject.id}`}><Link href={`/subject/${subject.id}`}>{subject.display_names[locale]}</Link> <small>{t.subjectTag}</small></li>)}
      {event.companies.map(company => <li key={`c-${company.id}`}><Link href={`/company/${company.id}`}>{company.display_names[locale]}</Link> <small>{t.companyTag}</small></li>)}
    </ul>}
  </li>)}</ul>;
}

const RELATED_EVENTS = 5;

/** Up to five recent Events related to one Subject or Company through deterministic links. */
export function RelatedEvents({ filter, locale }: { filter: {subject_id: string} | {company_id: string}; locale: Locale }) {
  const [page, setPage] = useState<EventsPage>();
  const [failed, setFailed] = useState(false);
  const key = "subject_id" in filter ? `subject_id=${filter.subject_id}` : `company_id=${filter.company_id}`;
  useEffect(() => {
    const controller = new AbortController();
    setPage(undefined); setFailed(false);
    const [name, value] = key.split("=");
    fetchEvents({[name]: value, limit: RELATED_EVENTS}, controller.signal)
      .then(setPage)
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [key]);
  const t = common[locale];
  return <section className="detail-section" aria-labelledby="related-events"><h2 id="related-events" className="section-title">{t.relatedEvents}{page && page.total > 0 && ` (${page.total})`}</h2>
    <article className="ranking-card">
      {failed && <p className="empty">{t.unavailable}</p>}
      {!page && !failed && <p className="empty">{t.loading}</p>}
      {page?.items.length === 0 && <p className="empty">{t.noRelatedEvents}</p>}
      {page && page.items.length > 0 && <><p className="section-note">{t.relatedEventsNote}</p><EventList items={page.items} locale={locale}/></>}
      {page && page.total > page.items.length && <Link href={`/events?${key}`}>{t.viewAllEvents(page.total)} →</Link>}
    </article>
  </section>;
}
