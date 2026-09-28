"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { common, useLocale } from "../lib/i18n";

/** Discover groups the existing Today, Trending and Search routes; the brand links Home. */
const links = [
  {href: "/today", key: "discover", match: ["/today", "/trending", "/search"]},
  {href: "/events", key: "events", match: ["/events"]},
  {href: "/rankings", key: "rankings", match: ["/rankings"]},
  {href: "/companies", key: "companies", match: ["/companies"]},
] as const;

export function AppHeader() {
  const pathname = usePathname();
  const { locale, setLocale } = useLocale();
  const t = common[locale];
  const other = locale === "en" ? "zh-CN" : "en";
  return <header className="app-header"><div className="shell header-inner">
    <Link href="/" className="brand" aria-current={pathname === "/" ? "page" : undefined}>{t.brand}</Link>
    <nav aria-label={t.primaryNav}><ul>
      {links.map(link => {
        const active = link.match.some(prefix => pathname === prefix || pathname.startsWith(`${prefix}/`));
        return <li key={link.href}><Link href={link.href} aria-current={active ? "page" : undefined}>{t.nav[link.key]}</Link></li>;
      })}
    </ul></nav>
    <button type="button" className="locale-toggle" lang={other} aria-label={t.switchLanguage} onClick={() => setLocale(other)}>{t.otherLocaleLabel}</button>
  </div></header>;
}
