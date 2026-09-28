"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ExternalLink, InfoNote, PageState } from "../../../components/shared";
import { ApiError, fetchEvent, fetchEventAi, fetchEventContent, type AIInsightArtifact, type AIInsightType, type EventAI, type EventContentItem, type EventDetail } from "../../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../../lib/i18n";

const TIMELINE_PAGE = 10;
const copy = {
  en: {eyebrow: "Event", firstReported: (date: string) => `First reported ${date}`, subjects: "Related Topics", companies: "Related Companies", timeline: "Timeline", notFound: "Event not found", notFoundNote: "No event exists at this address.", allEvents: "Browse all events", loadFailed: "Could not load this section.", about: "About this event", timeBasis: (first: string, last: string) => `First reported ${first}; last reported ${last}. These are the times the sources published their reports, not verified times of the occurrence itself.`, bySource: "Reports by source", byLanguage: "Languages", coverageNote: "The number of sources shows how widely this was reported, not how important it is.", grouping: "Reports are grouped into one event when their headlines, timing, and linked topics match; related topics and companies come from the links recorded on these reports. The event type is a label taken from the headlines."},
  "zh-CN": {eyebrow: "事件", firstReported: (date: string) => `首次报道于 ${date}`, subjects: "相关主题", companies: "相关公司", timeline: "时间线", notFound: "未找到该事件", notFoundNote: "该地址没有对应的事件。", allEvents: "浏览全部事件", loadFailed: "该部分加载失败。", about: "关于此事件", timeBasis: (first: string, last: string) => `首次报道于 ${first}，最近报道于 ${last}。以上为来源发布报道的时间，并非经过核实的事件发生时间。`, bySource: "各来源报道数", byLanguage: "语言", coverageNote: "来源数只表示报道范围，不代表重要性。", grouping: "标题、时间和关联主题相匹配的报道会归入同一事件；相关主题与公司来自这些报道已记录的关联。事件类型是依据标题给出的标签。"},
} as const;

const aiCopy = {
  en: {
    summary: "What Happened", why: "Why It Matters", badge: "AI-generated", combined: "AI Intelligence",
    combinedUnavailable: "AI insights are currently unavailable for this event.",
    disclosure: (reports: number, sources: number) => `${sources === 1 ? `Based on ${reports === 1 ? "1 report" : `${reports} reports`} from a single source` : `Based on ${reports} reports from ${sources} sources`}. AI may make mistakes; check the linked reports.`,
    evidence: "Sources",
    states: {not_generated: "Not generated for this event.", failed: "Not available: the last attempt did not pass checks.", rejected: "Withdrawn after review and not shown.", stale: "Not shown: it was generated from earlier reports or earlier AI instructions."} as Record<string, string>,
    insufficientWhy: "The reports do not give enough context to explain why this matters.",
    insufficientSummary: "The reports are brief; this summary only restates what they report.",
    aiDetails: "AI insights",
    generated: (date: string, model: string, version: string) => `generated ${date} · ${model} · ${version}`,
    contexts: {business: "Business", technology: "Technology", industry: "Industry", product: "Product", regulatory: "Regulatory"} as Record<string, string>,
  },
  "zh-CN": {
    summary: "发生了什么", why: "为何值得关注", badge: "AI 生成", combined: "AI 解读",
    combinedUnavailable: "该事件暂无可用的 AI 解读。",
    disclosure: (reports: number, sources: number) => `${sources === 1 ? `依据单一来源的 ${reports} 篇报道` : `依据 ${sources} 个来源的 ${reports} 篇报道`}。AI 可能出错，请以所链接的报道为准。`,
    evidence: "来源",
    states: {not_generated: "该事件尚未生成。", failed: "暂不可用：最近一次生成未通过校验。", rejected: "经人工审核后已撤下，不予显示。", stale: "不予显示：基于较早的报道或较早的 AI 指令生成。"} as Record<string, string>,
    insufficientWhy: "现有报道不足以说明该事件为何值得关注。",
    insufficientSummary: "报道内容较少，此摘要仅复述报道所述内容。",
    aiDetails: "AI 解读",
    generated: (date: string, model: string, version: string) => `生成于 ${date} · ${model} · ${version}`,
    contexts: {business: "商业", technology: "技术", industry: "行业", product: "产品", regulatory: "监管"} as Record<string, string>,
  },
} as const;

const AI_TYPES: AIInsightType[] = ["event_summary", "event_why_it_matters"];

/** An available insight: labelled AI-generated, every point linked to the reports it cites. */
function AIInsightCard({ kind, artifact, locale }: { kind: AIInsightType; artifact: AIInsightArtifact; locale: Locale }) {
  const t = aiCopy[locale];
  return <section className="ranking-card ai-card" aria-labelledby={`ai-${kind}`}>
    <h2 id={`ai-${kind}`}>{kind === "event_summary" ? t.summary : t.why}<span className="tag ai-badge">{t.badge}</span></h2>
    {artifact.points.length > 0 && <ul className="ai-points">{artifact.points.map((point, index) => <li key={index}>
      {point.context && <span className="tag">{t.contexts[point.context] ?? point.context}</span>} <span>{point.text}</span>
      <span className="ai-evidence"> <span className="visually-hidden">{t.evidence}{locale === "zh-CN" ? "：" : ": "}</span>{point.evidence.map(item => <ExternalLink key={item.content_id} href={item.url} locale={locale}><span title={item.title}>[{item.position}] {item.source}</span></ExternalLink>)}</span>
    </li>)}</ul>}
    {artifact.insufficient_evidence && <p className="section-note">{kind === "event_summary" ? t.insufficientSummary : t.insufficientWhy}</p>}
  </section>;
}

/**
 * What Happened and Why It Matters from persisted, validated artifacts only. Only `available` artifacts
 * carry text; every other state is a one-line note, and one combined note when neither is available.
 */
function AIInsights({ data, failed, locale }: { data?: EventAI; failed: boolean; locale: Locale }) {
  const t = aiCopy[locale];
  if (!data && !failed) return null;
  const available = data ? AI_TYPES.filter(kind => data.insights[kind].state === "available" && data.insights[kind].artifact) : [];
  if (!data || available.length === 0) return <section className="ai-unavailable" aria-labelledby="ai-combined">
    <h2 id="ai-combined">{t.combined}</h2><p>{t.combinedUnavailable}</p>
  </section>;
  return <div className="ai-insights">
    <p className="ai-disclosure"><span className="tag ai-badge">{t.badge}</span> {t.disclosure(data.content_count, data.source_count)}</p>
    {AI_TYPES.map(kind => {
      const insight = data.insights[kind];
      if (insight.state === "available" && insight.artifact) return <AIInsightCard key={kind} kind={kind} artifact={insight.artifact} locale={locale}/>;
      return <section key={kind} className="ai-unavailable" aria-labelledby={`ai-${kind}`}>
        <h2 id={`ai-${kind}`}>{kind === "event_summary" ? t.summary : t.why}</h2><p>{t.states[insight.state] ?? t.states.not_generated}</p>
      </section>;
    })}
  </div>;
}

/** Generation details for the About note: provenance of each AI insight, or why it is not shown. */
function AIDetails({ data, locale }: { data?: EventAI; locale: Locale }) {
  if (!data) return null;
  const t = aiCopy[locale];
  const sep = locale === "zh-CN" ? "：" : ": ";
  return <p>{t.aiDetails}{sep}{AI_TYPES.map((kind, index) => {
    const insight = data.insights[kind];
    const title = kind === "event_summary" ? t.summary : t.why;
    const text = insight.state === "available" && insight.artifact
      ? t.generated(formatDateTime(insight.artifact.generated_at, locale), insight.artifact.model, insight.artifact.prompt_version)
      : (t.states[insight.state] ?? t.states.not_generated).replace(/[.。]$/, "");
    return <span key={kind}>{index > 0 && (locale === "zh-CN" ? "；" : "; ")}{locale === "zh-CN" ? `「${title}」${text}` : `${title} — ${text}`}</span>;
  })}{locale === "zh-CN" ? "。" : "."}</p>;
}

function Timeline({ eventId, total, locale }: { eventId: string; total: number; locale: Locale }) {
  const [items, setItems] = useState<EventContentItem[]>();
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setItems(undefined); setFailed(false);
    fetchEventContent(eventId, {limit: TIMELINE_PAGE, order: "asc"}, controller.signal)
      .then(page => setItems(page.items))
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [eventId]);
  async function more() {
    if (!items) return;
    setLoading(true);
    try {
      const page = await fetchEventContent(eventId, {limit: TIMELINE_PAGE, offset: items.length, order: "asc"});
      const known = new Set(items.map(item => item.id));
      setItems([...items, ...page.items.filter(item => !known.has(item.id))]);
    } catch { setFailed(true); } finally { setLoading(false); }
  }
  const t = copy[locale]; const c = common[locale];
  // Each report as published, oldest first, linking to the original article: the timeline is also the source list.
  return <section className="detail-section" aria-labelledby="timeline"><h2 id="timeline" className="section-title">{t.timeline} <span className="section-count">{c.reports(total)}</span></h2>
    <article className="ranking-card">
      {failed && <p className="empty">{t.loadFailed}</p>}
      {!items && !failed && <p className="empty">{c.loading}</p>}
      {items && <ol className="event-timeline">{items.map(item => <li key={item.id}>
        <time dateTime={item.published_at ?? item.observed_at}>{formatDateTime(item.published_at ?? item.observed_at, locale)}</time>
        <div><ExternalLink href={item.url} locale={locale}><span lang={item.language}>{item.title}</span></ExternalLink>
          <p className="meta">{item.source} · {c.languages[item.language] ?? item.language}</p></div>
      </li>)}</ol>}
      {items && items.length < total && <button type="button" className="link-button" disabled={loading} onClick={more}>{loading ? c.loading : c.viewMore}</button>}
    </article>
  </section>;
}

export default function EventPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { locale } = useLocale();
  const [detail, setDetail] = useState<EventDetail>();
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string>();
  const loaded = useRef<EventDetail | undefined>(undefined);
  useEffect(() => {
    // After a redirect replace, the canonical detail is already loaded.
    if (loaded.current?.id === id) return;
    const controller = new AbortController();
    setStatus("loading"); setDetail(undefined);
    fetchEvent(id, controller.signal).then(value => {
      loaded.current = value;
      setDetail(value); setStatus("ready");
      // One permanent URL per canonical Event: replace merged duplicate IDs.
      if (value.id !== id) router.replace(`/event/${value.id}`);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 422)) { setStatus("missing"); return; }
      setError(reason instanceof Error ? reason.message : String(reason)); setStatus("error");
    });
    return () => controller.abort();
  }, [id, router]);
  // Persisted AI artifacts only; an error here never affects the rest of the page.
  const [ai, setAi] = useState<EventAI>();
  const [aiFailed, setAiFailed] = useState(false);
  const eventId = detail?.id;
  useEffect(() => {
    if (!eventId) return;
    const controller = new AbortController();
    setAi(undefined); setAiFailed(false);
    fetchEventAi(eventId, locale, controller.signal).then(setAi)
      .catch(() => { if (!controller.signal.aborted) setAiFailed(true); });
    return () => controller.abort();
  }, [eventId, locale]);
  const t = copy[locale]; const c = common[locale];
  if (status === "missing") return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.notFound}</h1><p className="intro">{t.notFoundNote}</p>
    <p><Link href="/events">{t.allEvents} →</Link></p>
  </div></main>;
  const sep = locale === "zh-CN" ? "：" : ": ";
  const stop = locale === "zh-CN" ? "。" : ". ";
  const count = (name: string, value: number) => locale === "zh-CN" ? `${name}（${value}）` : `${name} (${value})`;
  return <main><div className="shell">
    <PageState loading={status === "loading"} error={status === "error" ? error : undefined} locale={locale}/>
    {detail && <>
      <p className="eyebrow detail-eyebrow">{t.eyebrow}</p>
      <h1 className="subject-title event-headline" lang={detail.title_language}>{detail.canonical_title}</h1>
      <p className="event-meta">
        <span>{c.shortDomains[detail.domain]}</span>
        {detail.event_type !== "general" && <span>{c.eventTypes[detail.event_type] ?? detail.event_type}</span>}
        <span><time dateTime={detail.time.first_published_at}>{t.firstReported(formatDateTime(detail.time.first_published_at, locale))}</time></span>
        <span>{c.reports(detail.content_count)}</span>
        <span>{c.sources(detail.source_count)}</span>
      </p>
      <AIInsights data={ai} failed={aiFailed} locale={locale}/>
      <Timeline eventId={detail.id} total={detail.content_count} locale={locale}/>
      {(detail.subjects.length > 0 || detail.companies.length > 0) && <div className="detail-grid">
        {detail.subjects.length > 0 && <section className="detail-section" aria-labelledby="event-subjects"><h2 id="event-subjects" className="section-title">{t.subjects}</h2>
          <ul className="chips">{detail.subjects.map(subject => <li key={subject.id}><Link href={`/subject/${subject.id}`}>{subject.display_names[locale]}</Link></li>)}</ul>
        </section>}
        {detail.companies.length > 0 && <section className="detail-section" aria-labelledby="event-companies"><h2 id="event-companies" className="section-title">{t.companies}</h2>
          <ul className="chips">{detail.companies.map(company => <li key={company.id}><Link href={`/company/${company.id}`}>{company.display_names[locale]}</Link></li>)}</ul>
        </section>}
      </div>}
      <div className="detail-about">
        <InfoNote title={t.about}>
          <p>{t.timeBasis(formatDateTime(detail.time.first_published_at, locale), formatDateTime(detail.time.last_published_at, locale))}</p>
          <p>{t.bySource}{sep}{detail.sources.map(source => count(source.name, source.content_count)).join(" · ")}{detail.source_count > detail.sources.length && " …"}{stop}{t.byLanguage}{sep}{detail.languages.map(item => count(c.languages[item.language] ?? item.language, item.content_count)).join(" · ")}{stop.trim()}</p>
          <p>{t.coverageNote}</p>
          <p>{t.grouping}</p>
          <AIDetails data={ai} locale={locale}/>
        </InfoNote>
      </div>
    </>}
  </div></main>;
}
