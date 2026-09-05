import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  IS_SNAPSHOT,
  NotFoundError,
  getDataset,
  getDistributions,
  getLinks,
  getQuality,
  getSchema,
  snapshotDatasetIds,
} from "@/lib/api";
import { QualityBadges } from "@/components/QualityBadges";
import { Rule } from "@/components/Brand";
import { DatasetTabs } from "@/components/DatasetTabs";
import { ReportIssue } from "@/components/ReportIssue";
import { iriTail } from "@/lib/format";

/**
 * One record, seven tabs (PRD §F3).
 *
 * Every tab is fetched on the server in parallel and rendered at once. Tabs
 * that fetch on click feel faster on the first paint and slower on every
 * subsequent one, and a modeller comparing two datasets moves between tabs
 * constantly.
 *
 * A record the caller may not see 404s exactly as an absent one does. The API
 * makes them indistinguishable and this page must not undo that with a
 * different message.
 */

type Params = Promise<{ id: string }>;

/**
 * Which record pages the static build writes.
 *
 * Exactly the ids the exporter wrote, which is exactly the anonymously
 * visible catalog — so entitlement is enforced by there being no file, not by
 * a check that could be got wrong. An allowlisted record has no page here at
 * all; a restricted-metadata one has the stub the API itself would serve.
 *
 * Empty in live mode, where pages are rendered on demand and pre-rendering a
 * fixed set would only make the catalog stale.
 */
export async function generateStaticParams() {
  return (await snapshotDatasetIds()).map((id) => ({ id }));
}

export async function generateMetadata({ params }: { params: Params }) {
  const { id } = await params;
  try {
    const dataset = await getDataset(id);
    return { title: dataset.title, description: dataset.summary ?? undefined };
  } catch {
    return { title: "Not found" };
  }
}

export default async function DatasetPage({ params }: { params: Params }) {
  const { id } = await params;
  const t = await getTranslations("dataset");
  const empty = await getTranslations("empty");

  let dataset;
  try {
    dataset = await getDataset(id);
  } catch (error) {
    // `notFound()` rather than rendering an empty state inline, so the HTTP
    // status is 404 and not 200. A crawler, a link checker and a browser
    // should agree about whether this page exists — and the copy in
    // `not-found.tsx` is the same either way, because the API returns an
    // identical 404 for a record that is absent and one that is restricted.
    if (error instanceof NotFoundError) notFound();
    throw error;
  }

  // Fetched together, and each allowed to fail on its own: a broken link
  // prober should not take the whole record page down with it.
  const [schema, quality, distributions, links] = await Promise.all([
    getSchema(id).catch(() => null),
    getQuality(id).catch(() => null),
    getDistributions(id).catch(() => []),
    getLinks(id).catch(() => null),
  ]);

  const levelKey = String(dataset.completeness_level) as "1" | "2" | "3";

  return (
    <article className="space-y-6">
      <nav className="text-sm text-[color:var(--muted)]">
        <Link href="/" className="hover:text-[color:var(--foreground)]">
          ← {empty("backToSearch")}
        </Link>
      </nav>

      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{dataset.title}</h1>
            <Rule />
            {dataset.publisher ? (
              <p className="mt-4 text-sm text-[color:var(--muted)]">{dataset.publisher}</p>
            ) : null}
          </div>
          {/* A report button with nothing to post to would be reported as
              broken, which is not the feedback anybody wants. */}
          {IS_SNAPSHOT ? null : (
            <ReportIssue datasetId={dataset.id} datasetTitle={dataset.title} />
          )}
        </div>

        {dataset.summary ? <p className="max-w-prose">{dataset.summary}</p> : null}

        <div className="flex flex-wrap items-center gap-3">
          <span className="og-tag" title={t(`levelHelp.${levelKey}`)}>
            {t("level", { level: dataset.completeness_level })} · {t(`levelNames.${levelKey}`)}
          </span>
          {dataset.data_domains.map((domain) => (
            <span key={domain.iri} className="og-tag">
              {domain.label ?? iriTail(domain.iri)}
            </span>
          ))}
        </div>

        <QualityBadges facets={quality?.facets ?? dataset.quality} size="lg" />
      </header>

      <DatasetTabs
        dataset={dataset}
        schema={schema}
        quality={quality}
        distributions={distributions}
        links={links}
      />
    </article>
  );
}
