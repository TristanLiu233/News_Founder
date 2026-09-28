"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ExternalLink, InfoNote, NewsList, PageState, RelatedEvents, TrendBadge, trendingDomainLabel } from "../../../components/shared";
import {
  ApiError, fetchNews, fetchSubject, fetchSubjectHistory,
  type NewsItem, type RankHistoryPoint, type SourceAppearance, type SubjectDetail, type SubjectHistory,
} from "../../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../../lib/i18n";

const HISTORY_LIMIT = 30;
const HISTORY_PREVIEW = 10;
const NEWS_PAGE = 10;

const copy = {
  en: {eyebrow: "Topic", appearsIn: (count: number) => `Appears in ${count} source ${count === 1 ? "list" : "lists"}`, lastSeen: "Last seen", currentRank: "Current Rank", notRanked: "Not Currently Ranked", notRankedNote: "Not in the latest Trending list for its domain.", viewTrending: "View in Trending", calculated: "Updated", previousRank: "previously", rankHistory: "Rank History", noHistory: "No ranking history yet.", historyNote: (shown: number, total: number) => `Latest ${shown} of ${total} Trending updates that included this topic.`, chartLabel: (count: number, best: number, worst: number) => `Trending rank over the last ${count} updates, best #${best}, worst #${worst}. Higher on the chart is a better rank.`, best: "Best", worst: "Worst", date: "Updated", rank: "Rank", sources: "Sources", sourceAppearances: "Where It Appears", sourceNote: "Its latest position in each source's own list.", currentSnapshot: "In current list", lastSeenTag: "Last seen", firstSeen: "first seen", appearances: (count: number) => `seen ${count} ${count === 1 ? "time" : "times"}`, bestRank: "best", noAppearances: "No source appearances recorded.", moreAppearances: (shown: number, total: number) => `Showing ${shown} of ${total} lists.`, relatedNews: (count: number) => `Related news (${count})`, relatedNewsNote: "The same reports also appear, grouped into events, under Related Events.", aliases: "Also Known As", aliasGroups: {en: "English", "zh-CN": "Chinese", other: "Other"} as Record<string, string>, aliasTypes: {official_name: "Official", localized_name: "Localized", source_name: "Source name"} as Record<string, string>, notFound: "Topic not found", notFoundNote: "No topic exists at this address.", searchArchive: "Search the archive", loadFailed: "Could not load this section.", marketPulse: "Market Pulse", methodologyRank: "The Trending rank combines this topic's positions across the source lists of its domain, weighting fresher lists more (reciprocal rank fusion). It measures cross-platform attention, not quality, importance, or investment merit.", methodologyScore: (score: string) => `Current combined score: ${score}.`, methodologyObservations: (count: number, lists: number) => `${count} source observations recorded across ${lists} lists.`, methodologyLinks: "Related reports and events are linked only on exact evidence: the same article, a unique exact name or alias, an exchange code, or a cashtag."},
  "zh-CN": {eyebrow: "主题", appearsIn: (count: number) => `出现在 ${count} 个来源榜单`, lastSeen: "最近出现", currentRank: "当前排名", notRanked: "当前未上榜", notRankedNote: "不在所属领域最新的综合趋势中。", viewTrending: "在综合趋势中查看", calculated: "更新于", previousRank: "上次", rankHistory: "排名历史", noHistory: "暂无排名历史。", historyNote: (shown: number, total: number) => `显示最近 ${shown} 次（共 ${total} 次）包含该主题的综合趋势更新。`, chartLabel: (count: number, best: number, worst: number) => `最近 ${count} 次综合趋势排名，最佳 #${best}，最差 #${worst}。图中越高代表排名越靠前。`, best: "最佳", worst: "最差", date: "更新时间", rank: "排名", sources: "来源数", sourceAppearances: "出现在哪些榜单", sourceNote: "在各来源自身榜单中的最新位置。", currentSnapshot: "在当前榜单", lastSeenTag: "最近出现", firstSeen: "首次出现", appearances: (count: number) => `出现 ${count} 次`, bestRank: "最佳", noAppearances: "暂无来源记录。", moreAppearances: (shown: number, total: number) => `显示 ${shown} / ${total} 个榜单。`, relatedNews: (count: number) => `相关新闻（${count}）`, relatedNewsNote: "这些报道也已按事件归组，显示在上方“相关事件”中。", aliases: "其他名称", aliasGroups: {en: "英文", "zh-CN": "中文", other: "其他"} as Record<string, string>, aliasTypes: {official_name: "官方名称", localized_name: "本地化名称", source_name: "来源名称"} as Record<string, string>, notFound: "未找到该主题", notFoundNote: "该地址没有对应的主题。", searchArchive: "搜索历史信息", loadFailed: "该部分加载失败。", marketPulse: "市场动态", methodologyRank: "综合趋势排名汇总该主题在所属领域各来源榜单中的位置，较新的榜单权重更高（倒数排名融合）。它衡量的是跨平台关注度，而非质量、重要性或投资价值。", methodologyScore: (score: string) => `当前综合分数：${score}。`, methodologyObservations: (count: number, lists: number) => `共记录 ${count} 次来源观测，涉及 ${lists} 个榜单。`, methodologyLinks: "相关报道与事件仅在有确切依据时关联：同一篇文章、唯一的精确名称或别名、交易所代码或 $代码。"},
} as const;

function formatMetric(appearance: SourceAppearance, locale: Locale) {
  const value = appearance.raw_metric;
  if (typeof value !== "number" && typeof value !== "string") return null;
  const text = typeof value === "number" ? value.toLocaleString(locale) : value;
  return appearance.metric_label ? `${text} ${appearance.metric_label}` : text;
}

function CurrentRank({ detail, locale }: { detail: SubjectDetail; locale: Locale }) {
  const t = copy[locale];
  const current = detail.current_ranking;
  return <article className="ranking-card compact">
    <header className="list-header"><h2 className="card-title">{t.currentRank}</h2><span className="tag">{trendingDomainLabel(detail.domain, locale)}</span></header>
    {current ? <>
      <p className="big-rank">#{current.rank} <TrendBadge trend={current.trend} change={current.rank_change} locale={locale}/></p>
      <p className="meta">{common[locale].sources(current.source_count)}{current.previous_rank && ` · ${t.previousRank} #${current.previous_rank}`}</p>
      <p className="meta">{t.calculated} {formatDateTime(current.calculated_at, locale)}</p>
    </> : <><p className="big-rank muted">{t.notRanked}</p><p className="meta">{t.notRankedNote}</p></>}
    <p className="card-footer"><Link href={`/trending?domain=${detail.domain}`}>{t.viewTrending} →</Link></p>
  </article>;
}

function RankChart({ points, locale }: { points: RankHistoryPoint[]; locale: Locale }) {
  const t = copy[locale];
  const ordered = [...points].reverse();
  const ranks = ordered.map(point => point.rank);
  const best = Math.min(...ranks), worst = Math.max(...ranks);
  const width = 600, height = 120, pad = 10;
  const x = (index: number) => pad + index * (width - 2 * pad) / Math.max(ordered.length - 1, 1);
  const y = (rank: number) => worst === best ? height / 2 : pad + (rank - best) * (height - 2 * pad) / (worst - best);
  const path = ordered.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.rank).toFixed(1)}`).join(" ");
  return <figure className="rank-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t.chartLabel(ordered.length, best, worst)} preserveAspectRatio="none">
      <path d={path} fill="none" vectorEffect="non-scaling-stroke"/>
      {ordered.map((point, index) => <circle key={point.ranking_run_id} cx={x(index)} cy={y(point.rank)} r="3.5"><title>#{point.rank} · {formatDateTime(point.calculated_at, locale)}</title></circle>)}
    </svg>
    <figcaption>{t.best} #{best} · {t.worst} #{worst}</figcaption>
  </figure>;
}

function RankHistorySection({ subjectId, locale }: { subjectId: string; locale: Locale }) {
  const [history, setHistory] = useState<SubjectHistory>();
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetchSubjectHistory(subjectId, HISTORY_LIMIT, controller.signal).then(setHistory).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [subjectId]);
  const t = copy[locale];
  const c = common[locale];
  const rows = expanded ? history?.items : history?.items.slice(0, HISTORY_PREVIEW);
  return <section className="detail-section" aria-labelledby="rank-history"><h2 id="rank-history" className="section-title">{t.rankHistory}</h2>
    <article className="ranking-card">
      {failed ? <p className="empty">{t.loadFailed}</p> : !history ? <p className="empty">{c.loading}</p> : history.items.length === 0 ? <p className="empty">{t.noHistory}</p> : <>
        <p className="section-note">{t.historyNote(history.items.length, history.total)}</p>
        {history.items.length > 1 && <RankChart points={history.items} locale={locale}/>}
        <table className="history-table"><thead><tr><th scope="col">{t.date}</th><th scope="col">{t.rank}</th><th scope="col">{t.sources}</th></tr></thead>
          <tbody>{rows?.map(point => <tr key={point.ranking_run_id}><td>{formatDateTime(point.calculated_at, locale)}</td><td><strong>#{point.rank}</strong> <TrendBadge trend={point.trend} change={point.rank_change} locale={locale}/></td><td>{point.source_count}</td></tr>)}</tbody>
        </table>
        {history.items.length > HISTORY_PREVIEW && <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? c.showLess : c.viewMore}</button>}
      </>}
    </article>
  </section>;
}

function SourceAppearances({ detail, locale }: { detail: SubjectDetail; locale: Locale }) {
  const t = copy[locale];
  return <section className="detail-section" aria-labelledby="source-appearances"><h2 id="source-appearances" className="section-title">{t.sourceAppearances}</h2>
    <article className="ranking-card">
      <p className="section-note">{t.sourceNote}</p>
      {detail.source_appearances.length === 0 ? <p className="empty">{t.noAppearances}</p> : <ul className="ranking-list">{detail.source_appearances.map(item => {
        const metric = formatMetric(item, locale);
        return <li key={`${item.source}-${item.rank_list}`}><span className="rank">#{item.latest_rank}</span><div>
          <div className="row-main"><strong>{item.source}</strong><span className="list-name">{item.rank_list}</span>
            {item.list_type === "market_pulse" && <span className="tag">{t.marketPulse}</span>}
            <span className={item.in_current_snapshot ? "tag current" : "tag"}>{item.in_current_snapshot ? t.currentSnapshot : t.lastSeenTag}</span></div>
          {item.title && <p className="appearance-title">{item.url ? <ExternalLink href={item.url} locale={locale}>{item.title}</ExternalLink> : item.title}</p>}
          <p><time dateTime={item.latest_observed_at}>{formatDateTime(item.latest_observed_at, locale)}</time>{metric && ` · ${metric}`} · {t.appearances(item.appearance_count)} · {t.bestRank} #{item.best_rank} · {t.firstSeen} {formatDateTime(item.first_seen_at, locale)}</p>
        </div></li>;
      })}</ul>}
      {detail.source_appearance_count > detail.source_appearances.length && <p className="section-note">{t.moreAppearances(detail.source_appearances.length, detail.source_appearance_count)}</p>}
    </article>
  </section>;
}

function RelatedNews({ subjectId, total, locale }: { subjectId: string; total: number; locale: Locale }) {
  const [items, setItems] = useState<NewsItem[]>();
  const [lastPage, setLastPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetchNews({subject_id: subjectId, limit: NEWS_PAGE}, controller.signal)
      .then(page => { setItems(page.items); setLastPage(page.items.length); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [subjectId]);
  async function more() {
    if (!items) return;
    setLoading(true);
    try {
      const page = await fetchNews({subject_id: subjectId, limit: NEWS_PAGE, offset: items.length});
      const known = new Set(items.map(item => item.id));
      setItems([...items, ...page.items.filter(item => !known.has(item.id))]);
      setLastPage(page.items.length);
    } catch { setFailed(true); } finally { setLoading(false); }
  }
  const t = copy[locale];
  const c = common[locale];
  // Every related report also belongs to an Event listed under Related Events, so this list is secondary.
  return <details className="secondary related-news">
    <summary>{t.relatedNews(total)}</summary>
    <p className="section-note">{t.relatedNewsNote}</p>
    {failed && <p className="empty">{t.loadFailed}</p>}
    {!items && !failed && <p className="empty">{c.loading}</p>}
    {items && items.length > 0 && <NewsList items={items} locale={locale} showLanguage/>}
    {items && items.length < total && lastPage === NEWS_PAGE && <button type="button" className="link-button" disabled={loading} onClick={more}>{loading ? c.loading : c.viewMore}</button>}
  </details>;
}

function Aliases({ detail, locale }: { detail: SubjectDetail; locale: Locale }) {
  const t = copy[locale];
  const groups = ["en", "zh-CN", "other"].map(group => ({
    group, aliases: detail.aliases.filter(alias => group === "other" ? !["en", "zh-CN"].includes(alias.locale) : alias.locale === group),
  })).filter(item => item.aliases.length > 0);
  if (groups.length === 0) return null;
  return <section className="detail-section" aria-labelledby="aliases"><h2 id="aliases" className="section-title">{t.aliases}</h2>
    <article className="ranking-card">
      {<dl className="alias-groups">{groups.map(({group, aliases}) => <div key={group}>
        <dt>{t.aliasGroups[group]}</dt>
        <dd><ul className="chips">{aliases.map(alias => <li key={`${alias.locale}-${alias.alias}`} lang={alias.locale}>{alias.alias} <small>{t.aliasTypes[alias.alias_type] ?? alias.alias_type}</small></li>)}</ul></dd>
      </div>)}</dl>}
    </article>
  </section>;
}

export default function SubjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { locale } = useLocale();
  const [detail, setDetail] = useState<SubjectDetail>();
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string>();
  const loaded = useRef<SubjectDetail | undefined>(undefined);
  useEffect(() => {
    // After a redirect replace, the canonical detail is already loaded.
    if (loaded.current?.subject_id === id) return;
    const controller = new AbortController();
    setStatus("loading"); setDetail(undefined);
    fetchSubject(id, controller.signal).then(value => {
      loaded.current = value;
      setDetail(value); setStatus("ready");
      // One permanent URL per canonical subject: replace merged/duplicate IDs.
      if (value.subject_id !== id) router.replace(`/subject/${value.subject_id}`);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 422)) { setStatus("missing"); return; }
      setError(reason instanceof Error ? reason.message : String(reason)); setStatus("error");
    });
    return () => controller.abort();
  }, [id, router]);
  const t = copy[locale];
  if (status === "missing") return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.notFound}</h1><p className="intro">{t.notFoundNote}</p>
    <p><Link href="/search">{t.searchArchive} →</Link></p>
  </div></main>;
  return <main><div className="shell">
    <PageState loading={status === "loading"} error={status === "error" ? error : undefined} locale={locale}/>
    {detail && <>
      <p className="eyebrow detail-eyebrow">{t.eyebrow}</p>
      <h1 className="subject-title">{detail.display_names[locale]}</h1>
      {detail.display_names[locale] !== detail.canonical_name && <p className="intro">{detail.canonical_name}</p>}
      <p className="detail-meta">
        <span>{common[locale].shortDomains[detail.domain]}</span>
        <span>{common[locale].subjectTypes[detail.subject_type] ?? detail.subject_type}</span>
        <span>{t.appearsIn(detail.source_appearance_count)}</span>
        {detail.latest_observed_at && <span>{t.lastSeen} <time dateTime={detail.latest_observed_at}>{formatDateTime(detail.latest_observed_at, locale)}</time></span>}
      </p>
      <div className="rank-summary"><CurrentRank detail={detail} locale={locale}/></div>
      <RelatedEvents filter={{subject_id: detail.subject_id}} locale={locale} hideWhenEmpty/>
      <RankHistorySection subjectId={detail.subject_id} locale={locale}/>
      <SourceAppearances detail={detail} locale={locale}/>
      {detail.related_news_count > 0 && <RelatedNews subjectId={detail.subject_id} total={detail.related_news_count} locale={locale}/>}
      <Aliases detail={detail} locale={locale}/>
      <div className="detail-about">
        <InfoNote title={common[locale].methodology}>
          <p>{t.methodologyRank}{detail.current_ranking && `${locale === "zh-CN" ? "" : " "}${t.methodologyScore(detail.current_ranking.final_score.toFixed(4))}`}</p>
          <p>{t.methodologyObservations(detail.observation_count, detail.source_appearance_count)}</p>
          <p>{t.methodologyLinks}</p>
        </InfoNote>
      </div>
    </>}
  </div></main>;
}
