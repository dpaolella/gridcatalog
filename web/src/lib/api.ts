/**
 * The REST client. `web` talks to the API and nothing else.
 *
 * That boundary (architecture table, PRD principle 9) is why there is no
 * SPARQL here, no store client, and — importantly — no second copy of any rule
 * the API owns. Entitlement, quality grading and link ranking are decided
 * server-side; this file's job is to ask and to type the answer.
 *
 * Every fetch is server-side. A browser holding an API token is a token an XSS
 * bug exfiltrates, and rendering on the server also means the first paint is
 * the data rather than a spinner.
 */

/**
 * Read per call, not captured at module load and never inlined at build time.
 *
 * Next's `env` config option bakes a value into the bundle when the image is
 * built, which turns one deployment mistake into a silent one: the container
 * runs, the pages render, and every request goes to localhost. Reading the
 * process environment here keeps one image usable against a local API in
 * development and a real one in production.
 */
export function apiUrl(): string {
  return process.env.DATAHUB_API_URL ?? "http://localhost:8000";
}

/**
 * Whether the configured API URL is one a stranger could actually open.
 *
 * On a developer's machine `http://localhost:8000` is exactly right — it is
 * where their own API is. Baked into a site published to the world it is a
 * link to the reader's own machine, which is not running anything. The
 * Developers page shipped two of those.
 *
 * Loopback is the only case worth detecting: any other host is at least
 * *plausibly* reachable, and a page that second-guessed a real hostname would
 * be wrong more often than the check is worth.
 */
export function isReachableByStrangers(url: string = apiUrl()): boolean {
  try {
    const { hostname } = new URL(url);
    return !["localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"].includes(hostname);
  } catch {
    return false;
  }
}

/**
 * Where the data comes from.
 *
 * Two modes, one codebase:
 *
 * - **live** — every page fetches the API when it renders. What runs behind a
 *   Node server, against a catalog that changes.
 * - **snapshot** — every page reads JSON written ahead of time by
 *   `datahub snapshot export`, so the whole site can be pre-rendered and served
 *   as files. What runs on GitHub Pages, which has no process to fetch with.
 *
 * The snapshot is produced by driving the real API, so both modes read exactly
 * the same shapes. That is what lets one set of components serve both without a
 * branch anywhere except this file.
 */
export const SNAPSHOT_DIR = process.env.DATAHUB_SNAPSHOT ?? "";
export const IS_SNAPSHOT = SNAPSHOT_DIR !== "";

/** How long a list page may be served from cache. Short, because the catalog
 * changes when a harvest lands and a stale search lies about what exists. */
const LIST_REVALIDATE = 60;
const RECORD_REVALIDATE = 300;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** A record the caller may not see raises the same error as one that does not
 * exist. The API returns an identical 404 for both, and reconstructing the
 * difference here would rebuild the existence oracle it removed. */
export class NotFoundError extends ApiError {}

type Options = RequestInit & { revalidate?: number };

async function request<T>(path: string, init: Options = {}): Promise<T> {
  if (IS_SNAPSHOT) return snapshotRead<T>(path);

  const { revalidate, ...rest } = init;
  const response = await fetch(`${apiUrl()}${path}`, {
    ...rest,
    headers: { Accept: "application/json", ...(rest.headers ?? {}) },
    next: revalidate === undefined ? undefined : { revalidate },
    redirect: "manual",
  });

  if (response.status === 404) {
    throw new NotFoundError(404, "not found");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, string>;
    throw new ApiError(response.status, body.detail ?? body.title ?? response.statusText);
  }
  return (await response.json()) as T;
}

/**
 * Read a pre-rendered response off disk.
 *
 * Node's `fs` is imported dynamically so it never reaches a browser bundle: in
 * snapshot mode this runs only during `next build`, and the client-side search
 * that ships to the browser reads the same files over HTTP instead.
 *
 * A missing file is a `NotFoundError`, the same as a 404 — which is what makes
 * a restricted record behave identically in both modes. The exporter writes a
 * public stub for it and no detail files, so `/schema` is missing here for the
 * same reason it 404s there.
 */
async function snapshotRead<T>(path: string): Promise<T> {
  const { readFile } = await import("node:fs/promises");
  const { join } = await import("node:path");

  const file = snapshotFile(path);
  if (file === null) throw new NotFoundError(404, `no snapshot entry for ${path}`);
  try {
    return JSON.parse(await readFile(join(SNAPSHOT_DIR, file), "utf8")) as T;
  } catch {
    throw new NotFoundError(404, `no snapshot entry for ${path}`);
  }
}

/** Map an API path onto the file the exporter wrote for it. */
function snapshotFile(path: string): string | null {
  const [route] = path.split("?");
  if (route === "/v1/domains") return "domains.json";
  if (route === "/v1/datasets") return "index.json";

  const detail = /^\/v1\/datasets\/([^/]+)(?:\/(schema|quality|distributions|links))?$/.exec(
    route,
  );
  if (detail) {
    const [, id, part] = detail;
    return `datasets/${id}/${part ?? "record"}.json`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Types — mirrors of the API's response models, not of the graph.
// ---------------------------------------------------------------------------

export type Grade = "A" | "B" | "C" | "D";
export type FacetName = "provenance" | "documentation" | "currency";

export interface ConceptRef {
  iri: string;
  label?: string | null;
  definition?: string | null;
  notation?: string | null;
}

export interface QualityFacet {
  facet: FacetName;
  grade: Grade | null;
  label: string | null;
  rationale?: string | null;
  assessed: boolean;
}

export interface SpatialCoverage {
  bbox?: number[] | null;
  place_labels?: string[];
  native_crs?: string | null;
  geometry_types?: string[];
  granularity?: string | null;
  feature_count?: number | null;
}

export interface TemporalCoverage {
  start?: string | null;
  end?: string | null;
  update_cadence?: string | null;
  time_resolution?: string | null;
}

export interface DatasetSummary {
  id: string;
  title: string;
  summary?: string | null;
  publisher?: string | null;
  creators?: string[];
  data_domains: ConceptRef[];
  provenance_class?: string | null;
  license_id?: string | null;
  license_url?: string | null;
  completeness_level: number;
  quality: QualityFacet[];
  spatial?: SpatialCoverage | null;
  temporal?: TemporalCoverage | null;
  anonymous_access?: boolean | null;
  bulk_download?: boolean | null;
  formats?: string[];
  distribution_count?: number;
  reference_only?: boolean;
  worst_link_health?: string | null;
}

export interface DatasetDetail extends DatasetSummary {
  iri: string;
  description?: string | null;
  persistent_id?: string | null;
  doi?: string | null;
  keywords?: string[];
  concepts?: ConceptRef[];
  supported_analysis?: ConceptRef[];
  excluded_analysis?: ConceptRef[];
  access_restriction?: string | null;
  redistribution_allowed?: boolean | null;
  has_topology?: boolean | null;
  has_impedance?: boolean | null;
  voltage_classes?: string[];
  supersedes?: string[];
  superseded_by?: string | null;
  issued?: string | null;
  modified?: string | null;
  documentation_status?: string | null;
  review_state?: string;
  harvest_source?: string | null;
  upstream_sources?: string[];
  exclusion_rationale?: string | null;
}

export interface FacetBucket {
  /** Whatever the facet's field holds: an IRI for a domain, a number for
   * completeness level, a boolean for anonymous access. Typed honestly, so a
   * component that assumes a string has to say so. */
  value: string | number | boolean;
  count: number;
  label?: string | null;
}

export interface SearchResponse {
  total: number;
  offset: number;
  limit: number;
  results: DatasetSummary[];
  facets: Record<string, FacetBucket[]>;
  took_ms: number;
}

export interface FieldDetail {
  id: string;
  local_name: string;
  label?: string | null;
  definition?: string | null;
  data_type?: string | null;
  unit?: string | null;
  unit_label?: string | null;
  concept?: ConceptRef | null;
  concept_inferred: boolean;
  inference_basis?: string | null;
  concept_gap_reason?: string | null;
  value_basis?: string | null;
  required?: boolean | null;
  completeness_caveats?: string | null;
}

export interface SchemaResponse {
  dataset_id: string;
  completeness_level: number;
  fields: FieldDetail[];
  unavailable_reason?: string | null;
}

export interface QualityResponse {
  dataset_id: string;
  facets: QualityFacet[];
  not_yet_assessed_reason?: string | null;
}

/** Link health is an object, not a status string: the probe cadence and the
 * failure count are what let a reader judge whether "unreachable" means a blip
 * or a dead dataset. */
export interface LinkHealth {
  status: string;
  last_probed_at?: string | null;
  consecutive_failures?: number;
  probe_cadence?: string | null;
  redirect_target?: string | null;
}

export interface DistributionDetail {
  id: string;
  access_url?: string | null;
  download_url?: string | null;
  media_type?: string | null;
  format_label?: string | null;
  byte_size?: number | null;
  access_restriction?: string | null;
  anonymous_access?: boolean | null;
  credential_requirement?: string | null;
  bulk_download?: boolean | null;
  supports_range_requests?: boolean;
  subsetting_protocol?: string | null;
  link_health?: LinkHealth | null;
}

export interface LinkedDataset {
  dataset_id: string;
  title?: string | null;
  relation: string;
  strength: number;
  descriptor: string;
  reasons: string[];
  joinable_keys: string[];
  shared_workflow_tags: string[];
  correlation_warning?: string | null;
  shared_origin?: string | null;
  strength_reduced_by_correlation: boolean;
}

export interface LinksResponse {
  dataset_id: string;
  links: LinkedDataset[];
  unavailable_reason?: string | null;
}

export interface DomainSummary {
  id: string;
  /** The concept IRI, which is what the `data_domain` filter takes. */
  iri: string;
  notation: string;
  label: string;
  definition?: string | null;
  /** What is genuinely unavailable in this domain and why. A product feature,
   * not a disclaimer: a catalog that says what does not exist is more useful
   * than one that silently returns nothing (PRD §5). */
  structural_note?: string | null;
  v1_ingestion_scope?: string | null;
  dataset_count: number;
  alt_labels?: string[];
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

export async function search(
  params: Record<string, string | string[] | undefined>,
): Promise<SearchResponse> {
  if (IS_SNAPSHOT) {
    // The snapshot holds the whole public catalog in one file, and the static
    // site filters it in the browser. Server-side here would mean pre-rendering
    // a page per query, which is not a finite set.
    const index = await request<{ total: number; results: DatasetSummary[] }>("/v1/datasets");
    const facets = await snapshotFacets();
    return { ...index, offset: 0, limit: index.results.length, facets, took_ms: 0 };
  }

  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    for (const item of Array.isArray(value) ? value : [value]) query.append(key, item);
  }
  return request<SearchResponse>(`/v1/datasets?${query}`, { revalidate: LIST_REVALIDATE });
}

async function snapshotFacets(): Promise<Record<string, FacetBucket[]>> {
  const { readFile } = await import("node:fs/promises");
  const { join } = await import("node:path");
  try {
    return JSON.parse(await readFile(join(SNAPSHOT_DIR, "facets.json"), "utf8")) as Record<
      string,
      FacetBucket[]
    >;
  } catch {
    return {};
  }
}

/** Every dataset id in the snapshot, for `generateStaticParams`. */
export async function snapshotDatasetIds(): Promise<string[]> {
  if (!IS_SNAPSHOT) return [];
  const index = await request<{ results: DatasetSummary[] }>("/v1/datasets");
  return index.results.map((d) => d.id);
}

export const getDataset = (id: string) =>
  request<DatasetDetail>(`/v1/datasets/${id}`, { revalidate: RECORD_REVALIDATE });

export const getSchema = (id: string) =>
  request<SchemaResponse>(`/v1/datasets/${id}/schema`, { revalidate: RECORD_REVALIDATE });

export const getQuality = (id: string) =>
  request<QualityResponse>(`/v1/datasets/${id}/quality`, { revalidate: RECORD_REVALIDATE });

export const getDistributions = (id: string) =>
  request<DistributionDetail[]>(`/v1/datasets/${id}/distributions`, {
    revalidate: RECORD_REVALIDATE,
  });

export const getLinks = (id: string) =>
  request<LinksResponse>(`/v1/datasets/${id}/links`, { revalidate: RECORD_REVALIDATE });

/** Returns a bare array. Typed as one rather than assumed to be an envelope:
 * the two shapes are one refactor apart, and the failure is a 500 on a page
 * that worked yesterday. */
export const getDomains = () =>
  request<DomainSummary[]>(`/v1/domains`, { revalidate: LIST_REVALIDATE });

export interface ReviewItem {
  dataset_id: string;
  state: string;
  source_id?: string | null;
  data_domain?: string | null;
  completeness_level: number;
  inbound_link_count: number;
  validation_conforms: boolean;
  violations: unknown[];
  confirmed_fields: string[];
  conflict_detail: unknown[];
  steward_notes?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}

export interface ReviewQueueResponse {
  state: string;
  items: ReviewItem[];
  total: number;
}

/** Steward only. Never cached: a queue served from a cache shows a steward work
 * somebody else finished ten seconds ago, which is how two people review the
 * same record. */
export const reviewQueue = (state = "draft") =>
  request<ReviewQueueResponse>(`/v1/review?state=${encodeURIComponent(state)}`, {
    cache: "no-store",
  });

export function submitDataset(body: unknown) {
  return request<{ id: string; status: string; message: string }>("/v1/submissions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function reportIssue(body: unknown) {
  return request<{ id: string; status: string; message: string }>("/v1/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
