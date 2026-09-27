"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { common, useLocale } from "../lib/i18n";

const links = [
  {href: "/", key: "home"},
  {href: "/today", key: "today"},
  {href: "/trending", key: "trending"},
  {href: "/search", key: "search"},
  {href: "/rankings", key: "rankings"},
] as const;

export function AppHeader() {
  const pathname = usePathname();
  const { locale, setLocale } = useLocale();
  const t = common[locale];
  const other = locale === "en" ? "zh-CN" : "en";
  return <header className="app-header"><div className="shell header-inner">
    <Link href="/" className="brand">{t.brand}</Link>
    <nav aria-label={t.primaryNav}><ul>
      {links.map(link => {
        const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return <li key={link.href}><Link href={link.href} aria-current={active ? "page" : undefined}>{t.nav[link.key]}</Link></li>;
      })}
    </ul></nav>
    <button type="button" className="locale-toggle" lang={other} aria-label={t.switchLanguage} onClick={() => setLocale(other)}>{t.otherLocaleLabel}</button>
  </div></header>;
}
