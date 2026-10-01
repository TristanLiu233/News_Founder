"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { fetchEvents, type Domain, type EventSummary, type EventsPage, type ExternalRanking, type NewsItem, type Trend, type TrendingEventItem } from "../lib/api";
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

/**
 * One Trending Event (Trending and Home): headline → Event Detail, then every publisher's own rank
 * for its reports exactly as published (the evidence for why it is here), time, coverage, and Topics
 * as context. There is deliberately no platform position number or score. `showSummary` (Home) adds the
 * persisted summary, labelled as AI-generated or as a publisher excerpt; a card without one simply has
 * no summary. `showMovement` (the publisher-list view only) adds same-list movement badges.
 */
export function TrendingEventCard({ item, locale, compact = false, showSummary = false, showMovement = false }: { item: TrendingEventItem; locale: Locale; compact?: boolean; showSummary?: boolean; showMovement?: boolean }) {
  const t = common[locale];
  const { event, summary } = item;
  return <li className="trending-event">
    <Link className="event-title" href={`/event/${event.id}`} lang={event.title_language}>{event.canonical_title}</Link>
    {showSummary && summary && <p className="event-summary">
      <span className="summary-tag">{summary.kind === "ai_summary" ? t.aiSummaryTag : t.excerptTag(summary.source)}</span>{" "}
      <span lang={summary.kind === "ai_summary" ? summary.locale : summary.language}>{summary.text}</span>
    </p>}
    <ul className="rank-evidence" aria-label={t.publisherRanks}>
      {item.external_rankings.map(ranking => <li key={`${ranking.source_key}:${ranking.content_item_id}`}>
        <span>{ranking.source} · {ranking.list_name}</span> <strong className="native-rank"><span className="visually-hidden">{t.rankWord} </span>#{ranking.rank}</strong>
        {showMovement && <MovementBadge ranking={ranking} locale={locale}/>}
      </li>)}
    </ul>
    {item.native_list_count > 1 && <p className="multi-list">{t.onLists(item.native_list_count, item.native_publisher_count)}</p>}
    <p className="meta">
      <span>{t.lastReported} <time dateTime={event.time.last_published_at}>{formatDateTime(event.time.last_published_at, locale)}</time></span>
      <Coverage event={event} locale={locale}/>
    </p>
    {!compact && item.topics.length > 0 && <ul className="chips event-entities" aria-label={t.relatedTopics}>
      {item.topics.map(topic => <li key={topic.id}><Link href={`/subject/${topic.id}`}>{topic.display_names[locale]}</Link> <small>{t.subjectTag}</small></li>)}
    </ul>}
  </li>;
}

/**
 * Trending-2: the change of one article's rank within one publisher list since that list's previous
 * stored snapshot (↑5, ↓2, —, NEW). Nothing is shown when no comparable previous snapshot exists.
 * A same-list fact, never a score.
 */
function MovementBadge({ ranking, locale }: { ranking: ExternalRanking; locale: Locale }) {
  const t = common[locale];
  const { movement, rank_delta: delta } = ranking;
  if (!movement) return null;
  const [text, label] = movement === "up" && delta !== null ? [`↑${delta}`, t.movement.up(delta)]
    : movement === "down" && delta !== null ? [`↓${-delta}`, t.movement.down(-delta)]
    : movement === "stable" ? ["—", t.movement.stable]
    : ranking.reentered ? [t.backBadge, t.movement.reentered] : [t.newBadge, t.movement.new];
  return <span className={`rank-move ${movement}`} title={label}><span aria-hidden="true">{text}</span><span className="visually-hidden">{label}</span></span>;
}

/** A publisher-stated list window, e.g. `day:2026-09-30`; null when the publisher states none. */
export function listWindowLabel(window: string | null, locale: Locale) {
  if (!window) return null;
  const [kind, value] = window.split(":");
  return kind === "day" && value ? common[locale].dailyList(value) : window;
}

/** Composite attention domain label; Finance composite rankings are Market Pulse only. */
export function trendingDomainLabel(domain: Domain, locale: Locale) {
  const t = common[locale];
  return domain === "finance" ? `${t.domains.finance} · ${t.marketPulse}` : t.domains[domain];
}

/** Short domain labels; the machine values stay `computer`, `finance`, `gaming`. */
export function shortDomainLabel(domain: Domain, locale: Locale) {
  return common[locale].shortDomains[domain];
}

export function DomainTabs({ value, onChange, locale, label = shortDomainLabel, onAll }: {
  /** Undefined when no domain is selected (an "All" control is pressed instead). */
  value?: Domain; onChange: (domain: Domain) => void; locale: Locale;
  label?: (domain: Domain, locale: Locale) => string;
  /** When given, an "All" button comes first and is pressed while `value` is undefined. */
  onAll?: () => void;
}) {
  return <div className="domain-tabs" role="group" aria-label={common[locale].domainFilter}>
    {onAll && <button type="button" aria-pressed={value === undefined} onClick={onAll}>{common[locale].all}</button>}
    {DOMAINS.map(domain => <button key={domain} type="button" aria-pressed={domain === value} onClick={() => onChange(domain)}>{label(domain, locale)}</button>)}
  </div>;
}

export type DiscoverSection = "today" | "trending" | "search";
const DISCOVER_SECTIONS: DiscoverSection[] = ["today", "trending", "search"];

/** Today | Trending | Search over the existing routes; switching keeps the selected domain. */
export function DiscoverTabs({ current, domain, locale }: { current: DiscoverSection; domain?: Domain; locale: Locale }) {
  const t = common[locale];
  const query = domain ? `?domain=${domain}` : "";
  return <nav className="discover-tabs" aria-label={t.discoverNav}><ul>
    {DISCOVER_SECTIONS.map(section => <li key={section}>
      <Link href={`/${section}${query}`} aria-current={section === current ? "page" : undefined}>{t.nav[section]}</Link>
    </li>)}
  </ul></nav>;
}

/** A collapsed explanation (methodology, data notes) kept out of the primary flow. */
export function InfoNote({ title, children }: { title: string; children: ReactNode }) {
  return <details className="info-note">
    <summary><span aria-hidden="true" className="info-mark">ⓘ</span> {title}</summary>
    <div className="info-body">{children}</div>
  </details>;
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

/**
 * Report and source counts only when there is more than one (Product-2.1): "1 report · 1 source" on
 * every single-source card repeated what the source name already says.
 */
function Coverage({ event, locale }: { event: EventSummary; locale: Locale }) {
  const t = common[locale];
  return <>
    {event.content_count > 1 && <span>{t.reports(event.content_count)}</span>}
    {event.source_count > 1 && <span>{t.sources(event.source_count)}</span>}
  </>;
}

/**
 * Canonical Events as compact rows: title, then one metadata line — latest-report time (the list order),
 * the reporting sources, domain when mixed, coverage when above one, non-general type — and, outside
 * `compact` (Home), a few linked entities.
 */
export function EventList({ items, locale, showDomain = false, compact = false }: { items: EventSummary[]; locale: Locale; showDomain?: boolean; compact?: boolean }) {
  const t = common[locale];
  return <ul className="event-list">{items.map(event => <li key={event.id}>
    <Link className="event-title" href={`/event/${event.id}`} lang={event.title_language}>{event.canonical_title}</Link>
    <p className="meta">
      <span>{t.lastReported} <time dateTime={event.time.last_published_at}>{formatDateTime(event.time.last_published_at, locale)}</time></span>
      <span className="event-sources">{event.sources.map(source => source.name).join(" · ")}{event.source_count > event.sources.length && " …"}</span>
      {showDomain && <span>{t.shortDomains[event.domain]}</span>}
      <Coverage event={event} locale={locale}/>
      {event.event_type !== "general" && <span className="tag">{t.eventTypes[event.event_type] ?? event.event_type}</span>}
    </p>
    {!compact && <>
    {(event.subjects.length > 0 || event.companies.length > 0) && <ul className="chips event-entities">
      {event.subjects.map(subject => <li key={`s-${subject.id}`}><Link href={`/subject/${subject.id}`}>{subject.display_names[locale]}</Link> <small>{t.subjectTag}</small></li>)}
      {event.companies.map(company => <li key={`c-${company.id}`}><Link href={`/company/${company.id}`}>{company.display_names[locale]}</Link> <small>{t.companyTag}</small></li>)}
    </ul>}</>}
  </li>)}</ul>;
}

const RELATED_EVENTS = 5;

/**
 * Up to five recent Events related to one Topic (Subject) or Company. With `hideWhenEmpty`, the whole
 * section is left out when nothing is related, so sparse detail pages are not padded with empty cards.
 */
export function RelatedEvents({ filter, locale, hideWhenEmpty = false }: { filter: {subject_id: string} | {company_id: string}; locale: Locale; hideWhenEmpty?: boolean }) {
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
  if (hideWhenEmpty && (failed || !page || page.items.length === 0)) return null;
  return <section className="detail-section" aria-labelledby="related-events"><h2 id="related-events" className="section-title">{t.relatedEvents}{page && page.total > 0 && ` (${page.total})`}</h2>
    <article className="ranking-card">
      {failed && <p className="empty">{t.unavailable}</p>}
      {!page && !failed && <p className="empty">{t.loading}</p>}
      {page?.items.length === 0 && <p className="empty">{t.noRelatedEvents}</p>}
      {page && page.items.length > 0 && <EventList items={page.items} locale={locale}/>}
      {page && page.total > page.items.length && <Link href={`/events?${key}`}>{t.viewAllEvents(page.total)} →</Link>}
    </article>
  </section>;
}
