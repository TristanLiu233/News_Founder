"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ExternalLink, InfoNote, NewsList, PageState, RelatedEvents } from "../../../components/shared";
import { ApiError, fetchCompany, fetchNews, type CompanyDetail, type NewsPage } from "../../../lib/api";
import { common, formatDateTime, useLocale } from "../../../lib/i18n";

const copy = {
  en: {eyebrow: "Company", notFound: "Company not found", notFoundNote: "No company exists at this address.", profile: "Profile", legal: "Legal name", aliases: "Also known as", website: "Website", country: "Country / region", headquarters: "Headquarters", industry: "Industry", overview: "Overview", noDescription: "The company's website publishes no short description.", segments: "Business Segments & Products", segmentsLead: "As tagged in the company's latest annual filing with the SEC.", areas: "Business segments", products: "Products and services", rankings: "External Rankings", inRankings: (count: number) => `In ${count} external ${count === 1 ? "ranking" : "rankings"}`, rankingsNote: "Shown as published by each ranking's publisher; not a platform score or recommendation.", viewRanking: "Open ranking", notInLatest: "Not in the latest list", relatedNews: "Related News", relatedTopics: "Related Topics", sources: "Profile Sources", usedFor: "Used for", checked: "Checked", noProfile: "Only ranking information is available for this company so far.", dataDetails: "Data details", identifiers: "Identifiers", fieldSources: "Where each profile field comes from", segmentNote: "Segment and product labels are the company's own tags in its filing data; some tags are accounting or reconciling items rather than business divisions.", linkNote: "A company is linked to a topic only by an exact exchange code or a unique exact name.", fields: {legal_name: "legal name", website: "website", country: "country", headquarters: "headquarters", jurisdiction: "jurisdiction", industry: "industry", categories: "categories", description: "overview", business_areas: "business segments", key_products: "products and services"} as Record<string, string>, schemes: {"companiesmarketcap:symbol": "Ticker (CompaniesMarketCap)", "sec:cik": "SEC CIK", "gleif:lei": "LEI", "wikipedia:en": "Wikipedia article"} as Record<string, string>},
  "zh-CN": {eyebrow: "公司", notFound: "未找到该公司", notFoundNote: "该地址没有对应的公司。", profile: "公司资料", legal: "法定名称", aliases: "其他名称", website: "官方网站", country: "国家 / 地区", headquarters: "总部", industry: "行业", overview: "概况", noDescription: "公司官网未发布简短介绍。", segments: "业务分部与产品", segmentsLead: "取自公司向美国证券交易委员会（SEC）提交的最新年报中的标注。", areas: "业务分部", products: "产品与服务", rankings: "外部排行榜", inRankings: (count: number) => `出现在 ${count} 个外部排行榜`, rankingsNote: "按各榜单发布方原样展示，不代表平台评分或推荐。", viewRanking: "查看榜单", notInLatest: "不在最新榜单中", relatedNews: "相关新闻", relatedTopics: "相关主题", sources: "资料来源", usedFor: "用于", checked: "最近核对", noProfile: "该公司目前仅有排行榜信息。", dataDetails: "数据详情", identifiers: "外部标识", fieldSources: "各资料字段的来源", segmentNote: "业务分部与产品标签是公司在申报数据中的自有标注，部分标注属于会计或调节项目，并非实际业务部门。", linkNote: "公司仅在交易所代码完全一致或名称唯一且完全一致时与主题关联。", fields: {legal_name: "法定名称", website: "官方网站", country: "国家 / 地区", headquarters: "总部", jurisdiction: "注册地", industry: "行业", categories: "分类", description: "概况", business_areas: "业务分部", key_products: "产品与服务"} as Record<string, string>, schemes: {"companiesmarketcap:symbol": "股票代码（CompaniesMarketCap）", "sec:cik": "SEC CIK", "gleif:lei": "LEI", "wikipedia:en": "维基百科条目"} as Record<string, string>},
} as const;

function Chips({items}: {items: string[]}) { return <ul className="chips">{items.map(item => <li key={item}>{item}</li>)}</ul>; }

export default function CompanyPage() {
  const params = useParams<{id: string}>();
  const {locale} = useLocale(); const t = copy[locale]; const c = common[locale];
  const id = params.id; const [company, setCompany] = useState<CompanyDetail>();
  const [news, setNews] = useState<NewsPage>(); const [error, setError] = useState<string>(); const [notFound, setNotFound] = useState(false);
  useEffect(() => { if (!id) return; const controller = new AbortController(); fetchCompany(id, controller.signal).then(value => { setCompany(value); if (value.redirected) window.history.replaceState(null, "", `/company/${value.id}`); }).catch(reason => { if (controller.signal.aborted) return; if (reason instanceof ApiError && reason.status === 404) setNotFound(true); else setError(reason instanceof Error ? reason.message : String(reason)); }); return () => controller.abort(); }, [id]);
  useEffect(() => { if (!company) return; const controller = new AbortController(); fetchNews({company_id: company.id, limit: 10}, controller.signal).then(setNews).catch(() => setNews({items: [], limit: 10, offset: 0})); return () => controller.abort(); }, [company]);
  if (notFound) return <main><div className="shell"><p className="eyebrow">{t.eyebrow}</p><h1>{t.notFound}</h1><p className="notice">{t.notFoundNote}</p></div></main>;
  const p = company?.profile;
  const sep = locale === "zh-CN" ? "：" : ": ";
  const fieldList = (fields: string[]) => fields.map(field => t.fields[field] ?? field).join(locale === "zh-CN" ? "、" : ", ");
  const shownNames = company ? new Set([company.canonical_name, company.display_names.en, company.display_names["zh-CN"], p?.legal_name]) : new Set();
  const otherNames = company ? [...new Set(company.aliases.map(item => item.alias))].filter(name => !shownNames.has(name)) : [];
  const sourceNames = new Map(company?.profile_sources.map(source => [source.source_key, source.source_name]) ?? []);
  return <main><div className="shell"><PageState loading={!company && !error} error={error} locale={locale}/>{company && <>
    <p className="eyebrow detail-eyebrow">{t.eyebrow}</p><h1 className="subject-title">{company.display_names[locale]}</h1>{company.display_names[locale] !== company.canonical_name && <p className="intro">{company.canonical_name}</p>}
    <p className="detail-meta">{company.domains.map(domain => <span key={domain}>{c.shortDomains[domain]}</span>)}{company.current_rankings.length > 0 && <span>{t.inRankings(company.current_rankings.length)}</span>}</p>
    {p ? <div className="detail-grid">
      <section className="ranking-card" aria-labelledby="company-overview"><h2 id="company-overview">{t.overview}</h2>{p.description ? <p lang={p.description_language ?? undefined}>{p.description}</p> : <p className="empty">{t.noDescription}</p>}{p.categories.length > 0 && <Chips items={p.categories}/>}</section>
      <section className="ranking-card" aria-labelledby="company-profile"><h2 id="company-profile">{t.profile}</h2><dl className="facts">{p.legal_name && <><dt>{t.legal}</dt><dd lang={p.legal_name_locale ?? undefined}>{p.legal_name}</dd></>}{p.website && <><dt>{t.website}</dt><dd><ExternalLink href={p.website} locale={locale}>{new URL(p.website).hostname}</ExternalLink></dd></>}{p.country && <><dt>{t.country}</dt><dd>{p.country}</dd></>}{p.headquarters && <><dt>{t.headquarters}</dt><dd>{p.headquarters}</dd></>}{p.industry && <><dt>{t.industry}</dt><dd>{p.industry}</dd></>}{otherNames.length > 0 && <><dt>{t.aliases}</dt><dd>{otherNames.join(" · ")}</dd></>}</dl></section>
    </div> : <p className="notice">{t.noProfile}</p>}
    {company.current_rankings.length > 0 && <section className="detail-section" aria-labelledby="company-rankings"><h2 id="company-rankings" className="section-title">{t.rankings}</h2><p className="section-note">{t.rankingsNote}</p><div className="card-grid">{company.current_rankings.map(item => <article className="ranking-card compact" key={item.ranking.key}><h3 className="card-title" lang={item.ranking.primary_language}>{item.ranking.name}</h3><p className="big-rank">{item.latest_rank ? `#${item.latest_rank}` : <span className="meta">{t.notInLatest}</span>}</p><p className="meta">{item.ranking.publisher}{item.effective_at && ` · ${formatDateTime(item.effective_at, locale)}`}</p><p className="card-footer"><Link href={`/rankings?domain=${item.ranking.domain}&ranking=${item.ranking.key}`}>{t.viewRanking} →</Link></p></article>)}</div></section>}
    <RelatedEvents filter={{company_id: company.id}} locale={locale} hideWhenEmpty/>
    {news && news.items.length > 0 && <section className="detail-section" aria-labelledby="company-news"><h2 id="company-news" className="section-title">{t.relatedNews}</h2><div className="ranking-card"><NewsList items={news.items} locale={locale}/></div></section>}
    {company.related_subjects.length > 0 && <section className="detail-section" aria-labelledby="company-topics"><h2 id="company-topics" className="section-title">{t.relatedTopics}</h2><ul className="chips">{company.related_subjects.map(subject => <li key={subject.id}><Link href={`/subject/${subject.id}`}>{subject.title}</Link></li>)}</ul></section>}
    {p && (p.business_areas.length > 0 || p.key_products.length > 0) && <section className="detail-section" aria-labelledby="company-segments"><h2 id="company-segments" className="section-title">{t.segments}</h2><p className="section-note">{t.segmentsLead}</p><div className="detail-grid">
      {p.business_areas.length > 0 && <div className="ranking-card"><h3 className="card-title">{t.areas}</h3><Chips items={p.business_areas}/></div>}
      {p.key_products.length > 0 && <div className="ranking-card"><h3 className="card-title">{t.products}</h3><Chips items={p.key_products}/></div>}
    </div></section>}
    {company.profile_sources.length > 0 && <section className="detail-section" aria-labelledby="company-sources"><h2 id="company-sources" className="section-title">{t.sources}</h2><div className="card-grid">{company.profile_sources.map(source => <article className="ranking-card compact" key={source.source_key}><h3 className="card-title">{source.source_name}</h3><p><ExternalLink href={source.source_url} locale={locale}>{new URL(source.source_url).hostname}</ExternalLink></p>{source.used_fields.length > 0 && <p className="meta">{t.usedFor}{sep}{fieldList(source.used_fields)}</p>}<p className="meta">{t.checked}{sep}{formatDateTime(source.checked_at, locale)}</p></article>)}</div></section>}
    <div className="detail-about">
      <InfoNote title={t.dataDetails}>
        {company.identities.length > 0 && <p>{t.identifiers}{sep}{company.identities.map(item => `${t.schemes[item.scheme] ?? item.scheme} ${item.value}`).join(" · ")}</p>}
        {p && Object.keys(p.field_sources).length > 0 && <p>{t.fieldSources}{sep}{Object.entries(p.field_sources).map(([field, source]) => `${t.fields[field] ?? field} — ${sourceNames.get(source) ?? source}`).join(" · ")}</p>}
        {p && (p.business_areas.length > 0 || p.key_products.length > 0) && <p>{t.segmentNote}</p>}
        <p>{t.linkNote}</p>
      </InfoNote>
    </div>
  </>}</div></main>;
}
