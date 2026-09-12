import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { IS_SNAPSHOT, search, searchGaps } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { Facets } from "@/components/Facets";
import { GapNotice } from "@/components/GapNotice";
import { ResultRow } from "@/components/ResultRow";
import { SearchBar } from "@/components/SearchBar";
import { StaticSearch } from "@/components/StaticSearch";
import { HexWash, Rule } from "@/components/Brand";
import { Pagination } from "@/components/Pagination";
import { SortSelect } from "@/components/SortSelect";
import { perRequest } from "@/lib/rendering";

/**
 * The data catalog: one of the Hub's four sections, not its front door.
 *
 * It used to be both. "The landing page and the list view are the same page"
 * was right for a catalog — a landing page that is not already a search makes
 * the first thing a modeller does a click. It is wrong for a registry, where
 * the catalog is the smallest of four things and a reader arriving at a
 * dataset search would conclude that is all there is.
 *
 * The cost is one click for the modeller who only ever wanted the catalog.
 * `/datasets` is a stable, linkable, bookmarkable address, so they pay it once.
 */

const FACETS = [
  // A registry holds more than datasets. `record_type` is what keeps a search
  // for "wind" from returning a utility's filing beside a wind atlas, and
  // `fidelity_class` is the one axis on which reference models differ from
  // each other in a way a modeller has to choose on.
  "record_type",
  "fidelity_class",
  "data_domain",
  "provenance_class",
  "license",
  "format",
  "completeness_level",
  // Next to completeness deliberately, because the two read as the same thing
  // and are not (#46). Completeness answers *how well described*; this answers
  // *described at all*. Level 1 means no field metadata by definition, so 27
  // records with real probed schemas sat at level 1 and were indistinguishable
  // from the 410 with none through every control the site offered.
  "field_count_bucket",
  "spatial_granularity",
  "anonymous_access",
  "link_health",
  // Offered by the static site since it was built and never by this one, so
  // the same page had different filters depending on which build you opened.
  // Found by diffing this list against `services/snapshot.py`, which claimed
  // to be a copy of it; `tests/snapshot/test_facet_parity.py` now checks that.
  "has_usage_evidence",
];

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function DatasetsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  return (
    <div className="space-y-8">
      <Hero />
      {IS_SNAPSHOT ? (
        <SnapshotResults />
      ) : (
        <LiveResults searchParams={searchParams} />
      )}
    </div>
  );
}

async function Hero() {
  const app = await getTranslations("app");
  return (
    /* The hero is the one place the motif runs wide, as a corner wash behind
       the title. Everywhere else it stays at an edge. */
    <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
      <HexWash color="var(--og-petrol)" opacity={0.08} />
      <div className="relative max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          {app("tagline")}
        </h1>
        <Rule />
        <p className="mt-5 text-base text-[color:var(--muted)]">
          {app("description")}
        </p>
      </div>
    </section>
  );
}

async function LiveResults({ searchParams }: { searchParams: SearchParams }) {
  await perRequest();
  const params = await searchParams;
  const t = await getTranslations("search");
  const empty = await getTranslations("empty");

  const offset = Number(params.offset ?? 0) || 0;
  const limit = 20;

  const response = await search({
    ...params,
    facets: FACETS.join(","),
    limit: String(limit),
    offset: String(offset),
  });

  const hasQuery = Object.keys(params).some((key) => key !== "offset");
  // Only when there is nothing to show and something was actually asked for.
  // A gap notice under a full result list would be noise, and one under an
  // unfiltered landing page would be answering a question nobody asked.
  const asked = Array.isArray(params.q) ? params.q[0] : params.q;
  const gaps = response.results.length === 0 && asked ? await searchGaps(asked) : [];

  return (
    <>
      <Suspense>
        <SearchBar />
      </Suspense>

      <div className="grid gap-10 md:grid-cols-[13rem_1fr]">
        <Suspense>
          <Facets facets={response.facets} />
        </Suspense>

        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="og-eyebrow" aria-live="polite">
              {t("resultsCount", { count: response.total })}
              {response.total > 0
                ? ` · ${t("showing", {
                    from: offset + 1,
                    to: Math.min(
                      offset + response.results.length,
                      response.total,
                    ),
                    total: response.total,
                  })}`
                : ""}
            </p>
            <Suspense>
              <SortSelect />
            </Suspense>
          </div>

          {response.results.length === 0 ? (
            <EmptyState
              title={empty("noResults")}
              action={
                hasQuery
                  ? { href: "/datasets", label: empty("noResultsAction") }
                  : undefined
              }
            >
              <p>
                {empty("noResultsHelp", {
                  total: response.total || "all",
                  column: "ssrd",
                  concept: "globalHorizontalIrradiance",
                })}
              </p>
              {/* The answer this catalog can give that a search engine cannot.
                  A reader who typed "nodal demand", got nothing, and is told
                  only "no datasets match" has learned that the catalog is
                  small. Told that nothing open supplies it, with who found
                  that and when, they have learned something true about the
                  field — PRD §5, saying what does not exist is a feature. */}
              <GapNotice gaps={gaps} />
            </EmptyState>
          ) : (
            <ul className="space-y-4">
              {response.results.map((dataset) => (
                <ResultRow key={dataset.id} dataset={dataset} />
              ))}
            </ul>
          )}

          <Suspense>
            <Pagination total={response.total} offset={offset} limit={limit} />
          </Suspense>
        </div>
      </div>
    </>
  );
}

/**
 * The same search, filtered in the browser over a catalog that shipped with the
 * page.
 *
 * The whole catalog ships as data, and `StaticSearch` renders a page of it at a
 * time. It used to also build every row here and pass them down, so each record
 * travelled twice — as the summary the filter reads and again as a serialised
 * element tree that, because `StaticSearch` reads the query string, was never
 * rendered into the built HTML at all. That was tolerable at 66 records and
 * became a 9 MB landing page at 444, where `ops/check-page-weight.sh` stopped
 * the deploy and the published site froze at the last build that passed.
 *
 * Measured on this catalog: 9,082 KB before, 3,224 KB once `snapshot.py`
 * stopped shipping whole descriptions, 1,718 KB with the second copy gone —
 * and 3,122 KB again the moment the first real harvest took the catalog from
 * 272 records to 1,117, which is the run `ops/check-page-weight.sh` refused.
 *
 * So the whole catalog no longer ships here. Only the first page does, which is
 * what makes the list real on first paint and for a crawler; `StaticSearch`
 * fetches `catalog.json` for everything it filters over. The page stops growing
 * with the catalog, which is the property the three previous fixes did not buy.
 */
/** Rows shipped with the page. The rest arrives as `catalog.json`.
 *
 * **Not rendered into the HTML** — `StaticSearch` is a client component that
 * reads `useSearchParams`, so Next serialises these into the RSC payload and
 * React paints them on hydration. Measured: 0 dataset links in the built HTML,
 * 20 dataset ids inside its `<script>` blocks, which are 91 KB of the 109 KB
 * page. So this buys a list that is there the instant React runs, rather than a
 * blank one until `catalog.json` resolves — and it buys nothing for a crawler
 * or a reader with JavaScript off, who see the shell either way.
 *
 * That limitation is not new and not this change's to fix: the same component
 * has always rendered on the client, which is what the `Suspense` note below
 * means by "the most a static page can honestly do".
 *
 * Matched to `StaticSearch`'s own `PAGE_SIZE`: fewer leaves a gap under the
 * fold until the fetch lands, more pays weight for rows nobody scrolled to. */
const PRERENDERED = 20;

async function SnapshotResults() {
  const response = await search({});
  return (
    // `useSearchParams` needs a boundary: the shell prerenders without a query
    // string and the filter applies on hydration, which is the most a static
    // page can honestly do.
    <Suspense>
      <StaticSearch initial={response.results.slice(0, PRERENDERED)} facets={response.facets} />
    </Suspense>
  );
}
