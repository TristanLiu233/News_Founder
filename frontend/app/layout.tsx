import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppHeader } from "../components/AppHeader";
import { LocaleProvider } from "../lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "Industry Intelligence & Ranking Platform",
  description: "Source-native news, cross-platform attention rankings and multilingual search.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <LocaleProvider>
          <AppHeader />
          {children}
        </LocaleProvider>
      </body>
    </html>
  );
}
