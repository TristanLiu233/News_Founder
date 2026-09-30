"use client";

import { useEffect, useState } from "react";

import { DiscoverTabs, DomainTabs, InfoNote, isDomain, listWindowLabel, PageState, TrendingEventCard } from "../../components/shared";
import { fetchTrending, type Domain, type TrendingDomain, type TrendingView } from "../../lib/api";
import { common, formatDateTime, useLocale } from "../../lib/i18n";

const DEFAULT_LIMIT = 20;
const EXPANDED_LIMIT = 50;

const copy = {
  en: {
    eyebrow: "Discover", title: "Trending",
    intro: {
      aggregate: "Popular across selected sources: current events whose reports are on publishers' own popularity lists.",
      native: "Each selected publisher's own popularity list, in the publisher's own order.",
    },
    viewLabel: "View", views: {aggregate: "Combined", native: "By publisher list"},
    rankedLists: "Publisher lists", observed: "observed",
    comparedWith: "movement vs.", noMovement: {no_previous_snapshot: "first collection, no movement yet", window_changed: "new day's list, no movement yet", list_changed: "list changed, no movement"},
    methodologyText: [
      "Each card is an event. Its reports appear on publishers' own ranked lists, such as a click ranking or a popular-posts list. The number shown (#2) is the publisher's own position in that list, exactly as published: it is the reason the event is here.",
      "Combined order: events are placed by their best publisher position, and an event that another publisher lists as well moves up three places for each additional publisher (at most two). Ties go to the event on more publishers' lists, then to the newer report. This is a simple display order, not a popularity, importance or quality score, and no score is shown.",
      "The combined view keeps current events only: the event's latest report is at most 48 hours older than the list that lists it. Older listed events stay visible under By publisher list.",
      "Reports are combined into one card only when they already belong to the same event; similar headlines about different events stay separate cards.",
      "Each list keeps its publisher's own name and time scope: a daily list covers the publisher's calendar day so far (新浪财经 uses the Beijing date), and lists that state no period, such as Popular Posts or 近期热门, are not given one.",
      "By publisher list shows every list in a fixed order, each in its publisher's own order, with the change since the previous collection of the same list (↑3, ↓2, —, NEW). Positions are only compared within one list.",
      "Topics are subjects linked to the event on exact evidence. They are context, not what is trending.",
      "Lists that rank games, coins, stocks or repositories rather than articles appear on Today, not here.",
    ],
    excluded: (count: number, hours: number) => `${count} older listed ${count === 1 ? "event is" : "events are"} not shown (latest report more than ${hours} hours before the list).`,
    showAllLists: "See every publisher list",
    pending: (count: number) => `${count} listed ${count === 1 ? "report is" : "reports are"} not grouped into an event yet.`,
    showing: (shown: number, total: number) => `Showing ${shown} of ${total} events`,
    showMore: (count: number) => `Show up to ${count}`, showFewer: "Show fewer",
    unavailable: "No publisher's ranked news list is collected for this domain yet.",
    empty: "No listed report has been grouped into an event yet.",
    emptyCurrent: "No listed event is current right now.",
  },
  "zh-CN": {
    eyebrow: "发现", title: "热门",
    intro: {
      aggregate: "来自选定来源的热门内容：其报道登上发布方自有热门榜单的近期事件。",
      native: "各选定发布方自有的热门榜单，保持发布方自己的顺序。",
    },
    viewLabel: "视图", views: {aggregate: "综合", native: "按发布方榜单"},
    rankedLists: "发布方榜单", observed: "采集于",
    comparedWith: "名次变化对比", noMovement: {no_previous_snapshot: "首次采集，暂无名次变化", window_changed: "新一天的榜单，暂无名次变化", list_changed: "榜单已变更，不比较名次"},
    methodologyText: [
      "每张卡片是一个事件，其报道出现在发布方自有的排行榜上，例如点击量排行或热门文章列表。显示的名次（#2）是该文章在发布方榜单中的原始位置，按原样保留，也是该事件出现在这里的依据。",
      "综合顺序：事件按其在发布方榜单中的最佳名次排列；若另有发布方也将其列入榜单，每多一个发布方前移三位（最多两个）。顺序相同时，出现在更多发布方榜单的事件在前，其次是报道更新的事件。这只是简单的展示顺序，不是热度、重要性或质量评分，也不显示任何分数。",
      "综合视图只保留近期事件：事件最新报道的发布时间距列入它的榜单采集时间不超过 48 小时。较早的上榜事件仍可在“按发布方榜单”中查看。",
      "只有已属于同一事件的报道才会合并为一张卡片；标题相近但属于不同事件的报道保持为不同卡片。",
      "每个榜单保留发布方自己的名称与时间范围：日榜覆盖发布方当日至今（新浪财经按北京时间日期），未注明时间范围的榜单（如 Popular Posts、近期热门）不另加时间范围。",
      "“按发布方榜单”按固定顺序列出每个榜单，每个榜单内保持发布方自己的顺序，并显示与同一榜单上一次采集相比的名次变化（↑3、↓2、—、新上榜）。名次只在同一榜单内比较。",
      "主题是依据确切证据与事件关联的对象，仅作为背景信息，并非热门本身。",
      "对游戏、加密货币、股票或代码仓库（而非文章）排名的榜单显示在“今日”页面，不在此处。",
    ],
    excluded: (count: number, hours: number) => `${count} 个较早的上榜事件未显示（最新报道早于榜单采集时间 ${hours} 小时以上）。`,
    showAllLists: "查看各发布方榜单",
    pending: (count: number) => `${count} 篇上榜报道尚未归入事件。`,
    showing: (shown: number, total: number) => `显示 ${shown} / ${total} 个事件`,
    showMore: (count: number) => `最多显示 ${count} 个`, showFewer: "收起",
    unavailable: "该领域尚未采集发布方的新闻排行榜。",
    empty: "上榜报道尚未归入事件。",
    emptyCurrent: "目前没有近期的上榜事件。",
  },
} as const;

const VIEWS: TrendingView[] = ["aggregate", "native"];

function trendingUrl(domain: Domain, view: TrendingView) {
  return `/trending?domain=${domain}${view === "native" ? "&view=native" : ""}`;
}

export default function TrendingPage() {
  const { locale } = useLocale();
  const [domain, setDomain] = useState<Domain>();
  const [view, setView] = useState<TrendingView>("aggregate");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [data, setData] = useState<TrendingDomain>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const restore = () => {
      const params = new URLSearchParams(window.location.search);
      const value = params.get("domain");
      setDomain(isDomain(value) ? value : "computer");
      setView(params.get("view") === "native" ? "native" : "aggregate");
      setLimit(DEFAULT_LIMIT);
    };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (!domain) return;
    const controller = new AbortController();
    setError(undefined);
    fetchTrending(controller.signal, {domain, limit, view})
      .then(response => setData(response.domains[0]))
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [domain, limit, view]);
  function select(value: Domain) {
    setDomain(value); setLimit(DEFAULT_LIMIT); setData(undefined);
    window.history.pushState(null, "", trendingUrl(value, view));
  }
  function choose(value: TrendingView) {
    if (!domain || value === view) return;
    setView(value); setLimit(DEFAULT_LIMIT); setData(undefined);
    window.history.pushState(null, "", trendingUrl(domain, value));
  }
  const t = copy[locale];
  const current = data && data.domain === domain && data.view === view ? data : undefined;
  const pending = current ? current.lists.reduce((sum, list) => sum + list.pending_event_count, 0) : 0;
  const native = view === "native";
  const excluded = current?.eligibility?.excluded_count ?? 0;
  return <main><div className="shell">
    <p className="eyebrow">{t.eyebrow}</p><h1>{t.title}</h1>
    <DiscoverTabs current="trending" domain={domain} locale={locale}/>
    {domain && <DomainTabs value={domain} onChange={select} locale={locale}/>}
    <div className="domain-tabs view-tabs" role="group" aria-label={t.viewLabel}>
      {VIEWS.map(value => <button key={value} type="button" aria-pressed={value === view} onClick={() => choose(value)}>{t.views[value]}</button>)}
    </div>
    <p className="intro">{t.intro[view]}</p>
    <InfoNote title={common[locale].methodology}>
      {t.methodologyText.map(text => <p key={text}>{text}</p>)}
    </InfoNote>
    <PageState loading={!current} error={error} locale={locale}/>
    {current && <section className="domain" aria-labelledby="trending-heading">
      <div className="domain-heading"><h2 id="trending-heading">{common[locale].shortDomains[current.domain]}</h2></div>
      {current.lists.length > 0 && <ul className="trending-lists" aria-label={t.rankedLists}>
        {current.lists.map(list => {
          const stated = listWindowLabel(list.window, locale);
          return <li key={list.source_key}><strong>{list.source}</strong> · {list.list_name}{stated && ` (${stated})`} · {t.observed} <time dateTime={list.observed_at}>{formatDateTime(list.observed_at, locale)}</time>
            {native && <>{" · "}{list.previous_observed_at ? <>{t.comparedWith} <time dateTime={list.previous_observed_at}>{formatDateTime(list.previous_observed_at, locale)}</time></> : list.movement_unavailable_reason && t.noMovement[list.movement_unavailable_reason]}</>}
          </li>;
        })}
      </ul>}
      {current.status !== "ready" ? <p className="notice">{t.unavailable}</p> : current.items.length === 0 ? <p className="notice">{excluded > 0 ? t.emptyCurrent : t.empty}</p> : <>
        <article className="ranking-card"><ul className="trending-events">{current.items.map(item => <TrendingEventCard key={item.event.id} item={item} locale={locale} showMovement={native}/>)}</ul></article>
        <div className="pagination">
          <span className="result-count">{t.showing(current.items.length, current.total)}</span>
          {current.total > current.items.length && limit < EXPANDED_LIMIT && <button type="button" onClick={() => setLimit(EXPANDED_LIMIT)}>{t.showMore(EXPANDED_LIMIT)}</button>}
          {limit > DEFAULT_LIMIT && <button type="button" onClick={() => setLimit(DEFAULT_LIMIT)}>{t.showFewer}</button>}
        </div>
      </>}
      {!native && excluded > 0 && current.eligibility && <p className="section-note">{t.excluded(excluded, current.eligibility.max_report_age_hours)} <button type="button" className="link-button" onClick={() => choose("native")}>{t.showAllLists}</button></p>}
      {pending > 0 && <p className="section-note">{t.pending(pending)}</p>}
    </section>}
  </div></main>;
}
