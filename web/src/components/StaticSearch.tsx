"use client";

import { Fragment, useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { DatasetSummary, FacetBucket } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { FacetGroup } from "@/components/FacetGroup";

/**
 * Search, in the browser, over the snapshot.
 *
 * The static build has no server to query, so the whole public catalog ships
 * with the page and this filters it. That is fine for a catalog of this size
 * and would not be for a large one — at a few thousand records the index
 * outgrows a page payload and the honest fix is to serve the live API rather
 * than to shard a JSON file.
 *
 * **This ranking is deliberately simpler than the API's**, and saying so
 * matters more than hiding it. The server ranks with a real scoring function
 * over an inverted index; this does token matching and filtering. It is a
 * preview of the catalog, not a second implementation of search — and the
 * moment it starts trying to be one, the two will disagree and the static site
 * will quietly become wrong.
 */
export function StaticSearch({
  datasets,
  facets,
  rows,
}: {
  datasets: DatasetSummary[];
  facets: Record<string, FacetBucket[]>;
  /** The rows, already rendered on the server, keyed by dataset id.
   *
   * A function prop cannot cross the server/client boundary, but an element
   * can — so the server renders every row with the same `ResultRow` the live
   * site uses and this component decides which of them to show. One row
   * component, two modes; the alternative was a second row renderer that would
   * drift from the first the week after it was written. */
  rows: Record<string, React.ReactNode>;
}) {
  const t = useTranslations("search");
  const empty = useTranslations("empty");

  /**
   * The URL is the state, exactly as it is on the server-rendered build.
   *
   * Holding it in `useState` instead was a quiet bug: the Domains page links to
   * `/?data_domain=<iri>`, nobody read the query string, and every domain card
   * landed the reader on the unfiltered catalog. It also meant no filtered view
   * was linkable and the back button did not restore a search — three
   * behaviours the reader has no reason to expect to differ between the two
   * builds of the same page.
   */
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const query = params.get("q") ?? "";
  const selected = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const key of new Set(params.keys())) {
      if (key === "q" || key === "offset") continue;
      out[key] = params.getAll(key);
    }
    return out;
  }, [params]);

  const write = useCallback(
    (next: URLSearchParams) => {
      const search = next.toString();
      // `replace`, not `push`: typing eight characters should leave one history
      // entry, not eight. Same reasoning as SearchBar on the live build.
      router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const setQuery = useCallback(
    (value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set("q", value);
      else next.delete("q");
      write(next);
    },
    [params, write],
  );

  const haystacks = useMemo(
    () => new Map(datasets.map((d) => [d.id, haystack(d)])),
    [datasets],
  );

  const results = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return datasets.filter((dataset) => {
      const text = haystacks.get(dataset.id) ?? "";
      if (!terms.every((term) => text.includes(term))) return false;
      return Object.entries(selected).every(
        ([field, values]) => values.length === 0 || values.some((v) => matches(dataset, field, v)),
      );
    });
  }, [datasets, haystacks, query, selected]);

  function toggle(field: string, value: string) {
    const next = new URLSearchParams(params.toString());
    const current = next.getAll(field);
    next.delete(field);
    for (const item of current) if (item !== value) next.append(field, item);
    if (!current.includes(value)) next.append(field, value);
    write(next);
  }

  function clearFilters() {
    const next = new URLSearchParams();
    const q = params.get("q");
    if (q) next.set("q", q);
    write(next);
  }

  const hasFilters = Object.values(selected).some((v) => v.length > 0);
  const entries = Object.entries(facets).filter(([, buckets]) => buckets.length > 0);

  return (
    <div className="space-y-8">
      <div>
        <label htmlFor="q" className="sr-only">
          {t("label")}
        </label>
        <input
          id="q"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("placeholder")}
          autoComplete="off"
          className="w-full px-4 py-3 text-base"
        />
        <p className="mt-1.5 text-xs text-[color:var(--muted)]">{t("typing")}</p>
      </div>

      <div className="grid gap-10 md:grid-cols-[13rem_1fr]">
        <aside aria-label={t("filters")} className="min-w-0 space-y-6 text-sm">
          <div className="flex items-baseline justify-between">
            <h2 className="font-semibold">{t("filters")}</h2>
            {hasFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="text-xs font-medium text-[color:var(--accent-text)] hover:underline"
              >
                {t("clearFilters")}
              </button>
            ) : null}
          </div>

          {entries.map(([field, buckets]) => (
            <FacetGroup
              key={field}
              field={field}
              buckets={buckets}
              isActive={(value) => (selected[field] ?? []).includes(value)}
              onToggle={(value) => toggle(field, value)}
            />
          ))}
        </aside>

        <div className="min-w-0 space-y-4">
          <p className="og-eyebrow" aria-live="polite">
            {t("resultsCount", { count: results.length })}
          </p>

          {results.length === 0 ? (
            <EmptyState title={empty("noResults")}>
              <p>{empty("noResultsHelp", { total: datasets.length, example: "ssrd" })}</p>
            </EmptyState>
          ) : (
            <ul className="space-y-4">
              {results.map((dataset) => (
                <Fragment key={dataset.id}>{rows[dataset.id]}</Fragment>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/** The text a query is matched against. Mirrors what the server indexes, minus
 *  the scoring — see the note at the top of this file. */
function haystack(dataset: DatasetSummary): string {
  return [
    dataset.title,
    dataset.summary,
    dataset.publisher,
    ...(dataset.creators ?? []),
    ...dataset.data_domains.map((d) => d.label ?? d.iri),
    dataset.license_id,
    dataset.provenance_class,
    ...(dataset.formats ?? []),
    ...(dataset.spatial?.place_labels ?? []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/** Facet matching, by the same document paths the server facets on. */
function matches(dataset: DatasetSummary, field: string, value: string): boolean {
  switch (field) {
    case "data_domain":
      return dataset.data_domains.some((d) => d.iri === value);
    case "provenance_class":
      return dataset.provenance_class === value;
    case "license":
      return dataset.license_id === value;
    case "format":
      return (dataset.formats ?? []).includes(value);
    case "completeness_level":
      return String(dataset.completeness_level) === value;
    case "spatial_granularity":
      return dataset.spatial?.granularity === value;
    case "anonymous_access":
      return String(dataset.anonymous_access) === value;
    case "link_health":
      return dataset.worst_link_health === value;
    default:
      return true;
  }
}
