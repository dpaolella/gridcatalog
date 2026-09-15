import Link from "next/link";
import { Suspense } from "react";
import { CatalogReturnLink } from "@/components/CatalogReturnLink";
import { ReferenceModelSuitability } from "@/components/ReferenceModelSuitability";
import { MAPPED_MODELS } from "@/lib/reference-models";
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
  getStudyUsage,
  snapshotDatasetIds,
} from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { QualityBadges } from "@/components/QualityBadges";
import { StudyUsage } from "@/components/StudyUsage";
import { Rule } from "@/components/Brand";
import { DatasetTabs } from "@/components/DatasetTabs";
import { Lineage } from "@/components/Lineage";
import { ReportIssue } from "@/components/ReportIssue";
import { iriTail } from "@/lib/format";
import { perRequest } from "@/lib/rendering";

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
 * Absent in live mode, where pages are rendered on demand and pre-rendering a
 * fixed set would only make the catalog stale.
 *
 * `undefined` rather than a function returning `[]`, and the difference is the
 * whole route. Next decides at build time whether a segment is static or
 * dynamic, and *any* `generateStaticParams` export makes it static — with an
 * empty list that means zero pre-rendered paths and every request served by an
 * on-demand render that is still in the static store. `cookies()` is illegal
 * there, so once the reads below started carrying the session (#91) every
 * record page 500'd with DYNAMIC_SERVER_USAGE, anonymous ones included.
 * `perRequest()` cannot rescue it: with nothing to pre-render, `connection()`
 * is never reached at build time and the classification is already made.
 *
 * So the export exists in exactly the build that has a use for it. This is a
 * conditional *value*, not a conditional route-segment literal — Next parses
 * `export const dynamic` without evaluating it and rejects an expression, but
 * reads `generateStaticParams` off the loaded module.
 */
export const generateStaticParams = IS_SNAPSHOT
  ? async () => (await snapshotDatasetIds()).map((id) => ({ id }))
  : undefined;

export async function generateMetadata({ params }: { params: Params }) {
  const { id } = await params;
  await perRequest();
  try {
    const dataset = await getDataset(id);
    return { title: dataset.title, description: dataset.summary ?? undefined };
  } catch {
    return { title: "Not found" };
  }
}

export default async function DatasetPage({ params }: { params: Params }) {
  const { id } = await params;
  // Reading a record now depends on who is asking (#91): every call below
  // forwards the session cookie when there is one. `cookies()` is illegal
  // during a static render, and this route has `generateStaticParams`, so on
  // the live build Next rendered it in the static store and every detail page
  // failed with DYNAMIC_SERVER_USAGE. `perRequest()` says out loud what the
  // reads already assume, and stays a no-op in the deliberately anonymous
  // static export, where `generateStaticParams` still writes a file per record.
  await perRequest();
  const t = await getTranslations("dataset");
  const empty = await getTranslations("empty");
  const modelText = await getTranslations("referenceModel");

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

  // A study reached through `/datasets/...`. The list rows send studies to
  // `/studies/[id]`, so nobody arrives here by clicking — but a link written
  // before the section existed, or a reader editing a URL, still lands on a
  // real record, and rendering it with seven empty tabs would read as a broken
  // record rather than as a different kind of thing. Before the fetches below,
  // which would every one of them come back empty.
  if (dataset.record_type === "study") {
    const study = await getTranslations("study");
    return (
      <article className="space-y-6">
        <EmptyState
          title={study("onDataset")}
          action={{ href: `/studies/${dataset.id}`, label: dataset.title }}
        >
          <p>{study("onDatasetHelp")}</p>
        </EmptyState>
      </article>
    );
  }

  // Fetched together, and each allowed to fail on its own: a broken link
  // prober should not take the whole record page down with it.
  const [schema, quality, distributions, links, studies] = await Promise.all([
    getSchema(id).catch(() => null),
    getQuality(id).catch(() => null),
    getDistributions(id).catch(() => []),
    getLinks(id).catch(() => null),
    getStudyUsage(id),
  ]);

  const levelKey = String(dataset.completeness_level) as "1" | "2" | "3";

  return (
    <article className="space-y-6">
      <nav className="text-sm text-[color:var(--muted)]">
        <Suspense fallback={<Link href="/datasets">← {empty("backToSearch")}</Link>}>
          <CatalogReturnLink />
        </Suspense>
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

        {/* Visible on the page where a reader decides whether to use the
            dataset, with the explanation in the page rather than in a `title`
            attribute — a tooltip is invisible on touch and unreliable to a
            screen reader. It showed only in search results before, so the one
            caveat that matters most disappeared at exactly the moment it became
            relevant. A quarter of the published catalog is reference-only. */}
        {dataset.reference_only ? (
          <p className="og-card max-w-prose p-3 text-sm">
            <span className="font-semibold text-[color:var(--accent-text)]">
              {t("referenceOnly")}
            </span>{" "}
            <span className="text-[color:var(--muted)]">{t("referenceOnlyHelp")}</span>
          </p>
        ) : null}

        {/* Held in the graph since M2 and projected nowhere until #55: 478
            caveats reached no caller and no page. They are the one thing on a
            record that comes from somebody having tried the dataset, so they
            sit above the fold rather than in a tab — a reader deciding whether
            to use this should not have to go looking. Rendered as a list
            because a record commonly carries three or four, and the order is
            not meaningful: `og:caveat` is an RDF set, so the projector sorts
            them for a stable document rather than pretending to rank them. */}
        {dataset.caveats?.length ? (
          <section className="og-card max-w-prose space-y-2 p-3 text-sm">
            <h2 className="font-semibold text-[color:var(--accent-text)]">{t("caveats")}</h2>
            <ul className="list-disc space-y-1 pl-5 text-[color:var(--muted)]">
              {dataset.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </section>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <span className="og-tag" title={t(`levelHelp.${levelKey}`)}>
            {t("level", { level: dataset.completeness_level })} · {t(`levelNames.${levelKey}`)}
          </span>
          {/* Beside the level, not folded into it (#46). Level 1 means no field
              metadata *by definition* and level 2 needs a definition and a value
              basis on every field, which a probe cannot supply — so a record
              with 273 genuinely probed fields sits at level 1 wearing the same
              badge as one with none. These are different objects and the reader
              is deciding between them. */}
          {schema && schema.fields.length > 0 ? (
            <span className="og-tag" title={t("fieldsDescribedHelp")}>
              {t("fieldsDescribed", { count: schema.fields.length })}
            </span>
          ) : null}
          {dataset.data_domains.map((domain) => (
            <span key={domain.iri} className="og-tag">
              {domain.label ?? iriTail(domain.iri)}
            </span>
          ))}
        </div>

        <QualityBadges facets={quality?.facets ?? dataset.quality} size="lg" />

        {/* Beside the Provenance grade, because it is the evidence behind it:
            the grade says "modeled" and this says how far from an observation
            that leaves you (#52). */}
        <Lineage dataset={dataset} />

        {/* Distinct from the citations on the Connections tab, and the
            distinction is the whole point of #82. `usage_evidence` is a string
            a harvest found in a source's own metadata: unverifiable, and absent
            for most records because most sources have no field that could carry
            one. Every study here is an object in this catalog with an
            assumption set behind it, so "used by 2 studies" is a claim a reader
            can go and check rather than one the catalog is asking to be trusted
            on. */}
        <StudyUsage usage={studies} isReferenceModel={dataset.record_type === "reference_model"} />
      </header>

      {dataset.record_type === "reference_model" && !dataset.redacted ? (
        <div className="space-y-3">
          <ReferenceModelSuitability model={dataset} />
          {MAPPED_MODELS[dataset.id] ? (
            <div className="flex flex-wrap gap-3">
              <Link href={`/reference-models#model-${dataset.id}`} className="og-cta">
                {modelText("viewMap")}
              </Link>
              {/* The values, not the file. The Downloads tab still offers the
                  document and this is the reading of it — #98's point being
                  that a 2.9 MB download is a thing readers do not open, so
                  everything the catalog knows about how the model was made was
                  reachable only by people who already trusted it. */}
              <Link href={`/reference-models/${dataset.id}`} className="og-cta">
                {modelText("viewComponents")}
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}

      <DatasetTabs
        canReport={!IS_SNAPSHOT}
        dataset={dataset}
        schema={schema}
        quality={quality}
        distributions={distributions}
        links={links}
      />
    </article>
  );
}
