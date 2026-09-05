import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getMessages, getTranslations } from "next-intl/server";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("app");
  return { title: { default: t("name"), template: `%s · ${t("name")}` }, description: t("description") };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const messages = await getMessages();
  const t = await getTranslations("nav");

  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <NextIntlClientProvider messages={messages}>
          {/* First in the tab order, invisible until focused. An evaluator on a
              keyboard should not tab through the whole filter panel to reach a
              result. */}
          <a href="#main" className="skip-link">
            {t("skipToContent")}
          </a>
          <Header />
          <main id="main" className="mx-auto w-full max-w-6xl px-4 py-8">
            {children}
          </main>
          <Footer />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}

async function Header() {
  const t = await getTranslations("nav");
  const app = await getTranslations("app");
  const links = [
    { href: "/", label: t("search") },
    { href: "/domains", label: t("domains") },
    { href: "/submit", label: t("submit") },
    { href: "/connect", label: t("connect") },
    { href: "/developers", label: t("developers") },
  ];

  return (
    <header className="border-b" style={{ borderColor: "var(--border)" }}>
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="font-semibold tracking-tight">
          {app("name")}
        </Link>
        <nav aria-label="Primary" className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-[color:var(--muted)] hover:text-[color:var(--foreground)]"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}

async function Footer() {
  const t = await getTranslations("nav");
  const app = await getTranslations("app");
  return (
    <footer
      className="mt-16 border-t py-8 text-sm text-[color:var(--muted)]"
      style={{ borderColor: "var(--border)" }}
    >
      <div className="mx-auto flex w-full max-w-6xl flex-wrap gap-x-6 gap-y-2 px-4">
        <span>{app("description")}</span>
        <span className="ml-auto flex gap-4">
          <Link href="/about">{t("about")}</Link>
          <Link href="/help">{t("help")}</Link>
          <Link href="/developers">{t("developers")}</Link>
        </span>
      </div>
    </footer>
  );
}
