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
 * The API's address **as a browser should use it**, which is not always the one
 * this server uses.
 *
 * In any containerised deployment they differ. `web/Dockerfile` sets
 * `DATAHUB_API_URL=http://api:8000` so server-side fetches resolve over the
 * compose network — correct, and unusable in a browser, where `api` is not a
 * hostname that exists. Everything rendered *into the page* for a reader to
 * click, copy or configure has to use this instead: the sign-in link, the
 * OpenAPI and docs links, the MCP server URL.
 *
 * It was `apiUrl()` for all of them, so the shipped compose stack rendered
 * `http://api:8000/v1/auth/login/google` as the href of the sign-in button —
 * a dead link on every deployment that is not a developer's laptop.
 *
 * Read per request from the server's environment, like `DATAHUB_API_URL` and
 * for the same reason — **not** as a `NEXT_PUBLIC_` value. Next inlines any
 * `NEXT_PUBLIC_*` reference into the bundle when the image is built, which
 * would freeze the public address to whatever the build machine was told and
 * reintroduce exactly the trap `next.config.ts` refuses the `env` block over.
 * Every page that renders one of these URLs awaits `perRequest()`, so there is
 * a request and an environment to read when it does; one image serves any
 * deployment.
 *
 * That also means this is server-only. A client component calling it would
 * read an environment that is not there, get the fallback, and render
 * `http://localhost:8000` — so if one ever needs the address, pass it down as
 * a prop from the server component that rendered it.
 *
 * Falls back to the server's URL, which is right for development, where the two
 * genuinely are the same address.
 */
export function publicApiUrl(): string {
  return process.env.DATAHUB_PUBLIC_API_URL || apiUrl();
}

/**
 * Whether the configured API URL is one a stranger could actually open.
 *
 * On a developer's machine `http://localhost:8000` is exactly right — it is
 * where their own API is. Baked into a site published to the world it is a
 * link to the reader's own machine, which is not running anything. The
 * Developers page shipped two of those.
 *
 * Loopback was the only case this detected, on the reasoning that "any other
 * host is at least *plausibly* reachable". That reasoning was wrong for the
 * deployment this project actually ships: `http://api:8000` passed the check and
 * rendered as a live link, and `api` is a compose service name that resolves
 * nowhere outside the container network. A single-label hostname — no dot at all
 * — is never public, so it is caught here too.
 */
export function isReachableByStrangers(url: string = publicApiUrl()): boolean {
  try {
    const { hostname } = new URL(url);
    if (["localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"].includes(hostname)) return false;
    // `api`, `web`, `datahub` — a container or service name. A public host has a
    // dot in it, and an IPv6 literal arrives bracketed.
    return hostname.includes(".") || hostname.startsWith("[");
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
/** The API's session cookie. Set by `/v1/auth/callback`; see `SESSION_COOKIE`
 * in `datahub.api.entitlement.resolve`. */
export const SESSION_COOKIE = "og_session";

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

type Options = RequestInit & { revalidate?: number; authenticated?: boolean };

async function request<T>(path: string, init: Options = {}): Promise<T> {
  if (IS_SNAPSHOT) return snapshotRead<T>(path);

  const { revalidate, authenticated, ...rest } = init;
  const session = authenticated ? await sessionHeader() : {};
  const response = await fetch(`${apiUrl()}${path}`, {
    ...rest,
    headers: { Accept: "application/json", ...session, ...(rest.headers ?? {}) },
    // A response that depends on who is asking must never be cached. The
    // request that carries a cookie is opted out here rather than at each call
    // site, because "forgot to say no-store on the authenticated one" serves
    // one reader's view of the catalog to the next.
    ...(authenticated ? { cache: "no-store" as const } : {}),
    next: authenticated || revalidate === undefined ? undefined : { revalidate },
    redirect: "manual",
  });

  if (response.status === 404) {
    throw new NotFoundError(404, "not found");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, string>;
    throw new ApiError(response.status, body.detail ?? body.title ?? response.statusText);
  }
  // 204 has no body to parse. `logout` is one.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * The caller's session cookie, forwarded from the incoming request.
 *
 * Server-side fetches used to send nothing, so a signed-in steward's browser
 * carried a session the Next server then dropped on the floor: every API read
 * was anonymous and `reviewQueue()` answered 401 to a steward who was, in fact,
 * signed in.
 *
 * Only the session cookie, and only where it is asked for. Forwarding the whole
 * `Cookie` header would hand the API every unrelated cookie the site ever set,
 * and forwarding by default would attach an identity to the cached anonymous
 * reads that make up nearly all of the traffic.
 *
 * `next/headers` is imported here rather than at the top of the file so that a
 * client component importing a *type* from this module never drags a
 * server-only import into a browser bundle.
 */
async function sessionHeader(): Promise<Record<string, string>> {
  const { cookies } = await import("next/headers");
  const session = (await cookies()).get(SESSION_COOKIE);
  return session ? { Cookie: `${SESSION_COOKIE}=${session.value}` } : {};
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
  /** Descriptive text for the static site's search, present only in a snapshot.
   *
   *  The API indexes `description` but does not return it in a list row, and the
   *  static site has only list rows — so without this, a record with no summary
   *  is unfindable by any word describing it here while the live API finds it.
   *  `datahub snapshot export` fills it in; the live API never sends it. */
  search_text?: string | null;
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
  /** Why a reference-only record has no access path — restricted, commercial,
   *  superseded, or that the inventory records no reason. The Downloads tab's
   *  entire content for a record with no distributions, which is the point of
   *  cataloguing one: the gap is the information. */
  pointer_rationale?: string | null;
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
  /** Where this field's values came from — the upstream field or dataset it was
   *  read out of. The API has always sent it; this type did not declare it and
   *  the Schema tab did not render it, so field-level provenance arrived in the
   *  browser and was dropped (§F3, #40). It is the evidence behind the
   *  Provenance grade: a reader told a dataset grades B deserves to see which
   *  fields are the reason. */
  field_sources?: string[];
  /** The fields this one was computed from, where it is derived rather than
   *  measured. Distinct from `field_sources`: that says where a value came
   *  from, this says what it was made out of. */
  derived_from?: string[];
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
    authenticated: true,
  });

// ---------------------------------------------------------------------------
// Signing in
// ---------------------------------------------------------------------------

export interface MeResponse {
  authenticated: boolean;
  principal_id?: string | null;
  email?: string | null;
  role?: string | null;
  is_agent: boolean;
  is_steward: boolean;
  custodian_of: string[];
}

export interface ProviderList {
  providers: string[];
  native_credentials: boolean;
}

/** Who the API thinks is asking. Answers `{authenticated: false}` rather than
 *  401 for a signed-out caller, so there is no special case for the state the
 *  site spends most of its time in. */
export const me = () => request<MeResponse>("/v1/auth/me", { authenticated: true });

/** The sign-in providers this deployment actually has credentials for. Cached
 *  briefly: it changes when the deployment is reconfigured, not per reader. */
export const authProviders = () =>
  request<ProviderList>("/v1/auth/providers", { revalidate: LIST_REVALIDATE });

/** Where a browser goes to begin a federated sign-in. The API owns the flow —
 *  PKCE, state, the `next` check against the configured origins — so this is a
 *  URL, not a fetch. */
export function loginUrl(provider: string, next: string): string {
  const query = new URLSearchParams({ next });
  // `publicApiUrl`, not `apiUrl`: this string becomes an anchor href that a
  // browser follows, not a URL this server fetches.
  return `${publicApiUrl()}/v1/auth/login/${encodeURIComponent(provider)}?${query}`;
}

/** End this session: revoked server-side, not just forgotten here. */
export const logout = () =>
  request<void>("/v1/auth/logout", { method: "POST", authenticated: true });

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
