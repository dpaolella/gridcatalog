import type { DatasetSummary } from "./api";

/** The filters supported by the exported catalog. Unknown parameters are
 * reported by the page, never silently treated as successful filters. */
export const STATIC_FILTERS = [
  "record_type", "fidelity_class", "data_domain", "provenance_class", "license",
  "format", "completeness_level", "field_count_bucket", "spatial_granularity",
  "anonymous_access", "link_health", "has_usage_evidence", "concept", "place",
] as const;
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
