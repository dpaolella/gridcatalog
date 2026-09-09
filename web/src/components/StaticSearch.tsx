"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { DatasetSummary, FacetBucket } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { ResultRow } from "@/components/ResultRow";
import { FacetGroup } from "@/components/FacetGroup";
import { SortSelect, compareBySort } from "@/components/SortSelect";

/** Results per page.
 *
 * The page used to show every match at once, on the reasoning that everything
 * public had shipped with it anyway. It stopped being true of what the reader
 * pays for. `ResultRow` is still the one row component both modes render — the
 * server-rendered copies the page used to pass down were a *second* copy of
 * every record in the payload, and at 444 records that made a 9 MB landing page
 * that `ops/check-page-weight.sh` refused to deploy.
 *
 * Only the rendering is paged. Filtering still runs over the whole catalog in
 * the browser, so a search narrows the real catalog and not a page of it. */
const PAGE_SIZE = 20;

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
  initial,
  facets,
}: {
  initial: DatasetSummary[];
  facets: Record<string, FacetBucket[]>;
}) {
  const t = useTranslations("search");
  const empty = useTranslations("empty");

  /**
   * The catalog arrives in two pieces, and the split is what keeps the landing
   * page servable.
   *
   * `initial` is the first page of rows, shipped in the page's own payload so
   * the list is there the instant React hydrates instead of blank until the
   * fetch resolves. It is *not* in the HTML — this component reads
   * `useSearchParams`, so Next cannot prerender it — which means a crawler and
   * a reader with JavaScript off see the shell, as they always have.
   * `catalog.json` is every record, fetched once on mount, and filtering runs
   * over that.
   *
   * It used to be one piece: every record serialised into `index.html`. At 66
   * records that was 592 KB and fine. The first real harvest took the catalog
   * to 1,117 and the page to **3,122 KB**, and `ops/check-page-weight.sh`
   * refused the deploy — correctly, and with this fix named first in its own
   * error message.
   *
   * Filtering before the fetch lands would search one page and silently report
   * it as the whole catalog, so it is disabled until then rather than allowed
   * to be wrong. That window is one request on a warm cache.
   */
  const [datasets, setDatasets] = useState<DatasetSummary[]>(initial);
  const [state, setState] = useState<"initial" | "ready" | "failed">("initial");

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    let live = true;
    fetch(`${base}/catalog.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body: { results?: DatasetSummary[] }) => {
        if (!live) return;
        // Guard the shape: a truncated or half-written file must not empty the
        // page. Keeping `initial` and saying so beats rendering nothing.
        if (Array.isArray(body.results) && body.results.length) {
          setDatasets(body.results);
          setState("ready");
        } else {
          setState("failed");
        }
      })
      .catch(() => live && setState("failed"));
    return () => {
      live = false;
    };
  }, []);

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

  const sort = params.get("sort") ?? "";

  //: Is the reader asking a question, as opposed to browsing the front page?
  //  Only then does the difference between one page and the whole catalog
  //  matter, so only then is the pre-fetch window worth mentioning.
  const searching =
    Boolean(query) || Object.values(selected).some((values) => values.length > 0);

  const results = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    const matched = datasets.filter((dataset) => {
      const text = haystacks.get(dataset.id) ?? "";
      if (!terms.every((term) => text.includes(term))) return false;
      return Object.entries(selected).every(
        ([field, values]) => values.length === 0 || values.some((v) => matches(dataset, field, v)),
      );
    });
    // An explicit sort is deterministic, so both builds genuinely agree on it.
    // Relevance is the absence of one: the server ranks, and here the order the
    // exporter gave is left alone rather than a second scoring function being
    // invented — see the note at the top of this file.
    return sort ? [...matched].sort(compareBySort(sort)) : matched;
  }, [datasets, haystacks, query, selected, sort]);

  /**
   * How many of `results` are rendered. Reset whenever the URL changes, because
   * the URL *is* the query: a reader who narrows a 400-hit search to 12 should
   * see all 12, not the first 20 of a list that no longer exists.
   *
   * Adjusted during render rather than in an effect — React's own advice for
   * state derived from a prop change, and it avoids rendering one frame of the
   * previous page's length.
   */
  const [shown, setShown] = useState(PAGE_SIZE);
  const [pagedFor, setPagedFor] = useState(params.toString());
  if (pagedFor !== params.toString()) {
    setPagedFor(params.toString());
    setShown(PAGE_SIZE);
  }

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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="og-eyebrow" aria-live="polite">
              {state === "initial" && searching
                ? t("loadingCatalog")
                : t("resultsCount", { count: results.length })}
            </p>
            <SortSelect />
          </div>

          {/* Said, not hidden. A search that ran over the prerendered first
              page while the rest was still in flight would report a count for
              the whole catalog and mean one page of it — wrong in the one
              direction this project cares about, since "nothing matches" is a
              claim the catalog makes deliberately. */}
          {state === "failed" && searching ? (
            <p className="og-card p-3 text-sm text-[color:var(--muted)]">
              {t("catalogUnavailable", { count: initial.length })}
            </p>
          ) : null}

          {results.length === 0 ? (
            <EmptyState title={empty("noResults")}>
              <p>{empty("noResultsHelp", {
                total: datasets.length,
                column: "ssrd",
                concept: "globalHorizontalIrradiance",
              })}</p>
            </EmptyState>
          ) : (
            <>
              <ul className="space-y-4">
                {results.slice(0, shown).map((dataset) => (
                  <ResultRow key={dataset.id} dataset={dataset} />
                ))}
              </ul>
              {shown < results.length ? (
                <button
                  type="button"
                  onClick={() => setShown((n) => n + PAGE_SIZE)}
                  className="mt-4 w-full border px-4 py-2 text-sm"
                  style={{ borderColor: "var(--border)", borderRadius: "var(--radius)" }}
                >
                  {t("loadMore", {
                    count: Math.min(PAGE_SIZE, results.length - shown),
                    total: results.length,
                  })}
                </button>
              ) : null}
            </>
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
    // The exporter supplies this for records the API has a description for and
    // a summary for. Without it 38 of 66 published records matched on their
    // title and licence and nothing else.
    dataset.search_text,
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
    case "has_usage_evidence":
      return String(Boolean(dataset.has_usage_evidence)) === value;
    default:
      return true;
  }
}
