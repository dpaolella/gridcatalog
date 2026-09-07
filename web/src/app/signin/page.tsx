import { getTranslations } from "next-intl/server";
import { ApiError, IS_SNAPSHOT, type ProviderList, authProviders, loginUrl } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { Rule } from "@/components/Brand";
import { StaticNotice } from "@/components/StaticNotice";
import { perRequest } from "@/lib/rendering";

/**
 * Signing in (PRD §F3, §F10).
 *
 * Federated only: the Hub holds no passwords, so there is nothing here to
 * register. The API owns the whole flow — PKCE, the state parameter, and the
 * check that `next` points back at a configured origin rather than wherever a
 * link says — so this page's entire job is to offer the providers the
 * deployment actually has credentials for and hand the browser to `/v1/auth`.
 *
 * **Browsing needs none of this.** Public records are readable signed out, and
 * a sign-in wall in front of a catalog is how a catalog stops being used. What
 * a session buys is the steward queue, restricted records you are entitled to,
 * and tokens.
 */

type SearchParams = Promise<{ next?: string }>;

export async function generateMetadata() {
  const t = await getTranslations("signin");
  return { title: t("title") };
}

export default async function SignInPage({ searchParams }: { searchParams: SearchParams }) {
  const t = await getTranslations("signin");

  return (
    <div className="max-w-xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <Rule />
        <p className="mt-4 text-sm text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>
      {/* `searchParams` is awaited inside, not here: awaiting it at all makes a
          route un-prerenderable, and the static build has no sign-in to offer
          anyway. */}
      {IS_SNAPSHOT ? <StaticNotice /> : <Providers searchParams={searchParams} />}
      <p className="text-sm text-[color:var(--muted)]">{t("browsingIsOpen")}</p>
      {/* This page used to promise that a session also buys "restricted records
          you are entitled to". It does not, on this site: `lib/api.ts` sends
          `authenticated: true` on exactly two calls — `me` and `reviewQueue` —
          and every catalog read goes out anonymous so the pages stay cacheable.
          A signed-in reader entitled to a restricted record still cannot see it
          here. Saying where they can beats a promise the UI does not keep. */}
      <p className="text-sm text-[color:var(--muted)]">{t("restrictedNote")}</p>
    </div>
  );
}

async function Providers({ searchParams }: { searchParams: SearchParams }) {
  await perRequest();
  const t = await getTranslations("signin");
  const { next } = await searchParams;
  const returnTo = await absoluteUrl(next ?? "/");

  let list: ProviderList;
  try {
    list = await authProviders();
  } catch (error) {
    // The API being unreachable is not the reader's problem to debug, and a
    // stack trace here would be the least useful thing on the page.
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <EmptyState title={t("unavailable")}>
        <p>{t("unavailableHelp", { status: status || "no response" })}</p>
      </EmptyState>
    );
  }

  if (list.providers.length === 0) {
    // Configured with no client ids. Saying so beats rendering a button that
    // leads to an error page blaming the person who pressed it.
    return (
      <EmptyState title={t("noneConfigured")}>
        <p>{t("noneConfiguredHelp")}</p>
      </EmptyState>
    );
  }

  return (
    <ul className="space-y-3">
      {list.providers.map((provider) => (
        <li key={provider}>
          <a href={loginUrl(provider, returnTo)} className="og-cta" rel="nofollow">
            {t("continueWith", { provider: label(provider) })}
          </a>
        </li>
      ))}
    </ul>
  );
}

/**
 * Where the API should send the browser once sign-in succeeds.
 *
 * Absolute, and it has to be: the API redirects with whatever `next` holds, so
 * a bare path would resolve against the API's own origin and land on a 404
 * there rather than on this site.
 *
 * Built from the incoming request because the site does not otherwise know its
 * own public URL, and a `Host` header is the reader's to set. That is safe
 * here and only because the API checks: `_safe_next` accepts an absolute URL
 * only if it starts with one of the configured origins, so a forged host is
 * discarded and sign-in returns to the configured site root. This end must not
 * be the one that decides — an unchecked `next` is an open redirect wearing our
 * domain in the address bar for the part the reader was watching.
 */
async function absoluteUrl(path: string): Promise<string> {
  const { headers } = await import("next/headers");
  const header = await headers();
  const host = header.get("host");
  if (!host) return path;
  const protocol = header.get("x-forwarded-proto") ?? "http";
  const base = `${protocol}://${host}${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}`;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Provider ids are lowercase keys in the API's config; these are the names
 *  their own brand guidelines use. Anything unrecognised is title-cased rather
 *  than dropped — a deployment may configure one this list has not met. */
function label(provider: string): string {
  const known: Record<string, string> = {
    google: "Google",
    github: "GitHub",
    orcid: "ORCID",
    microsoft: "Microsoft",
    okta: "Okta",
  };
  return known[provider] ?? provider.charAt(0).toUpperCase() + provider.slice(1);
}
