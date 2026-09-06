import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { IS_SNAPSHOT, search } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { Facets } from "@/components/Facets";
import { ResultRow } from "@/components/ResultRow";
import { SearchBar } from "@/components/SearchBar";
import { StaticSearch } from "@/components/StaticSearch";
import { HexWash, Rule } from "@/components/Brand";
import { Pagination } from "@/components/Pagination";
import { perRequest } from "@/lib/rendering";

/**
 * The landing page and the list view are the same page (PRD §F3).
 *
 * Deliberately: a landing page that is not already a search makes the first
 * thing a modeller does a click, and the done-criterion for this milestone is
 * measured in seconds from here to an access plan.
 */

const FACETS = [
  "data_domain",
  "provenance_class",
  "license",
  "format",
  "completeness_level",
  "spatial_granularity",
  "anonymous_access",
  "link_health",
];

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function SearchPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-8">
      <Hero />
      {IS_SNAPSHOT ? <SnapshotResults /> : <LiveResults searchParams={searchParams} />}
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
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{app("tagline")}</h1>
        <Rule />
        <p className="mt-5 text-base text-[color:var(--muted)]">{app("description")}</p>
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
          <p className="og-eyebrow" aria-live="polite">
            {t("resultsCount", { count: response.total })}
            {response.total > 0
              ? ` · ${t("showing", {
                  from: offset + 1,
                  to: Math.min(offset + response.results.length, response.total),
                  total: response.total,
                })}`
              : ""}
          </p>

          {response.results.length === 0 ? (
            <EmptyState
              title={empty("noResults")}
              action={hasQuery ? { href: "/", label: empty("noResultsAction") } : undefined}
            >
              <p>
                {empty("noResultsHelp", {
                  total: response.total || "all",
                  example: "ssrd",
                })}
              </p>
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
 * page. No pagination: everything public is already here, and a page control
 * over a list the reader is holding would be furniture.
 */
async function SnapshotResults() {
  const response = await search({});
  const rows = Object.fromEntries(
    response.results.map((dataset) => [dataset.id, <ResultRow key={dataset.id} dataset={dataset} />]),
  );
  return (
    // `useSearchParams` needs a boundary: the shell prerenders without a query
    // string and the filter applies on hydration, which is the most a static
    // page can honestly do.
    <Suspense>
      <StaticSearch datasets={response.results} facets={response.facets} rows={rows} />
    </Suspense>
  );
}
