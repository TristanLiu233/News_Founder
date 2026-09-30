"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type Locale = "en" | "zh-CN";

/**
 * Strings shared by the header and shared components. Page-specific strings stay in
 * each page's own dictionary; every entry needs both `en` and `zh-CN`.
 */
export const common = {
  en: {
    brand: "Industry Intelligence",
    primaryNav: "Primary navigation",
    nav: {discover: "Discover", today: "Today", trending: "Trending", search: "Search", rankings: "Rankings", companies: "Companies", events: "Events"},
    discoverNav: "Discover sections",
    all: "All",
    methodology: "Methodology",
    /** User-facing labels for `SubjectType` machine values (a Subject is shown as a "topic"). */
    subjectTypes: {event: "Story", company: "Company", stock: "Asset", game: "Game", repository: "Repository", paper: "Paper", model: "Model", technology: "Technology"} as Record<string, string>,
    switchLanguage: "切换到中文",
    otherLocaleLabel: "中文",
    loading: "Loading…",
    unavailable: "Backend unavailable. Please try again later.",
    domainFilter: "Domain",
    domains: {computer: "Computer / AI / Technology", finance: "Finance", gaming: "Gaming"},
    shortDomains: {computer: "Computer", finance: "Finance", gaming: "Gaming"},
    marketPulse: "Market Pulse",
    trend: {up: "Up", down: "Down", unchanged: "Unchanged", new: "New"},
    newLabel: "New",
    sources: (count: number) => `${count} ${count === 1 ? "source" : "sources"}`,
    newTab: "(opens original in a new tab)",
    viewMore: "View More",
    showLess: "Show Less",
    languages: {en: "English", "zh-CN": "Chinese"} as Record<string, string>,
    eventTypes: {earnings: "Earnings", product_launch: "Product launch", acquisition: "Acquisition", funding: "Funding", regulatory: "Regulatory / legal", leadership: "Leadership", partnership: "Partnership", security_incident: "Security incident", game_release: "Game release", general: "General"} as Record<string, string>,
    reports: (count: number) => `${count} ${count === 1 ? "report" : "reports"}`,
    multipleSources: "Multiple sources",
    firstReported: "First reported",
    lastReported: "Last reported",
    relatedEvents: "Related Events",
    noRelatedEvents: "No related events yet.",
    viewAllEvents: (count: number) => `View all ${count} events`,
    subjectTag: "Topic",
    publisherRanks: "Publisher rankings",
    rankWord: "rank",
    movement: {
      up: (delta: number) => `Up ${delta} since the previous snapshot of this list`,
      down: (delta: number) => `Down ${delta} since the previous snapshot of this list`,
      stable: "Same position as in the previous snapshot of this list",
      new: "Not on the previous snapshot of this list",
      reentered: "Back on this list after dropping off",
    },
    newBadge: "NEW", backBadge: "BACK",
    onLists: (lists: number, publishers: number) => `On ${lists} publisher lists${publishers !== lists ? ` from ${publishers} ${publishers === 1 ? "publisher" : "publishers"}` : ""}`,
    relatedTopics: "Related topics",
    /** The publisher's own daily list (e.g. Sina `top_type=day`) for a Beijing calendar date; never a rolling 24 h window. */
    dailyList: (date: string) => `daily list, Beijing date ${date}`,
    companyTag: "Company",
    aiSummaryTag: "AI summary",
    excerptTag: (source: string) => `Excerpt · ${source}`,
  },
  "zh-CN": {
    brand: "行业情报",
    primaryNav: "主导航",
    nav: {discover: "发现", today: "今日", trending: "热门", search: "搜索", rankings: "排行榜", companies: "公司", events: "事件"},
    discoverNav: "发现栏目",
    all: "全部",
    methodology: "计算方法",
    subjectTypes: {event: "文章", company: "公司", stock: "资产", game: "游戏", repository: "代码仓库", paper: "论文", model: "模型", technology: "技术"} as Record<string, string>,
    switchLanguage: "Switch to English",
    otherLocaleLabel: "EN",
    loading: "加载中…",
    unavailable: "后端服务暂不可用，请稍后重试。",
    domainFilter: "领域",
    domains: {computer: "计算机 / AI / 科技", finance: "金融", gaming: "游戏"},
    shortDomains: {computer: "计算机", finance: "金融", gaming: "游戏"},
    marketPulse: "市场动态",
    trend: {up: "上升", down: "下降", unchanged: "不变", new: "新上榜"},
    newLabel: "新",
    sources: (count: number) => `${count} 个来源`,
    newTab: "（在新标签页打开原文）",
    viewMore: "查看更多",
    showLess: "收起",
    languages: {en: "英文", "zh-CN": "中文"} as Record<string, string>,
    eventTypes: {earnings: "财报业绩", product_launch: "产品发布", acquisition: "收购并购", funding: "融资", regulatory: "监管 / 司法", leadership: "人事变动", partnership: "合作", security_incident: "安全事件", game_release: "游戏发售", general: "一般"} as Record<string, string>,
    reports: (count: number) => `${count} 篇报道`,
    multipleSources: "多来源",
    firstReported: "首次报道",
    lastReported: "最近报道",
    relatedEvents: "相关事件",
    noRelatedEvents: "暂无相关事件。",
    viewAllEvents: (count: number) => `查看全部 ${count} 个事件`,
    subjectTag: "主题",
    publisherRanks: "发布方榜单排名",
    rankWord: "排名",
    movement: {
      up: (delta: number) => `较该榜单上一次采集上升 ${delta} 位`,
      down: (delta: number) => `较该榜单上一次采集下降 ${delta} 位`,
      stable: "与该榜单上一次采集名次相同",
      new: "该榜单上一次采集中未出现",
      reentered: "曾跌出该榜单，现重新上榜",
    },
    newBadge: "新上榜", backBadge: "重新上榜",
    onLists: (lists: number, publishers: number) => `登上 ${lists} 个发布方榜单${publishers !== lists ? `（${publishers} 个发布方）` : ""}`,
    relatedTopics: "相关主题",
    dailyList: (date: string) => `日榜，北京时间 ${date}`,
    companyTag: "公司",
    aiSummaryTag: "AI 摘要",
    excerptTag: (source: string) => `摘录 · ${source}`,
  },
} as const;

const LocaleContext = createContext<{locale: Locale; setLocale: (value: Locale) => void}>({
  locale: "en",
  setLocale: () => undefined,
});

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");
  useEffect(() => {
    const saved = localStorage.getItem("locale");
    if (saved === "en" || saved === "zh-CN") setLocaleState(saved);
  }, []);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const setLocale = useCallback((value: Locale) => {
    setLocaleState(value);
    localStorage.setItem("locale", value);
  }, []);
  return <LocaleContext.Provider value={{locale, setLocale}}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  return useContext(LocaleContext);
}

export function formatDateTime(value: string | null | undefined, locale: Locale) {
  if (!value) return "—";
  return new Date(value).toLocaleString(locale, {dateStyle: "medium", timeStyle: "short"});
}
