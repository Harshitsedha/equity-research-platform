import type { Metadata } from "next";
import { JetBrains_Mono, Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

// Mono for ALL data/numerics; tight sans for labels/nav (design system).
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});
const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Equity Research — Coverage Terminal",
  description: "Read-only viewer over the immutable research ledger.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${mono.variable} ${sans.variable}`}>
      <body className="min-h-screen bg-bg text-fg">
        <header className="border-b border-border">
          <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-2">
            <Link
              href="/stocks"
              className="font-mono text-sm font-semibold tracking-tight !text-fg hover:!no-underline"
            >
              ERP<span className="text-fg-dim">/coverage</span>
            </Link>
            <nav className="font-sans text-xs uppercase tracking-wider text-fg-dim">
              <Link href="/stocks">stocks</Link>
            </nav>
            <span className="ml-auto font-mono text-xs text-fg-dim">
              read-only · immutable ledger
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
