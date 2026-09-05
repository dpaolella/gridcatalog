import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { search } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { Facets } from "@/components/Facets";
import { ResultRow } from "@/components/ResultRow";
import { SearchBar } from "@/components/SearchBar";
import { Pagination } from "@/components/Pagination";

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

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function SearchPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const t = await getTranslations("search");
  const empty = await getTranslations("empty");
  const app = await getTranslations("app");

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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{app("tagline")}</h1>
        <p className="mt-1 max-w-prose text-sm text-[color:var(--muted)]">{app("description")}</p>
      </div>

      <Suspense>
        <SearchBar />
      </Suspense>

      <div className="grid gap-8 md:grid-cols-[13rem_1fr]">
        <Suspense>
          <Facets facets={response.facets} />
        </Suspense>

        <div className="min-w-0 space-y-4">
          <p className="text-sm text-[color:var(--muted)]" aria-live="polite">
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
            <ul className="space-y-3">
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
    </div>
  );
}
