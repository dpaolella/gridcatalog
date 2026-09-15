import type { DatasetSummary } from "./api";

/** The filters supported by the exported catalog. Unknown parameters are
 * reported by the page, never silently treated as successful filters. */
export const STATIC_FILTERS = [
  "record_type", "fidelity_class", "data_domain", "provenance_class", "license",
  "format", "completeness_level", "field_count_bucket", "spatial_granularity",
  "anonymous_access", "link_health", "has_usage_evidence", "concept", "place",
  "domain_coverage",
] as const;

/** Facets the filter panel does not render as a checkbox list.
 *
 * `domain_coverage` is a real facet and a real filter — the coverage view
 * reads it and links cells to it — but its values are `{IRI}|{level}` pairs,
 * which is a key and not a label. Rendered in the panel it would be twenty
 * unreadable checkboxes duplicating the two groups either side of it.
 *
 * Excluded by name here rather than by a shape heuristic: a panel that hid
 * whatever looked unreadable would hide the next facet somebody adds badly,
 * quietly, which is how a filter comes to exist and never be offered. */
export const PANEL_HIDDEN = new Set<string>(["domain_coverage"]);

export function panelFacets<T>(facets: Record<string, T[]>): [string, T[]][] {
  return Object.entries(facets).filter(
    ([field, buckets]) => buckets.length > 0 && !PANEL_HIDDEN.has(field),
  );
}
const CONTROLS = new Set(["q", "sort", "offset"]);

export function selectedFilters(params: URLSearchParams): Record<string, string[]> {
  return Object.fromEntries(
    [...new Set(params.keys())]
      .filter((key) => !CONTROLS.has(key))
      .map((key) => [key, params.getAll(key)]),
  );
}

export function unsupportedFilters(selected: Record<string, string[]>): string[] {
  return Object.keys(selected).filter((key) => !STATIC_FILTERS.some((field) => field === key));
}

export function matches(dataset: DatasetSummary, field: string, value: string): boolean {
  switch (field) {
    case "record_type": return dataset.record_type === value;
    case "fidelity_class": return dataset.fidelity_class === value;
    case "field_count_bucket": return dataset.field_count_bucket === value;
    case "concept": return (dataset.concepts ?? []).some((c) => c.iri === value);
    case "place": return (dataset.spatial?.place_iris ?? []).includes(value);
    case "domain_coverage": {
      // The crossing, recomputed from the row rather than read off it: the
      // static site filters rows in the browser and the summary is a derived
      // value the server computes, so this is the one place the two builds
      // could drift. Deriving it the same way the document does keeps them
      // the same answer.
      const [domain, level] = value.split("|");
      return (
        String(dataset.completeness_level) === level &&
        dataset.data_domains.some((d) => d.iri === domain)
      );
    }
    case "data_domain": return dataset.data_domains.some((d) => d.iri === value);
    case "provenance_class": return dataset.provenance_class === value;
    case "license": return dataset.license_id === value;
    case "format": return (dataset.formats ?? []).includes(value);
    case "completeness_level": return String(dataset.completeness_level) === value;
    case "spatial_granularity": return dataset.spatial?.granularity === value;
    case "anonymous_access": return String(dataset.anonymous_access) === value;
    case "link_health": return dataset.worst_link_health === value;
    case "has_usage_evidence": return String(dataset.has_usage_evidence) === value;
    default: return false;
  }
}

export function filterCatalog(datasets: DatasetSummary[], query: string, selected: Record<string, string[]>) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  return datasets.filter((dataset) => {
    const text = [
      dataset.title, dataset.summary, dataset.search_text, dataset.publisher,
      ...(dataset.creators ?? []), ...dataset.data_domains.map((d) => d.label ?? d.iri),
      dataset.license_id, dataset.provenance_class, ...(dataset.formats ?? []),
      ...(dataset.spatial?.place_labels ?? []),
    ].filter(Boolean).join(" ").toLowerCase();
    return terms.every((term) => text.includes(term)) && Object.entries(selected).every(
      ([field, values]) => values.some((value) => matches(dataset, field, value)),
    );
  });
}

/** Validate enough of the envelope and rows to reject an error page or a
 * truncated export before attempting to render it. Empty success is valid. */
export function isCatalog(value: unknown): value is { total: number; results: DatasetSummary[] } {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return Array.isArray(body.results) && body.total === body.results.length && body.results.every(
    (row) => row && typeof row.id === "string" && typeof row.title === "string" &&
      Array.isArray(row.data_domains) && row.data_domains.every(
        (domain: { iri?: unknown } | null) => domain && typeof domain.iri === "string",
      ) && Array.isArray(row.quality) && typeof row.completeness_level === "number",
  );
}

/**
 * The sort orders the UI offers, and the comparator that applies one.
 *
 * Here rather than beside the `<select>` that renders them because both builds
 * now need the comparator on the *server*: `search()` sorts the snapshot index
 * before slicing a page out of it, and a `"use client"` module's exports reach
 * a server component as client references rather than as callable functions.
 *
 * Together, though, and that is the property `tests/sort.test.cjs` keeps: an
 * option the comparator does not understand is a control that silently does
 * nothing, which is indistinguishable from a catalog that happens to already be
 * in that order.
 *
 * Relevance is the absence of a `sort` parameter — the server ranks, and
 * nothing here invents a score.
 */
export const SORT_OPTIONS = [
  { value: "", labelKey: "sortRelevance" },
  { value: "-modified", labelKey: "sortRecent" },
  { value: "title", labelKey: "sortTitle" },
  { value: "-temporal_start", labelKey: "sortCoverage" },
] as const;

/** Only the fields a sort reads — so this stays usable without importing the
 *  whole `DatasetSummary`, and so adding a sortable field is a compile error
 *  here rather than a silent no-op. */
export interface Sortable {
  title?: string | null;
  modified?: string | null;
  completeness_level?: number | null;
  temporal?: { start?: string | null } | null;
}

/** Whether `sort` names a field this comparator reads. An unknown value orders
 *  nothing, so a caller that cares can decline to claim an order it did not
 *  apply rather than presenting the input sequence as one. */
export function isSortable(sort: string): boolean {
  return ["title", "modified", "temporal_start", "completeness_level"].includes(
    sort.startsWith("-") ? sort.slice(1) : sort,
  );
}

export function compareBySort<T extends Sortable>(sort: string) {
  const descending = sort.startsWith("-");
  const field = descending ? sort.slice(1) : sort;

  const read = (row: T): string | number | null => {
    if (field === "title") return row.title ?? null;
    if (field === "modified") return row.modified ?? null;
    if (field === "temporal_start") return row.temporal?.start ?? null;
    if (field === "completeness_level") return row.completeness_level ?? null;
    return null;
  };

  return (a: T, b: T): number => {
    const left = read(a);
    const right = read(b);
    // Missing values sort last whichever way the order runs. A record with no
    // coverage window is not "earliest"; it is unknown, and putting it first
    // under "Coverage start" would read as a claim about the data.
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    const order = left < right ? -1 : left > right ? 1 : 0;
    return descending ? -order : order;
  };
}

/** The gap-register entries a query names.
 *
 * PRD §5: saying what does not exist is a feature. "No datasets match this
 * search" tells a reader the catalog is small; "nothing open supplies this,
 * here is who found that and when" tells them something true about the field.
 *
 * Here rather than inside the API client because both builds owe the reader
 * that answer and only one of them had it: the live catalog called
 * `searchGaps` on an empty result and the published site did not, so the
 * strongest thing the Hub has to say was missing from exactly the deployment
 * most people will read. One matching rule, two callers — the live path fetches
 * the register and the static path is handed the copy its coverage table
 * already has.
 */
export function matchGaps<T extends { title: string; category: string; reason: string }>(
  gaps: readonly T[] | null | undefined,
  query: string,
  limit = 3,
): T[] {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  return (gaps ?? [])
    .filter((gap) =>
      tokens.every((token) =>
        `${gap.title} ${gap.category} ${gap.reason}`.toLowerCase().includes(token),
      ),
    )
    .slice(0, limit);
}
