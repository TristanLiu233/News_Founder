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
    nav: {home: "Home", today: "Today", trending: "Trending", search: "Search", rankings: "Rankings", companies: "Companies"},
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
  },
  "zh-CN": {
    brand: "行业情报",
    primaryNav: "主导航",
    nav: {home: "首页", today: "今日", trending: "综合趋势", search: "搜索", rankings: "排行榜", companies: "公司"},
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
