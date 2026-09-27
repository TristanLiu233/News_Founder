"use client";

import { useEffect, useState, type SyntheticEvent } from "react";

import { DomainTabs, ExternalLink, isDomain, PageState, TrendBadge } from "../../components/shared";
import {
  ApiError, fetchCompanyAppearances, fetchCompanyRanking, fetchCompanyRankingHistory, fetchCompanyRankings,
  type CompanyRankingAppearances, type CompanyRankingDefinition, type CompanyRankingDetail, type CompanyRankingEntry,
  type CompanyRankingHistory, type CompanyRankingSummary, type Domain,
} from "../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../lib/i18n";

const DEFAULT_ROWS = 20;
// The API maximum; every tracked ranking stores at most this many rows per snapshot.
const ALL_ROWS = 200;
const HISTORY_LIMIT = 20;

const copy = {
  en: {eyebrow: "External company rankings", title: "Rankings", intro: "Company rankings published by external sources, shown as published and tracked over time. The platform does not score companies: each ranking keeps its own publisher, method and metric, and rankings are never combined.", availableRankings: "Available rankings", noRankings: "No external company rankings are configured for this domain yet.", noSnapshot: "No snapshot of this ranking has been collected yet.", noSnapshotNote: "Rankings are collected by a separate ingestion command; this page only shows stored snapshots.", notFound: "Ranking not found", notFoundNote: "No external ranking or snapshot exists for this address.", backToRankings: "Back to Rankings", publisher: "Publisher", measure: "Ranked by", cadence: "Source updates", acquisition: "Acquisition", snapshotDate: "Snapshot", observed: "Observed", coverage: "Coverage", coverageText: (stored: number, listed: number | null) => listed && listed > stored ? `Top ${stored} of ${listed.toLocaleString("en")} listed` : `${stored} listed`, sourceColumn: "Source column", sourceDescription: "Source description", original: "Original source", disclaimer: "External ranking shown as published. It is not a platform score, rating or recommendation.", metrics: {market_cap: "Market cap", revenue: "Revenue", total_assets: "Total assets"} as Record<string, string>, units: {USD: "USD", "USD billion": "US$ bn"} as Record<string, string>, cadences: {daily: "Daily", on_revision: "When the source page is revised"} as Record<string, string>, columns: {rank: "Rank", company: "Company", change: "Change"}, noEarlier: "No earlier snapshot", firstSnapshot: "Earliest tracked snapshot: there is no earlier snapshot to compare with.", comparedWith: (date: string) => `Change compared with the snapshot of ${date}.`, showing: (shown: number, total: number) => `Showing ${shown} of ${total}`, showAll: (total: number) => `Show all ${total}`, showTop: (count: number) => `Show Top ${count}`, dropped: (count: number) => `Left the list since the previous snapshot (${count})`, previously: "previously", history: (count: number) => `Snapshot history (${count})`, historyDate: "Snapshot", historyEntries: "Companies", leader: "#1", view: "View", viewing: "Viewing", historical: (date: string) => `Viewing the historical snapshot of ${date}.`, backToLatest: "Back to latest", otherRankings: "Other rankings", noOtherRankings: "Not listed in any other tracked ranking.", notInLatest: "not in latest snapshot", loadFailed: "Could not load this section.", partial: "Some source rows could not be parsed; only valid rows are stored."},
  "zh-CN": {eyebrow: "外部公司排行榜", title: "排行榜", intro: "展示外部来源发布的公司排行榜，按原样呈现并追踪历史变化。平台不对公司打分：每个榜单保留各自的发布方、方法和指标，不同榜单之间从不合并。", availableRankings: "可用榜单", noRankings: "该领域暂未配置外部公司排行榜。", noSnapshot: "该榜单尚未采集任何快照。", noSnapshotNote: "榜单由独立的采集命令获取；本页面只展示已存储的快照。", notFound: "未找到该榜单", notFoundNote: "该地址没有对应的外部榜单或快照。", backToRankings: "返回排行榜", publisher: "发布方", measure: "排名依据", cadence: "来源更新", acquisition: "获取方式", snapshotDate: "快照时间", observed: "采集时间", coverage: "覆盖范围", coverageText: (stored: number, listed: number | null) => listed && listed > stored ? `来源共列出 ${listed.toLocaleString("zh-CN")} 家，追踪前 ${stored} 名` : `共 ${stored} 家`, sourceColumn: "来源列名", sourceDescription: "来源说明", original: "原始来源", disclaimer: "外部榜单按原样展示，不代表平台评分、评级或推荐。", metrics: {market_cap: "市值", revenue: "营收", total_assets: "总资产"} as Record<string, string>, units: {USD: "美元", "USD billion": "十亿美元"} as Record<string, string>, cadences: {daily: "每日", on_revision: "来源页面修订时"} as Record<string, string>, columns: {rank: "排名", company: "公司", change: "变化"}, noEarlier: "无更早快照", firstSnapshot: "这是最早追踪的快照，没有更早的快照可供比较。", comparedWith: (date: string) => `与 ${date} 的快照相比。`, showing: (shown: number, total: number) => `显示 ${shown} / ${total}`, showAll: (total: number) => `显示全部 ${total}`, showTop: (count: number) => `显示前 ${count}`, dropped: (count: number) => `自上次快照后离开榜单（${count}）`, previously: "上次", history: (count: number) => `快照历史（${count}）`, historyDate: "快照时间", historyEntries: "公司数", leader: "第 1 名", view: "查看", viewing: "当前查看", historical: (date: string) => `正在查看 ${date} 的历史快照。`, backToLatest: "返回最新", otherRankings: "其他榜单", noOtherRankings: "未出现在其他已追踪榜单中。", notInLatest: "不在最新快照中", loadFailed: "该部分加载失败。", partial: "部分来源行无法解析，仅存储了有效行。"},
} as const;

type View = {domain: Domain; ranking?: string; snapshot?: string};

function readView(): View {
  const params = new URLSearchParams(window.location.search);
  const domain = params.get("domain");
  return {domain: isDomain(domain) ? domain : "computer", ranking: params.get("ranking") ?? undefined, snapshot: params.get("snapshot") ?? undefined};
}

function viewUrl(view: View) {
  const params = new URLSearchParams({domain: view.domain});
  if (view.ranking) params.set("ranking", view.ranking);
  if (view.snapshot) params.set("snapshot", view.snapshot);
  return `/rankings?${params}`;
}

function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

function metricHeading(ranking: CompanyRankingDefinition, locale: Locale) {
  const t = copy[locale];
  const label = t.metrics[ranking.metric_type] ?? ranking.metric_label;
  const unit = ranking.metric_unit ? t.units[ranking.metric_unit] ?? ranking.metric_unit : undefined;
  return unit ? `${label} (${unit})` : label;
}

function formatMetric(entry: CompanyRankingEntry, ranking: CompanyRankingDefinition, locale: Locale) {
  if (ranking.metric_unit === "USD" && entry.metric_value !== null) {
    return new Intl.NumberFormat(locale, {style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2}).format(entry.metric_value);
  }
  return entry.raw_value ?? "—";
}

function Movement({ entry, locale }: { entry: CompanyRankingEntry; locale: Locale }) {
  if (entry.movement === null) {
    const label = copy[locale].noEarlier;
    return <span className="trend trend-unchanged" title={label}><span aria-hidden="true">—</span><span className="visually-hidden">{label}</span></span>;
  }
  return <TrendBadge trend={entry.movement} change={entry.rank_change} locale={locale}/>;
}

function RankingSelector({ items, selected, onSelect, locale }: {
  items: CompanyRankingSummary[]; selected?: string; onSelect: (key: string) => void; locale: Locale;
}) {
  const t = copy[locale];
  return <div className="ranking-selector" role="group" aria-label={t.availableRankings}>
    {items.map(item => <button key={item.key} type="button" aria-pressed={item.key === selected} onClick={() => onSelect(item.key)}>
      <strong lang={item.primary_language}>{item.name}</strong>
      <span>{item.publisher} · {t.metrics[item.metric_type] ?? item.metric_label} · {item.latest_snapshot ? formatDateTime(item.latest_snapshot.effective_at, locale) : t.noSnapshot}</span>
    </button>)}
  </div>;
}

function OtherRankings({ entry, rankingKey, locale }: { entry: CompanyRankingEntry; rankingKey: string; locale: Locale }) {
  const t = copy[locale];
  const [data, setData] = useState<CompanyRankingAppearances>();
  const [failed, setFailed] = useState(false);
  function load(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || data) return;
    setFailed(false);
    fetchCompanyAppearances(entry.company_id, 2).then(setData).catch(() => setFailed(true));
  }
  const others = data?.rankings.filter(item => item.ranking.key !== rankingKey) ?? [];
  return <details className="row-details" onToggle={load}>
    <summary>{t.otherRankings}</summary>
    {failed ? <p className="meta">{t.loadFailed}</p> : !data ? <p className="meta">{common[locale].loading}</p>
      : others.length === 0 ? <p className="meta">{t.noOtherRankings}</p>
      : <ul className="other-rankings">{others.map(item => <li key={item.ranking.key}>
        <span lang={item.ranking.primary_language}>{item.ranking.name}</span> · {item.ranking.publisher} · {common[locale].shortDomains[item.ranking.domain]}: {item.latest_rank ? <strong>#{item.latest_rank}</strong> : t.notInLatest}
      </li>)}</ul>}
  </details>;
}

function EntriesTable({ detail, locale }: { detail: CompanyRankingDetail; locale: Locale }) {
  const t = copy[locale];
  return <div className="table-scroll"><table className="company-table">
    <thead><tr><th scope="col">{t.columns.rank}</th><th scope="col">{t.columns.company}</th><th scope="col" className="change">{t.columns.change}</th><th scope="col" className="numeric">{metricHeading(detail.ranking, locale)}</th></tr></thead>
    <tbody>{detail.entries.map(entry => {
      const meta = [entry.metadata.symbol, entry.metadata.country].filter((value): value is string => typeof value === "string");
      return <tr key={entry.company_id}>
        <td className="rank">#{entry.rank}</td>
        <td><span className="company-name" lang={detail.ranking.primary_language}>{entry.display_name}</span>
          {meta.length > 0 && <span className="company-meta">{meta.join(" · ")}</span>}
          <OtherRankings entry={entry} rankingKey={detail.ranking.key} locale={locale}/>
        </td>
        <td className="change">
          <Movement entry={entry} locale={locale}/>
          {entry.previous_rank !== null && entry.movement !== "unchanged" && <span className="company-meta">{t.previously} #{entry.previous_rank}</span>}
        </td>
        <td className="numeric" title={entry.raw_value ?? undefined}>{formatMetric(entry, detail.ranking, locale)}</td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function HistorySection({ detail, onView, locale }: { detail: CompanyRankingDetail; onView: (snapshotId: string) => void; locale: Locale }) {
  const t = copy[locale];
  const [history, setHistory] = useState<CompanyRankingHistory>();
  const [failed, setFailed] = useState(false);
  const key = detail.ranking.key;
  useEffect(() => { setHistory(undefined); setFailed(false); }, [key]);
  function load(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || history) return;
    fetchCompanyRankingHistory(key, HISTORY_LIMIT).then(setHistory).catch(() => setFailed(true));
  }
  return <details className="history-details" onToggle={load}>
    <summary>{t.history(detail.snapshot_count)}</summary>
    {failed ? <p className="meta">{t.loadFailed}</p> : !history ? <p className="meta">{common[locale].loading}</p> : <table className="history-table">
      <thead><tr><th scope="col">{t.historyDate}</th><th scope="col">{t.historyEntries}</th><th scope="col">{t.leader}</th><th scope="col"><span className="visually-hidden">{t.view}</span></th></tr></thead>
      <tbody>{history.items.map(item => <tr key={item.id}>
        <td><ExternalLink href={item.source_url} locale={locale}>{formatDateTime(item.effective_at, locale)}</ExternalLink></td>
        <td>{item.entry_count}</td>
        <td lang={detail.ranking.primary_language}>{item.leader?.name ?? "—"}</td>
        <td>{item.id === detail.snapshot?.id ? <span className="tag current">{t.viewing}</span> : <button type="button" className="link-button" onClick={() => onView(item.id)}>{t.view}</button>}</td>
      </tr>)}</tbody>
    </table>}
  </details>;
}

function RankingPanel({ detail, rows, setRows, onView, onLatest, viewingHistory, locale }: {
  detail: CompanyRankingDetail; rows: number; setRows: (rows: number) => void;
  onView: (snapshotId: string) => void; onLatest: () => void; viewingHistory: boolean; locale: Locale;
}) {
  const t = copy[locale];
  const { ranking, snapshot } = detail;
  const description = typeof snapshot?.metadata.basis === "string" ? snapshot.metadata.basis : ranking.methodology;
  const sourceColumn = typeof snapshot?.metadata.metric_header === "string" ? snapshot.metadata.metric_header : undefined;
  // A snapshot links to the exact published version (for example a wiki revision).
  const sourceUrl = snapshot?.source_url ?? ranking.source_url;
  return <article className="ranking-card" aria-labelledby="ranking-title">
    <header className="list-header">
      <div><h2 id="ranking-title" className="card-title" lang={ranking.primary_language}>{ranking.name}</h2><p>{t.disclaimer}</p></div>
      <span className="tag">{ranking.publisher}</span>
    </header>
    <dl className="facts">
      <dt>{t.publisher}</dt><dd>{ranking.publisher}</dd>
      <dt>{t.measure}</dt><dd>{t.metrics[ranking.metric_type] ?? ranking.metric_label}</dd>
      <dt>{t.cadence}</dt><dd>{t.cadences[ranking.frequency] ?? ranking.frequency}</dd>
      <dt>{t.acquisition}</dt><dd>{ranking.acquisition}</dd>
      {snapshot && <><dt>{t.snapshotDate}</dt><dd>{formatDateTime(snapshot.effective_at, locale)}</dd>
        <dt>{t.observed}</dt><dd>{formatDateTime(snapshot.observed_at, locale)}</dd>
        <dt>{t.coverage}</dt><dd>{t.coverageText(snapshot.entry_count, snapshot.source_entry_count)}</dd></>}
      {sourceColumn && <><dt>{t.sourceColumn}</dt><dd lang={ranking.primary_language}>{sourceColumn}</dd></>}
      {description && <><dt>{t.sourceDescription}</dt><dd lang={ranking.primary_language}>{description}</dd></>}
      {sourceUrl && <><dt>{t.original}</dt><dd><ExternalLink href={sourceUrl} locale={locale}>{new URL(sourceUrl).hostname}</ExternalLink></dd></>}
    </dl>
    {viewingHistory && snapshot && <p className="partial">{t.historical(formatDateTime(snapshot.effective_at, locale))} <button type="button" className="link-button" onClick={onLatest}>{t.backToLatest}</button></p>}
    {!snapshot ? <div className="notice"><p>{t.noSnapshot}</p><p>{t.noSnapshotNote}</p></div> : <>
      {snapshot.status === "partial" && <p className="partial">{t.partial}</p>}
      <p className="section-note">{detail.previous_snapshot ? t.comparedWith(formatDateTime(detail.previous_snapshot.effective_at, locale)) : t.firstSnapshot}</p>
      <EntriesTable detail={detail} locale={locale}/>
      <div className="pagination">
        <span className="result-count">{t.showing(detail.entries.length, detail.total)}</span>
        {detail.total > detail.entries.length && <button type="button" onClick={() => setRows(ALL_ROWS)}>{t.showAll(detail.total)}</button>}
        {rows > DEFAULT_ROWS && detail.total > DEFAULT_ROWS && <button type="button" onClick={() => setRows(DEFAULT_ROWS)}>{t.showTop(DEFAULT_ROWS)}</button>}
      </div>
      {detail.dropped_count > 0 && <details><summary>{t.dropped(detail.dropped_count)}</summary><ul className="breakdown">
        {detail.dropped.map(item => <li key={item.company_id}><span lang={ranking.primary_language}>{item.display_name}</span> · {t.previously} #{item.previous_rank}</li>)}
      </ul></details>}
      <HistorySection detail={detail} onView={onView} locale={locale}/>
    </>}
  </article>;
}

export default function RankingsPage() {
  const { locale } = useLocale();
  const t = copy[locale];
  const [view, setView] = useState<View>();
  const [rows, setRows] = useState(DEFAULT_ROWS);
  const [list, setList] = useState<{domain: Domain; items: CompanyRankingSummary[]}>();
  const [listError, setListError] = useState<string>();
  const [detail, setDetail] = useState<CompanyRankingDetail>();
  const [detailError, setDetailError] = useState<string>();
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    const restore = () => { setView(readView()); setRows(DEFAULT_ROWS); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const domain = view?.domain;
  useEffect(() => {
    if (!domain) return;
    const controller = new AbortController();
    setListError(undefined);
    fetchCompanyRankings({domain, limit: 50}, controller.signal)
      .then(page => setList({domain, items: page.items}))
      .catch(reason => { if (!controller.signal.aborted) setListError(errorText(reason)); });
    return () => controller.abort();
  }, [domain]);

  const items = list && list.domain === domain ? list.items : undefined;
  const fallback = items?.find(item => item.status === "ready") ?? items?.[0];
  const selected = view?.ranking ?? fallback?.key;
  const snapshotId = view?.snapshot;
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetailError(undefined); setNotFound(false);
    fetchCompanyRanking(selected, {snapshot_id: snapshotId, limit: rows}, controller.signal)
      .then(setDetail)
      .catch(reason => {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 404) setNotFound(true);
        else setDetailError(errorText(reason));
      });
    return () => controller.abort();
  }, [selected, snapshotId, rows]);

  // A shared link may name a ranking from another domain; follow the ranking's domain.
  useEffect(() => {
    if (!view || !detail || detail.ranking.key !== view.ranking || detail.ranking.domain === view.domain) return;
    const next = {...view, domain: detail.ranking.domain};
    window.history.replaceState(null, "", viewUrl(next));
    setView(next);
  }, [detail, view]);

  function navigate(next: View) {
    setView(next); setRows(DEFAULT_ROWS); setNotFound(false);
    window.history.pushState(null, "", viewUrl(next));
  }
  const current = detail && detail.ranking.key === selected && (detail.snapshot?.id === snapshotId || !snapshotId) ? detail : undefined;

  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1><p className="intro">{t.intro}</p>
    {domain && <DomainTabs value={domain} onChange={value => navigate({domain: value})} locale={locale} label={(value, active) => common[active].domains[value]}/>}
    <PageState loading={!items && !listError} error={listError} locale={locale}/>
    {items && items.length === 0 && <p className="notice">{t.noRankings}</p>}
    {items && items.length > 0 && domain && <RankingSelector items={items} selected={selected} onSelect={key => navigate({domain, ranking: key})} locale={locale}/>}
    {notFound ? <div className="notice" role="alert"><p><strong>{t.notFound}</strong></p><p>{t.notFoundNote}</p><p><button type="button" className="link-button" onClick={() => navigate({domain: domain ?? "computer"})}>{t.backToRankings}</button></p></div>
      : selected && <>
        <PageState loading={!current && !detailError} error={detailError} locale={locale}/>
        {current && domain && <RankingPanel detail={current} rows={rows} setRows={setRows} locale={locale}
          viewingHistory={Boolean(snapshotId)}
          onView={id => navigate({domain, ranking: current.ranking.key, snapshot: id})}
          onLatest={() => navigate({domain, ranking: current.ranking.key})}/>}
      </>}
  </div></main>;
}
