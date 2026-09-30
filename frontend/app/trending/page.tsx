"use client";

import { useEffect, useState } from "react";

import { DiscoverTabs, DomainTabs, InfoNote, isDomain, listWindowLabel, PageState, TrendingEventCard } from "../../components/shared";
import { fetchTrending, type Domain, type TrendingDomain } from "../../lib/api";
import { common, formatDateTime, useLocale } from "../../lib/i18n";

const DEFAULT_LIMIT = 20;
const EXPANDED_LIMIT = 50;

const copy = {
  en: {
    eyebrow: "Discover", title: "Trending",
    intro: "Events reported by the articles that publishers list on their own popularity rankings.",
    rankedLists: "Publisher lists", observed: "observed",
    methodologyText: [
      "Each card is an event. Its reports appear on publishers' own ranked lists, such as a click ranking or a popular-posts list. The number shown (#2) is the publisher's own position in that list, exactly as published.",
      "Ranks from different publishers are not comparable and are never combined. There is no platform popularity, trend or hotness score.",
      "Each list keeps its publisher's own name and time scope: a daily list covers the publisher's calendar day so far (新浪财经 uses the Beijing date), and lists that state no period, such as Popular Posts or 近期热门, are not given one.",
      "Display order: the lists in a fixed order, each in its publisher's own order; an event listed more than once appears once, at its first listing, with every rank shown. The order is not our judgement of popularity.",
      "Topics are subjects linked to the event on exact evidence. They are context, not what is trending.",
      "Lists that rank games, coins, stocks or repositories rather than articles appear on Today, not here.",
    ],
    pending: (count: number) => `${count} listed ${count === 1 ? "report is" : "reports are"} not grouped into an event yet.`,
    showing: (shown: number, total: number) => `Showing ${shown} of ${total} events`,
    showMore: (count: number) => `Show up to ${count}`, showFewer: "Show fewer",
    unavailable: "No publisher's ranked news list is collected for this domain yet.",
    empty: "No listed report has been grouped into an event yet.",
  },
  "zh-CN": {
    eyebrow: "发现", title: "热门",
    intro: "发布方在自有热门榜单中列出的文章所报道的事件。",
    rankedLists: "发布方榜单", observed: "采集于",
    methodologyText: [
      "每张卡片是一个事件，其报道出现在发布方自有的排行榜上，例如点击量排行或热门文章列表。显示的名次（#2）是该文章在发布方榜单中的原始位置，按原样保留。",
      "不同发布方的名次不可比较，也从不合并。平台不计算任何热度、趋势或受欢迎程度分数。",
      "每个榜单保留发布方自己的名称与时间范围：日榜覆盖发布方当日至今（新浪财经按北京时间日期），未注明时间范围的榜单（如 Popular Posts、近期热门）不另加时间范围。",
      "展示顺序：各榜单按固定顺序排列，每个榜单内保持发布方自己的顺序；同一事件多次上榜只显示一次（位于首次出现处），并列出全部名次。该顺序不代表我们对热度的判断。",
      "主题是依据确切证据与事件关联的对象，仅作为背景信息，并非热门本身。",
      "对游戏、加密货币、股票或代码仓库（而非文章）排名的榜单显示在“今日”页面，不在此处。",
    ],
    pending: (count: number) => `${count} 篇上榜报道尚未归入事件。`,
    showing: (shown: number, total: number) => `显示 ${shown} / ${total} 个事件`,
    showMore: (count: number) => `最多显示 ${count} 个`, showFewer: "收起",
    unavailable: "该领域尚未采集发布方的新闻排行榜。",
    empty: "上榜报道尚未归入事件。",
  },
} as const;

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
  const pending = current ? current.lists.reduce((sum, list) => sum + list.pending_event_count, 0) : 0;
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="trending" domain={domain} locale={locale}/>
    {domain && <DomainTabs value={domain} onChange={select} locale={locale}/>}
    <p className="intro">{t.intro}</p>
    <InfoNote title={common[locale].methodology}>
      {t.methodologyText.map(text => <p key={text}>{text}</p>)}
    </InfoNote>
    <PageState loading={!current} error={error} locale={locale}/>
    {current && <section className="domain" aria-labelledby="trending-heading">
      <div className="domain-heading"><h2 id="trending-heading">{common[locale].shortDomains[current.domain]}</h2></div>
      {current.lists.length > 0 && <ul className="trending-lists" aria-label={t.rankedLists}>
        {current.lists.map(list => {
          const stated = listWindowLabel(list.window, locale);
          return <li key={list.source_key}><strong>{list.source}</strong> · {list.list_name}{stated && ` (${stated})`} · {t.observed} <time dateTime={list.observed_at}>{formatDateTime(list.observed_at, locale)}</time></li>;
        })}
      </ul>}
      {current.status !== "ready" ? <p className="notice">{t.unavailable}</p> : current.items.length === 0 ? <p className="notice">{t.empty}</p> : <>
        <article className="ranking-card"><ul className="trending-events">{current.items.map(item => <TrendingEventCard key={item.event.id} item={item} locale={locale}/>)}</ul></article>
        <div className="pagination">
          <span className="result-count">{t.showing(current.items.length, current.total)}</span>
          {current.total > current.items.length && limit < EXPANDED_LIMIT && <button type="button" onClick={() => setLimit(EXPANDED_LIMIT)}>{t.showMore(EXPANDED_LIMIT)}</button>}
          {limit > DEFAULT_LIMIT && <button type="button" onClick={() => setLimit(DEFAULT_LIMIT)}>{t.showFewer}</button>}
        </div>
      </>}
      {pending > 0 && <p className="section-note">{t.pending(pending)}</p>}
    </section>}
  </div></main>;
}
