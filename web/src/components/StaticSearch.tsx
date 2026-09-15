"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { DataGap, DatasetSummary, FacetBucket } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { GapNotice } from "@/components/GapNotice";
import { ResultRow } from "@/components/ResultRow";
import { FacetGroup } from "@/components/FacetGroup";
import { SortSelect } from "@/components/SortSelect";
import { Pagination } from "@/components/Pagination";
import {
  compareBySort,
  filterCatalog,
  isCatalog,
  matchGaps,
  panelFacets,
  selectedFilters,
  unsupportedFilters,
} from "@/lib/catalog-search";
import { catalogUrl, pageOffset } from "@/lib/navigation";

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
  gaps,
}: {
  initial: DatasetSummary[];
  facets: Record<string, FacetBucket[]>;
  /** The gap register, as the coverage table above already has it.
   *
   *  Passed down rather than fetched, because the page has it server-side in
   *  both builds and a second copy over the wire buys nothing. `null` is a
   *  register that could not be read, which renders as no notice rather than as
   *  "no gap recorded" — a claim the register did not make. */
  gaps: DataGap[] | null;
}) {
  const t = useTranslations("search");
  const empty = useTranslations("empty");

  // The first page is only a preview until the complete catalog has loaded.
  const [datasets, setDatasets] = useState<DatasetSummary[]>(initial);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    fetch(`${base}/catalog.json`, { signal: controller.signal, cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((body: unknown) => {
        if (controller.signal.aborted) return;
        if (!isCatalog(body)) throw new Error("Invalid catalog");
        setDatasets(body.results);
        setState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("failed");
      });
    return () => controller.abort();
  }, [attempt]);

  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const query = params.get("q") ?? "";
  const selected = useMemo(() => selectedFilters(new URLSearchParams(params.toString())), [params]);
  const unsupported = unsupportedFilters(selected);

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
      next.delete("offset");
      if (value) next.set("q", value);
      else next.delete("q");
      write(next);
    },
    [params, write],
  );

  const sort = params.get("sort") ?? "";
  const results = useMemo(() => {
    if (state !== "ready") return initial;
    const matched = filterCatalog(datasets, query, selected);
    return sort ? [...matched].sort(compareBySort(sort)) : matched;
  }, [datasets, initial, query, selected, sort, state]);
  const offset = state === "ready" ? pageOffset(params.get("offset")) : 0;

  /** Whether the reader has asked for something narrower than "the catalog". */
  const narrowed = Boolean(query) || Object.values(selected).some((v) => v.length > 0);

  /**
   * Whether the prerendered rows are worth showing yet.
   *
   * The first page of the catalog is a fair preview *of the catalog*. It is not
   * a preview of an answer to a filter, and it was being shown as one: opening
   * `?record_type=reference_model` listed all nineteen records, most of them
   * datasets, under a "Preview" label, and then swapped them for the three
   * matches when `catalog.json` landed. A caption does not make a list of
   * non-matches an answer, and a reader who looked once saw a filter that had
   * not worked.
   *
   * So a narrowed view waits. The status card below still says what is
   * happening, which is the honest thing to show while there is no answer yet.
   */
  const previewable = state === "ready" || !narrowed;
  const returnTo = catalogUrl(new URLSearchParams(params.toString()));

  function toggle(field: string, value: string) {
    const next = new URLSearchParams(params.toString());
    next.delete("offset");
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
    if (sort) next.set("sort", sort);
    write(next);
  }

  const hasFilters = Object.values(selected).some((v) => v.length > 0);
  const entries = panelFacets(facets);

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
              {state !== "ready"
                ? narrowed
                  ? t("searchingCount")
                  : t("previewCount", { count: initial.length })
                : unsupported.length ? t("unsupportedTitle") : t("resultsCount", { count: results.length })}
            </p>
            <SortSelect />
          </div>

          {state !== "ready" ? (
            <div role="status" className="og-card space-y-2 p-3 text-sm text-[color:var(--muted)]">
              <p>{state === "loading" ? t("loadingCatalog") : t("catalogUnavailable")}</p>
              <p>{narrowed ? t("searchingHelp") : t("previewHelp")}</p>
              {state === "failed" ? (
                <button type="button" className="og-cta" onClick={() => {
                  setState("loading");
                  setAttempt((n) => n + 1);
                }}>{t("retry")}</button>
              ) : null}
            </div>
          ) : null}

          {unsupported.length > 0 ? (
            <EmptyState title={t("unsupportedTitle")}>
              <p>{t("unsupportedHelp", { filters: unsupported.join(", ") })}</p>
              <button type="button" className="og-cta mt-3" onClick={clearFilters}>{t("clearFilters")}</button>
            </EmptyState>
          ) : state === "ready" && results.length === 0 ? (
            <EmptyState
              title={empty("noResults")}
              // The same way out the live build offers. It had none here, so
              // the one page where a reader is most stuck was the one with no
              // control on it.
              action={narrowed ? { href: "/datasets", label: empty("noResultsAction") } : undefined}
            >
              <p>{empty("noResultsHelp", {
                total: datasets.length,
                column: "ssrd",
                concept: "globalHorizontalIrradiance",
              })}</p>
              {/* The answer this catalog can give that a search engine cannot,
                  and until now only the live build gave it. A reader who typed
                  "max upward ramp" and got nothing has learned the catalog is
                  small; told that nothing open supplies it, with who found that
                  and when, they have learned something true about the field. */}
              <GapNotice gaps={matchGaps(gaps, query)} t={empty} />
            </EmptyState>
          ) : previewable ? (
            <>
              <ul className="space-y-4">
                {results.slice(offset, offset + PAGE_SIZE).map((dataset) => (
                  <ResultRow key={dataset.id} dataset={dataset} returnTo={returnTo} />
                ))}
              </ul>
              {state === "ready" ? <Pagination total={results.length} offset={offset} limit={PAGE_SIZE} /> : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

