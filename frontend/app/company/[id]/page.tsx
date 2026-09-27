"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ExternalLink, NewsList, PageState } from "../../../components/shared";
import { ApiError, fetchCompany, fetchNews, type CompanyDetail, type NewsPage } from "../../../lib/api";
import { formatDateTime, useLocale } from "../../../lib/i18n";

const copy = {
  en: {eyebrow: "Company profile", notFound: "Company not found", notFoundNote: "No company exists for this address.", identity: "Identity", canonical: "Canonical name", legal: "Legal name", aliases: "Aliases", website: "Website", country: "Country", headquarters: "Headquarters", industry: "Industry / sector", overview: "Business overview", areas: "Reported business / segment members", products: "Reported product / service members", rankings: "External rankings", history: "Ranking history", rankingsNote: "Publisher rankings shown as published; they are not a platform score or recommendation.", noRankings: "No tracked ranking appearances.", relatedNews: "Related news", noNews: "No related news", sources: "Sources", sourceFields: "Profile fields used", checked: "Checked", relatedSubject: "Related Subject", noSubject: "No deterministic Subject relation.", noProfile: "No sourced profile has been collected for this ranked company yet.", identifiers: "Identifiers"},
  "zh-CN": {eyebrow: "公司资料", notFound: "未找到该公司", notFoundNote: "该地址没有对应的公司。", identity: "身份信息", canonical: "标准名称", legal: "法定名称", aliases: "别名", website: "官方网站", country: "国家 / 地区", headquarters: "总部", industry: "行业 / 领域", overview: "业务概览", areas: "申报的业务 / 分部标签", products: "申报的产品 / 服务标签", rankings: "外部排行榜", history: "排名历史", rankingsNote: "外部榜单按发布方原样展示，不代表平台评分或推荐。", noRankings: "暂无已追踪的榜单记录。", relatedNews: "相关新闻", noNews: "暂无相关新闻", sources: "信息来源", sourceFields: "采用的资料字段", checked: "最近核对", relatedSubject: "关联 Subject", noSubject: "暂无确定性 Subject 关联。", noProfile: "该上榜公司尚未采集有来源的公司资料。", identifiers: "外部标识"},
} as const;

function Chips({items}: {items: string[]}) { return <ul className="chips">{items.map(item => <li key={item}>{item}</li>)}</ul>; }

export default function CompanyPage() {
  const params = useParams<{id: string}>();
  const {locale} = useLocale(); const t = copy[locale];
  const id = params.id; const [company, setCompany] = useState<CompanyDetail>();
  const [news, setNews] = useState<NewsPage>(); const [error, setError] = useState<string>(); const [notFound, setNotFound] = useState(false);
  useEffect(() => { if (!id) return; const controller = new AbortController(); fetchCompany(id, controller.signal).then(value => { setCompany(value); if (value.redirected) window.history.replaceState(null, "", `/company/${value.id}`); }).catch(reason => { if (controller.signal.aborted) return; if (reason instanceof ApiError && reason.status === 404) setNotFound(true); else setError(reason instanceof Error ? reason.message : String(reason)); }); return () => controller.abort(); }, [id]);
  useEffect(() => { if (!company) return; const controller = new AbortController(); fetchNews({company_id: company.id, limit: 10}, controller.signal).then(setNews).catch(() => setNews({items: [], limit: 10, offset: 0})); return () => controller.abort(); }, [company]);
  if (notFound) return <main><div className="shell"><p className="eyebrow">{t.eyebrow}</p><h1>{t.notFound}</h1><p className="notice">{t.notFoundNote}</p></div></main>;
  const p = company?.profile;
  return <main><div className="shell"><PageState loading={!company && !error} error={error} locale={locale}/>{company && <>
    <p className="eyebrow">{t.eyebrow}</p><h1 className="subject-title">{company.display_names[locale]}</h1>{company.display_names[locale] !== company.canonical_name && <p className="intro">{company.canonical_name}</p>}
    <div className="detail-grid"><section className="ranking-card"><h2>{t.identity}</h2><dl className="facts"><dt>{t.canonical}</dt><dd>{company.canonical_name}</dd>{p?.legal_name && <><dt>{t.legal}</dt><dd lang={p.legal_name_locale ?? undefined}>{p.legal_name}</dd></>}{p?.website && <><dt>{t.website}</dt><dd><ExternalLink href={p.website} locale={locale}>{new URL(p.website).hostname}</ExternalLink></dd></>}{p?.country && <><dt>{t.country}</dt><dd>{p.country}</dd></>}{p?.headquarters && <><dt>{t.headquarters}</dt><dd>{p.headquarters}</dd></>}{p?.industry && <><dt>{t.industry}</dt><dd>{p.industry}</dd></>}</dl>
      {company.aliases.length > 0 && <><h3>{t.aliases}</h3><Chips items={company.aliases.map(item => item.alias)}/></>}{company.identities.length > 0 && <details><summary>{t.identifiers}</summary><ul className="breakdown">{company.identities.map(item => <li key={`${item.scheme}:${item.value}`}>{item.scheme}: {item.value}</li>)}</ul></details>}</section>
      <section className="ranking-card"><h2>{t.overview}</h2>{p?.description ? <p lang={p.description_language ?? undefined}>{p.description}</p> : !p ? <p className="empty">{t.noProfile}</p> : <p className="empty">—</p>}{p && p.categories.length > 0 && <Chips items={p.categories}/>}</section>
    </div>
    {p?.business_areas.length ? <section className="detail-section"><h2 className="section-title">{t.areas}</h2><div className="ranking-card"><Chips items={p.business_areas}/></div></section> : null}
    {p?.key_products.length ? <section className="detail-section"><h2 className="section-title">{t.products}</h2><div className="ranking-card"><Chips items={p.key_products}/></div></section> : null}
    <section className="detail-section"><h2 className="section-title">{t.rankings}</h2><p className="section-note">{t.rankingsNote}</p>{company.current_rankings.length === 0 ? <p className="empty">{t.noRankings}</p> : <div className="card-grid">{company.current_rankings.map(item => <article className="ranking-card compact" key={item.ranking.key}><h3 lang={item.ranking.primary_language}>{item.ranking.name}</h3><p>{item.ranking.publisher} · {item.latest_rank ? `#${item.latest_rank}` : "—"}</p><Link href={`/rankings?domain=${item.ranking.domain}&ranking=${item.ranking.key}`}>{t.history}</Link></article>)}</div>}</section>
    <section className="detail-section"><h2 className="section-title">{t.relatedNews}</h2>{!news ? <PageState loading locale={locale}/> : news.items.length ? <div className="ranking-card"><NewsList items={news.items} locale={locale}/></div> : <p className="empty">{t.noNews}</p>}</section>
    <section className="detail-section"><h2 className="section-title">{t.relatedSubject}</h2>{company.related_subjects.length ? <ul className="chips">{company.related_subjects.map(subject => <li key={subject.id}><Link href={`/subject/${subject.id}`}>{subject.title}</Link></li>)}</ul> : <p className="empty">{t.noSubject}</p>}</section>
    <section className="detail-section"><h2 className="section-title">{t.sources}</h2>{company.profile_sources.length === 0 ? <p className="empty">{t.noProfile}</p> : <div className="card-grid">{company.profile_sources.map(source => <article className="ranking-card compact" key={source.source_key}><h3>{source.source_name}</h3><p className="meta">{t.checked}: {formatDateTime(source.checked_at, locale)}</p>{source.used_fields.length > 0 && <p>{t.sourceFields}: {source.used_fields.join(", ")}</p>}<ExternalLink href={source.source_url} locale={locale}>{new URL(source.source_url).hostname}</ExternalLink></article>)}</div>}</section>
  </>}</div></main>;
}
