import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getMessages, getTranslations } from "next-intl/server";
import Link from "next/link";
import { HexWash, Logo, Mark, Rule } from "@/components/Brand";
import "./globals.css";

/**
 * Inter everywhere, self-hosted.
 *
 * `next/font` downloads the files at build time and serves them from this
 * origin — so there is no CDN request on first paint, no layout shift while a
 * third party responds, and the site works on a network that cannot reach
 * Google. The brand's default body weight is Light (300); SemiBold (600) heads
 * and Black (900) is reserved for statement numbers.
 */
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "900"],
  display: "swap",
});

/** For identifiers, WKT and code. A monospace is not part of the brand type
 *  system, so it stays quiet and is used only where alignment carries meaning. */
const mono = IBM_Plex_Mono({
  variable: "--font-mono-stack",
  subsets: ["latin"],
  weight: ["400"],
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("app");
  return {
    title: { default: t("name"), template: `%s · ${t("name")}` },
    description: t("description"),
  };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const messages = await getMessages();
  const t = await getTranslations("nav");

  return (
    <html lang="en">
      <body className={`${inter.variable} ${mono.variable} antialiased`}>
        <NextIntlClientProvider messages={messages}>
          {/* First in the tab order, invisible until focused. An evaluator on a
              keyboard should not tab through a filter panel to reach a result. */}
          <a href="#main" className="skip-link">
            {t("skipToContent")}
          </a>
          <Header />
          <main id="main" className="mx-auto w-full max-w-6xl px-5 py-10">
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
  const links = [
    { href: "/", label: t("search") },
    { href: "/domains", label: t("domains") },
    { href: "/submit", label: t("submit") },
    { href: "/connect", label: t("connect") },
    { href: "/developers", label: t("developers") },
  ];

  return (
    <header
      className="relative border-b bg-[color:var(--surface)]"
      style={{ borderColor: "var(--border)" }}
    >
      {/* The motif as a corner wash, masked away from the nav. An edge device. */}
      <HexWash color="var(--og-petrol)" opacity={0.07} />
      <div className="relative mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4">
        <Link href="/" aria-label="OpenGrid Data Hub" className="shrink-0">
          <Logo className="h-7 w-auto" />
        </Link>
        <nav aria-label="Primary" className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-[color:var(--muted)] transition-colors hover:text-[color:var(--foreground)]"
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
    <footer className="og-panel og-hex relative mt-20 overflow-hidden">
      <HexWash color="#ffffff" opacity={0.06} />
      <div className="relative mx-auto w-full max-w-6xl px-5 py-10">
        <div className="flex flex-wrap items-start justify-between gap-8">
          <div className="max-w-md">
            <Mark className="h-8 w-auto text-[color:var(--og-orange)]" />
            <Rule className="!mt-4" />
            <p className="mt-4 text-sm text-white/80">{app("description")}</p>
          </div>
          <nav aria-label="Secondary" className="flex flex-col gap-2 text-sm">
            <Link href="/about" className="text-white/80 hover:text-white">
              {t("about")}
            </Link>
            <Link href="/help" className="text-white/80 hover:text-white">
              {t("help")}
            </Link>
            <Link href="/developers" className="text-white/80 hover:text-white">
              {t("developers")}
            </Link>
            <Link href="/connect" className="text-white/80 hover:text-white">
              {t("connect")}
            </Link>
          </nav>
        </div>
      </div>
    </footer>
  );
}
