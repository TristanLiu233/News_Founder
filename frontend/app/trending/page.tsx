"use client";

import { useEffect, useState } from "react";

import { DiscoverTabs, DomainTabs, InfoNote, isDomain, NewsList, PageState, TrendingTopicSummary } from "../../components/shared";
import { fetchTrending, type Domain, type TrendingDomain, type TrendingItem } from "../../lib/api";
import { common, formatDateTime, useLocale, type Locale } from "../../lib/i18n";

const DEFAULT_LIMIT = 20;
const EXPANDED_LIMIT = 50;

const copy = {
  en: {eyebrow: "Discover", title: "Trending", intro: "Topics drawing attention across each domain's source rankings right now.", methodologyText: ["Topics are ranked by freshness-weighted reciprocal rank fusion (RRF) of each domain's current source rankings: a higher position in more lists, observed more recently, gives a higher score.", "Each source list has a weight; freshness decays with the age of its snapshot. A topic's score is the sum of its source contributions.", "Trending measures cross-platform attention, not quality, merit or a prediction. Scores are never compared across domains."], financeNote: "Finance Trending is built only from Market Pulse rankings (crypto trends, market cap, A-share turnover). News feeds never enter this ranking.", calculated: "Calculated", showing: (shown: number, total: number) => `Showing ${shown} of ${total}`, showTop: (count: number) => `Show Top ${count}`, showTop20: "Show Top 20", unavailable: "No composite ranking has been calculated for this domain yet.", breakdown: "Sources", relatedNews: "Related News", score: "score", coverage: {chinese: "Chinese sources", international: "International sources", both: "Chinese + International"}, weight: "weight", freshness: "freshness", contribution: "contribution"},
  "zh-CN": {eyebrow: "发现", title: "综合趋势", intro: "各领域来源榜单中当前受到关注的主题。", methodologyText: ["主题按带时效衰减的加权倒数排名融合（RRF）排序：在更多来源榜单中排名更靠前、观测更新的主题分数更高。", "每个来源榜单有各自的权重；时效随快照的时间衰减。主题分数为各来源贡献之和。", "综合趋势衡量跨平台关注度，不代表质量、价值或预测。不同领域的分数互不比较。"], financeNote: "金融综合趋势仅由市场动态榜单（加密货币热搜、市值、A 股成交额）计算，新闻资讯不参与排名。", calculated: "计算于", showing: (shown: number, total: number) => `显示 ${shown} / ${total}`, showTop: (count: number) => `显示前 ${count}`, showTop20: "显示前 20", unavailable: "该领域暂无综合排名。", breakdown: "来源", relatedNews: "相关新闻", score: "分数", coverage: {chinese: "中文来源", international: "国际来源", both: "中文 + 国际来源"}, weight: "权重", freshness: "时效", contribution: "贡献"},
} as const;

function TrendingCard({ item, locale }: { item: TrendingItem; locale: Locale }) {
  const t = copy[locale];
  return <li className="trending-item">
    <span className="rank">#{item.rank}</span>
    <div>
      <TrendingTopicSummary item={item} locale={locale}/>
      {/* Score inputs stay available, collapsed; the Methodology note explains them. */}
      <details><summary>{t.breakdown}</summary>
        <ul className="breakdown">{item.sources.map(source => <li key={source.source_key}>
          <strong>{source.source}</strong> #{source.source_rank} · {source.rank_list}
          <span className="muted-score">{t.weight} {source.weight} · {t.freshness} {source.freshness.toFixed(3)} · {t.contribution} {source.contribution.toFixed(4)}</span>
        </li>)}</ul>
        <p className="breakdown-meta">{t.coverage[item.language_coverage]} · <span className="muted-score">{t.score} {item.final_score.toFixed(4)}</span></p>
      </details>
      {item.related_news_count > 0 && <details><summary>{t.relatedNews} ({item.related_news_count})</summary><NewsList items={item.related_news_preview.slice(0, 3)} locale={locale}/></details>}
    </div>
  </li>;
}

export default function TrendingPage() {
  const { locale } = useLocale();
  const [domain, setDomain] = useState<Domain>();
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [data, setData] = useState<TrendingDomain>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const restore = () => { const value = new URLSearchParams(window.location.search).get("domain"); setDomain(isDomain(value) ? value : "computer"); setLimit(DEFAULT_LIMIT); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (!domain) return;
    const controller = new AbortController();
    setError(undefined);
    fetchTrending(controller.signal, {domain, limit})
      .then(response => setData(response.domains[0]))
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [domain, limit]);
  function select(value: Domain) {
    setDomain(value); setLimit(DEFAULT_LIMIT); setData(undefined);
    window.history.pushState(null, "", `/trending?domain=${value}`);
  }
  const t = copy[locale];
  const current = data && data.domain === domain ? data : undefined;
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="trending" domain={domain} locale={locale}/>
    {domain && <DomainTabs value={domain} onChange={select} locale={locale}/>}
    <p className="intro">{t.intro}</p>
    <InfoNote title={common[locale].methodology}>
      {t.methodologyText.map(text => <p key={text}>{text}</p>)}
      {domain === "finance" && <p>{t.financeNote}</p>}
    </InfoNote>
    <PageState loading={!current} error={error} locale={locale}/>
    {current && <section className="domain" aria-labelledby="trending-heading">
      <div className="domain-heading"><h2 id="trending-heading">{common[locale].shortDomains[current.domain]}{current.domain === "finance" && ` · ${common[locale].marketPulse}`}</h2>
        {current.calculated_at && <span>{t.calculated} {formatDateTime(current.calculated_at, locale)}</span>}</div>
      {current.status !== "ready" ? <p className="notice">{t.unavailable}</p> : <>
        <article className="ranking-card"><ol className="ranking-list">{current.items.map(item => <TrendingCard key={item.subject_id} item={item} locale={locale}/>)}</ol></article>
        <div className="pagination">
          <span className="result-count">{t.showing(current.items.length, current.total)}</span>
          {current.total > current.items.length && limit < EXPANDED_LIMIT && <button type="button" onClick={() => setLimit(EXPANDED_LIMIT)}>{t.showTop(EXPANDED_LIMIT)}</button>}
          {limit > DEFAULT_LIMIT && <button type="button" onClick={() => setLimit(DEFAULT_LIMIT)}>{t.showTop20}</button>}
        </div>
      </>}
    </section>}
  </div></main>;
}
