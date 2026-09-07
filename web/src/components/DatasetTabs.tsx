"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo, useRef, useState } from "react";
import type {
  DatasetDetail,
  DistributionDetail,
  FieldDetail,
  LinkHealth,
  LinksResponse,
  QualityResponse,
  SchemaResponse,
} from "@/lib/api";
import { BboxSummary, CoverageMap, CoverageTimeline, bboxToWkt } from "@/components/Coverage";
import { Connections } from "@/components/Connections";
import { EmptyState, NotCaptured } from "@/components/EmptyState";
import { ReportIssue } from "@/components/ReportIssue";
import {
  cadenceText,
  formatBytes,
  formatCadence,
  formatDate,
  formatNumber,
  iriTail,
} from "@/lib/format";

/**
 * The seven tabs from PRD §F3, in the order the PRD lists them — which is also
 * the order a modeller reads them: what is it, where did it come from, what
 * does it cover, what is in it, how good is it, what else goes with it, how do
 * I get it.
 *
 * All seven are rendered and hidden with CSS rather than mounted on click, so
 * browser find-in-page reaches content the user has not clicked to. A tab that
 * has to be opened before Ctrl-F can see it is a tab whose content is
 * effectively missing.
 */

type Tab = "overview" | "provenance" | "coverage" | "schema" | "quality" | "connections" | "downloads";

const TABS: Tab[] = [
  "overview",
  "provenance",
  "coverage",
  "schema",
  "quality",
  "connections",
  "downloads",
];

export function DatasetTabs({
  dataset,
  schema,
  quality,
  distributions,
  links,
}: {
  dataset: DatasetDetail;
  schema: SchemaResponse | null;
  quality: QualityResponse | null;
  distributions: DistributionDetail[];
  links: LinksResponse | null;
}) {
  const t = useTranslations("dataset.tabs");
  const [active, setActive] = useState<Tab>("overview");
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});

  /**
   * Arrow keys move between tabs, and only the selected one is a tab stop.
   *
   * WAI-ARIA's tab pattern asks for both, and this had neither: every one of the
   * seven tabs was in the tab order, so a keyboard user pressed Tab seven times
   * to get past the strip to the panel, and the arrow keys — the thing the
   * pattern trains people to reach for — did nothing at all.
   *
   * Selection follows focus, which is the right choice here because every panel
   * is already rendered (see the note above); moving focus reveals content
   * without a fetch, so requiring a second keypress would be ceremony.
   */
  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    const delta =
      event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    let next: Tab | null = null;
    if (delta !== 0) {
      const index = TABS.indexOf(active);
      next = TABS[(index + delta + TABS.length) % TABS.length];
    } else if (event.key === "Home") {
      next = TABS[0];
    } else if (event.key === "End") {
      next = TABS[TABS.length - 1];
    }
    if (next === null) return;
    event.preventDefault();
    setActive(next);
    tabRefs.current[next]?.focus();
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Dataset detail"
        className="flex flex-wrap gap-x-1 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        {TABS.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            role="tab"
            id={`tab-${tab}`}
            aria-selected={active === tab}
            aria-controls={`panel-${tab}`}
            // Roving tabindex: one stop for the whole strip, not seven.
            tabIndex={active === tab ? 0 : -1}
            onClick={() => setActive(tab)}
            onKeyDown={onKeyDown}
            className="-mb-px border-b-2 px-3 py-2.5 text-sm transition-colors"
            style={{
              borderColor: active === tab ? "var(--accent)" : "transparent",
              color: active === tab ? "var(--foreground)" : "var(--muted)",
              fontWeight: active === tab ? 600 : 400,
            }}
          >
            {t(tab)}
          </button>
        ))}
      </div>

      <div className="py-6">
        {TABS.map((tab) => (
          <section
            key={tab}
            role="tabpanel"
            id={`panel-${tab}`}
            aria-labelledby={`tab-${tab}`}
            hidden={active !== tab}
          >
            {tab === "overview" ? <Overview dataset={dataset} /> : null}
            {tab === "provenance" ? (
              <Provenance dataset={dataset} distributions={distributions} />
            ) : null}
            {tab === "coverage" ? <Coverage dataset={dataset} /> : null}
            {tab === "schema" ? (
              <Schema schema={schema} datasetId={dataset.id} datasetTitle={dataset.title} />
            ) : null}
            {tab === "quality" ? <Quality quality={quality} dataset={dataset} /> : null}
            {tab === "connections" ? <ConnectionsTab links={links} /> : null}
            {tab === "downloads" ? (
              <Downloads dataset={dataset} distributions={distributions} />
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Overview({ dataset }: { dataset: DatasetDetail }) {
  const t = useTranslations("overview");
  const d = useTranslations("dataset");

  return (
    <div className="space-y-6">
      {dataset.description ? (
        <p className="max-w-prose whitespace-pre-line">{dataset.description}</p>
      ) : null}

      <Rows>
        <Row label={d("identifier")}>
          <code className="text-xs">{dataset.persistent_id ?? dataset.iri}</code>
        </Row>
        <Row label={d("domains")}>
          {dataset.data_domains.map((c) => c.label ?? iriTail(c.iri)).join(", ") || <NotCaptured />}
        </Row>
        {dataset.creators?.length ? (
          <Row label={d("creators")}>{dataset.creators.join(", ")}</Row>
        ) : null}
        {dataset.keywords?.length ? (
          <Row label={d("keywords")}>{dataset.keywords.join(", ")}</Row>
        ) : null}
      </Rows>

      <section>
        <h2 className="mb-3 font-semibold">{t("fitness")}</h2>
        <Rows>
          <Row label={t("supported")}>
            {dataset.supported_analysis?.length ? (
              dataset.supported_analysis.map((c) => c.label ?? iriTail(c.iri)).join(", ")
            ) : (
              <NotCaptured />
            )}
          </Row>
          <Row label={t("excluded")}>
            {dataset.excluded_analysis?.length ? (
              <>
                {dataset.excluded_analysis.map((c) => c.label ?? iriTail(c.iri)).join(", ")}
                {dataset.exclusion_rationale ? (
                  <p className="mt-1 text-sm text-[color:var(--muted)]">
                    {dataset.exclusion_rationale}
                  </p>
                ) : null}
              </>
            ) : (
              <NotCaptured />
            )}
          </Row>
        </Rows>
        <p className="mt-2 max-w-prose text-sm text-[color:var(--muted)]">
          {t("exclusionHelp")}
        </p>
      </section>

      <section>
        <h2 className="mb-3 font-semibold">{t("structure")}</h2>
        <Rows>
          <Row label={t("hasTopology")}>
            <Bool value={dataset.has_topology} />
          </Row>
          <Row label={t("hasImpedance")}>
            <Bool value={dataset.has_impedance} />
          </Row>
          <Row label={t("voltageClasses")}>
            {dataset.voltage_classes?.length ? dataset.voltage_classes.join(", ") : <NotCaptured />}
          </Row>
        </Rows>
      </section>
    </div>
  );
}

function Provenance({
  dataset,
  distributions,
}: {
  dataset: DatasetDetail;
  distributions: DistributionDetail[];
}) {
  const t = useTranslations("provenance");

  return (
    <div className="space-y-6">
      <Rows>
        <Row label={t("class")}>
          {dataset.provenance_class ? iriTail(dataset.provenance_class) : <NotCaptured />}
        </Row>
        <Row label={t("upstream")}>
          {dataset.upstream_sources?.length ? (
            <ul className="space-y-0.5">
              {dataset.upstream_sources.map((iri) => (
                <li key={iri}>
                  <Link href={`/datasets/${iriTail(iri)}`} className="text-[color:var(--accent-text)] hover:underline">
                    {iriTail(iri)}
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <span>
              <NotCaptured />
              <span className="ml-2 text-sm text-[color:var(--muted)]">
                {t("upstreamAbsentHelp")}
              </span>
            </span>
          )}
        </Row>
        {dataset.superseded_by ? (
          <Row label={t("supersededBy")}>
            <Link
              href={`/datasets/${iriTail(dataset.superseded_by)}`}
              className="text-[color:var(--accent-text)] hover:underline"
            >
              {iriTail(dataset.superseded_by)}
            </Link>
            <span className="ml-2 text-sm text-[color:var(--muted)]">{t("supersededHelp")}</span>
          </Row>
        ) : null}
        {dataset.supersedes?.length ? (
          <Row label={t("supersedes")}>
            {dataset.supersedes.map((iri) => iriTail(iri)).join(", ")}
          </Row>
        ) : null}
        <Row label={t("licenseDetail")}>
          {dataset.license_url ? (
            <a
              href={dataset.license_url}
              className="text-[color:var(--accent-text)] hover:underline"
              rel="noreferrer noopener"
            >
              {dataset.license_id ? iriTail(dataset.license_id) : dataset.license_url}
            </a>
          ) : dataset.license_id ? (
            iriTail(dataset.license_id)
          ) : (
            <NotCaptured />
          )}
        </Row>
        <Row label={t("redistribution")}>
          {dataset.redistribution_allowed === null || dataset.redistribution_allowed === undefined ? (
            <NotCaptured />
          ) : dataset.redistribution_allowed ? (
            t("redistributionAllowed")
          ) : (
            t("redistributionForbidden")
          )}
        </Row>
      </Rows>

      <section>
        <h2 className="mb-3 font-semibold">{t("accessTerms")}</h2>
        <ul className="space-y-2 text-sm">
          {distributions.map((dist) => (
            <li
              key={dist.id}
              className="og-card p-3">
              <p className="font-medium">{dist.format_label ?? dist.media_type ?? dist.id}</p>
              <p className="text-[color:var(--muted)]">
                {dist.access_restriction ? iriTail(dist.access_restriction) : "—"}
                {dist.credential_requirement ? ` · ${dist.credential_requirement}` : ""}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Coverage({ dataset }: { dataset: DatasetDetail }) {
  const t = useTranslations("coverage");
  const cadence = useTranslations("cadence");
  const wkt = bboxToWkt(dataset.spatial?.bbox);

  return (
    <div className="grid gap-8 md:grid-cols-2">
      <section>
        <h2 className="mb-3 font-semibold">{t("geographic")}</h2>
        <CoverageMap
          bbox={dataset.spatial?.bbox}
          className="h-40 w-full border"
          style={{ borderColor: "var(--border)", borderRadius: "var(--radius)" }}
        />
        <Rows className="mt-3">
          <Row label={t("bbox")}>
            {dataset.spatial?.bbox ? <BboxSummary bbox={dataset.spatial.bbox} /> : <NotCaptured />}
          </Row>
          <Row label={t("granularity")}>{dataset.spatial?.granularity ?? <NotCaptured />}</Row>
          <Row label={t("crs")}>{dataset.spatial?.native_crs ?? <NotCaptured />}</Row>
          <Row label={t("geometryTypes")}>
            {dataset.spatial?.geometry_types?.length ? (
              dataset.spatial.geometry_types.join(", ")
            ) : (
              <NotCaptured />
            )}
          </Row>
          <Row label={t("featureCount")}>
            {formatNumber(dataset.spatial?.feature_count) ?? <NotCaptured />}
          </Row>
          {wkt ? (
            <Row label={t("wkt")}>
              <code className="block max-w-full overflow-x-auto text-xs">{wkt}</code>
            </Row>
          ) : null}
        </Rows>
      </section>

      <section>
        <h2 className="mb-3 font-semibold">{t("temporal")}</h2>
        <CoverageTimeline start={dataset.temporal?.start} end={dataset.temporal?.end} />
        <Rows className="mt-3">
          <Row label={t("from")}>{formatDate(dataset.temporal?.start) ?? <NotCaptured />}</Row>
          <Row label={t("to")}>{formatDate(dataset.temporal?.end) ?? <NotCaptured />}</Row>
          <Row label={t("cadence")}>
            {cadenceText(formatCadence(dataset.temporal?.update_cadence), cadence) ?? (
              <NotCaptured />
            )}
          </Row>
          <Row label={t("resolution")}>
            {cadenceText(formatCadence(dataset.temporal?.time_resolution), cadence) ?? (
              <NotCaptured />
            )}
          </Row>
        </Rows>
      </section>
    </div>
  );
}

/**
 * Above this many fields the table stops being a table and becomes a wall, so
 * a filter appears. ERA5 publishes 273 variables; scrolling 273 rows to find
 * `ssrd` is not reading a schema, it is searching one badly.
 */
const FILTER_THRESHOLD = 25;

function Schema({
  schema,
  datasetId,
  datasetTitle,
}: {
  schema: SchemaResponse | null;
  datasetId: string;
  datasetTitle: string;
}) {
  const t = useTranslations("schema");
  const empty = useTranslations("empty");
  const [query, setQuery] = useState("");

  const fields = useMemo(() => {
    if (!schema) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return schema.fields;
    return schema.fields.filter((field) =>
      [field.local_name, field.label, field.definition, field.unit_label, field.concept?.label]
        .filter(Boolean)
        .some((text) => String(text).toLowerCase().includes(needle)),
    );
  }, [schema, query]);

  if (!schema || schema.fields.length === 0) {
    return (
      <EmptyState title={empty("noSchema")}>
        <p>{schema?.unavailable_reason ?? empty("noSchema")}</p>
      </EmptyState>
    );
  }

  return (
    <div className="overflow-x-auto">
      {schema.fields.length > FILTER_THRESHOLD ? (
        <div className="mb-3 flex flex-wrap items-baseline gap-3">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("filterPlaceholder")}
            aria-label={t("filterPlaceholder")}
            className="min-w-[16rem] flex-1 border px-3 py-1.5 text-sm"
            style={{ borderColor: "var(--border)", borderRadius: "var(--radius)" }}
          />
          <p className="text-xs text-[color:var(--muted)]">
            {t("fieldCount", { shown: fields.length, total: schema.fields.length })}
          </p>
        </div>
      ) : null}
      {fields.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--muted)]">{t("noneMatch")}</p>
      ) : null}
      <table className="w-full min-w-[48rem] text-sm">
        <thead>
          <tr className="border-b text-left" style={{ borderColor: "var(--border)" }}>
            <th className="py-2 pr-4 font-medium">{t("field")}</th>
            <th className="py-2 pr-4 font-medium">{t("definition")}</th>
            <th className="py-2 pr-4 font-medium">{t("type")}</th>
            <th className="py-2 pr-4 font-medium">{t("unit")}</th>
            <th className="py-2 pr-4 font-medium">{t("concept")}</th>
            <th className="py-2 pr-4 font-medium">{t("basis")}</th>
            <th className="py-2 pr-4 font-medium">{t("provenance")}</th>
            <th className="py-2">
              <span className="sr-only">{t("report")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.id} className="border-b align-top" style={{ borderColor: "var(--border)" }}>
              <td className="py-2 pr-4">
                <code className="text-xs">{field.local_name}</code>
                {field.label ? (
                  <div className="text-xs text-[color:var(--muted)]">{field.label}</div>
                ) : null}
              </td>
              <td className="max-w-sm py-2 pr-4">
                {field.definition ?? <NotCaptured />}
                {field.completeness_caveats ? (
                  <p className="mt-1 text-xs" style={{ color: "var(--status-warn)" }}>
                    {t("caveat")}: {field.completeness_caveats}
                  </p>
                ) : null}
              </td>
              <td className="py-2 pr-4">
                <code className="text-xs">{field.data_type ?? "—"}</code>
              </td>
              <td className="py-2 pr-4">{field.unit_label ?? (field.unit ? iriTail(field.unit) : <NotCaptured />)}</td>
              <td className="py-2 pr-4">
                {field.concept ? (
                  <span title={field.concept.definition ?? undefined}>
                    {/* §F3: "Each concept links to the semantic layer." It goes
                        to the catalog filtered by this concept rather than to a
                        concept page, because the useful question a reader has
                        here is "what else carries this quantity" — `concept` is
                        already a filter the search backend compiles, so the
                        answer is one link away rather than a page away. */}
                    <Link
                      href={{ pathname: "/", query: { concept: field.concept.iri } }}
                      className="underline decoration-dotted underline-offset-2"
                    >
                      {field.concept.label ?? iriTail(field.concept.iri)}
                    </Link>
                    {field.concept_inferred ? (
                      <span
                        className="ml-1.5 px-1 text-[10px] font-medium"
                        style={{
                          borderRadius: "var(--radius)",
                          background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                          color: "var(--accent-text)",
                        }}
                        title={field.inference_basis ?? t("inferredHelp")}
                      >
                        {t("inferred")}
                      </span>
                    ) : null}
                  </span>
                ) : field.concept_gap_reason ? (
                  <span
                    className="text-[color:var(--muted)]"
                    title={field.concept_gap_reason}
                  >
                    {t("gap")} ⓘ
                  </span>
                ) : (
                  <NotCaptured />
                )}
              </td>
              <td className="py-2 pr-4">{field.value_basis ?? <NotCaptured />}</td>
              {/* Field-level provenance, which §F3 asks for per field and which
                  this table used to drop on the floor — the API sends it, the
                  exporter writes it, the browser received it and nothing
                  rendered it. It is the evidence behind the Provenance grade,
                  so a reader who wants to know why a dataset grades B can see
                  which fields are the reason. */}
              <td className="py-2 pr-4">
                <FieldProvenance field={field} />
              </td>
              {/* §F3 asks for a report on any record, *field* or distribution,
                  with the reference captured automatically. `ReportIssue` has
                  taken `fieldId` since it was written and nothing ever passed
                  one, so the most reportable defect there is — a wrong unit on
                  one column — had to be filed against the whole record. */}
              <td className="py-2">
                <ReportIssue
                  compact
                  datasetId={datasetId}
                  datasetTitle={datasetTitle}
                  fieldId={field.id}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 max-w-prose text-sm text-[color:var(--muted)]">{t("gapHelp")}</p>
    </div>
  );
}

/**
 * Where one field's values came from.
 *
 * Two different statements, kept apart because they answer different questions:
 * `field_sources` is where a value was read from, `derived_from` is what it was
 * computed out of. A field can have both — a capacity factor derived from a
 * wind speed that was itself read from a reanalysis.
 *
 * IRIs are shown by their tail. The full IRI is the title, because the tail is
 * what a reader recognises and the whole thing is what they would paste into a
 * query.
 */
function FieldProvenance({ field }: { field: FieldDetail }) {
  const t = useTranslations("schema");
  const sources = field.field_sources ?? [];
  const derived = field.derived_from ?? [];

  if (sources.length === 0 && derived.length === 0) return <NotCaptured />;

  return (
    <div className="space-y-1 text-xs">
      {sources.length > 0 ? (
        <div>
          <span className="text-[color:var(--muted)]">{t("readFrom")}: </span>
          {sources.map((source, index) => (
            <span key={source}>
              {index > 0 ? ", " : null}
              <code title={source}>{iriTail(source)}</code>
            </span>
          ))}
        </div>
      ) : null}
      {derived.length > 0 ? (
        <div>
          <span className="text-[color:var(--muted)]">{t("derivedFrom")}: </span>
          {derived.map((source, index) => (
            <span key={source}>
              {index > 0 ? ", " : null}
              <code title={source}>{iriTail(source)}</code>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Quality({
  quality,
  dataset,
}: {
  quality: QualityResponse | null;
  dataset: DatasetDetail;
}) {
  const t = useTranslations("quality");
  const facets = quality?.facets ?? dataset.quality;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="font-semibold">{t("title")}</h2>
        <p className="mt-1 max-w-prose text-sm text-[color:var(--muted)]">{t("help")}</p>
      </div>

      <ul className="grid gap-4 sm:grid-cols-3">
        {(["provenance", "documentation", "currency"] as const).map((name) => {
          const facet = facets.find((f) => f.facet === name);
          return (
            <li
              key={name}
              className="og-card p-4">
              <p className="og-eyebrow">{t(name)}</p>
              <p className="mt-1 flex items-baseline gap-2">
                <span
                  className="inline-flex h-7 w-7 items-center justify-center text-sm font-semibold"
                  style={{
                    borderRadius: "var(--radius)",
                    background: facet?.grade
                      ? `var(--grade-${facet.grade.toLowerCase()})`
                      : "transparent",
                    color: facet?.grade
                      ? `var(--grade-${facet.grade.toLowerCase()}-ink)`
                      : "var(--grade-none-ink)",
                    border: facet?.grade ? "none" : "1px dashed var(--border)",
                  }}
                >
                  {facet?.grade ?? "–"}
                </span>
                <span className="font-medium">{facet?.label ?? t("notAssessed")}</span>
              </p>
              {facet?.rationale ? (
                <p className="mt-2 text-xs text-[color:var(--muted)]">{facet.rationale}</p>
              ) : null}
              {facet && !facet.assessed ? (
                <p className="mt-2 text-xs text-[color:var(--muted)]">{t("notAssessedHelp")}</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      {quality?.not_yet_assessed_reason ? (
        <p className="max-w-prose text-sm text-[color:var(--muted)]">
          {quality.not_yet_assessed_reason}
        </p>
      ) : null}

      <p className="max-w-prose text-sm text-[color:var(--muted)]">{t("visibleToAll")}</p>
    </div>
  );
}

function ConnectionsTab({ links }: { links: LinksResponse | null }) {
  const empty = useTranslations("empty");

  if (!links || links.links.length === 0) {
    return (
      <EmptyState title={empty("noLinks")}>
        <p>{links?.unavailable_reason ?? empty("noLinks")}</p>
      </EmptyState>
    );
  }
  return <Connections links={links.links} />;
}

/**
 * Where to get it — or, for a reference-only record, why you cannot.
 *
 * The second case is 34 of the seed records and it is not a degraded version of
 * the first. A CEII-designated network model, a membership-restricted outage
 * database, a commercial forward curve: the catalog lists them so the gap is
 * visible (PRD §5), and `pointer_rationale` is the whole of what it has to say.
 *
 * These used to carry a distribution pointing at
 * `https://opengrid.org/catalog/no-known-access-path` — a URL the loader
 * invented to satisfy a shape, rendered here as a live "Open at source" button
 * that went nowhere. Now they carry no distribution and this tab says why (#18).
 */
function Downloads({
  dataset,
  distributions,
}: {
  dataset: DatasetDetail;
  distributions: DistributionDetail[];
}) {
  const t = useTranslations("downloads");
  const empty = useTranslations("empty");

  if (!distributions.length) {
    return (
      <EmptyState title={dataset.pointer_rationale ? t("noneTitle") : empty("noDistributions")}>
        {dataset.pointer_rationale ? (
          <>
            <p>{dataset.pointer_rationale}</p>
            <p className="mt-3">{t("noneHelp")}</p>
          </>
        ) : null}
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <p className="max-w-prose text-sm text-[color:var(--muted)]">{t("help")}</p>
      <ul className="space-y-3">
        {distributions.map((dist) => (
          <li
            key={dist.id}
            className="og-card p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="font-medium">{dist.format_label ?? dist.media_type ?? dist.id}</p>
              {dist.link_health ? <HealthTag health={dist.link_health} /> : null}
            </div>

            <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              {dist.byte_size ? (
                <Inline label={t("size")}>{formatBytes(dist.byte_size)}</Inline>
              ) : null}
              <Inline label={t("anonymous")}>
                <Bool value={dist.anonymous_access} />
              </Inline>
              {dist.credential_requirement ? (
                <Inline label={t("credentials")}>{dist.credential_requirement}</Inline>
              ) : null}
              {dist.supports_range_requests ? <Inline label={t("rangeRequests")}>✓</Inline> : null}
              {dist.subsetting_protocol ? (
                <Inline label={t("subsetting")}>{dist.subsetting_protocol}</Inline>
              ) : null}
            </dl>

            <div className="mt-3 flex flex-wrap items-baseline gap-4">
              {dist.access_url ? (
                <a
                  href={dist.access_url}
                  rel="noreferrer noopener"
                  className="og-cta"
                >
                  {t("openSource")} ↗
                </a>
              ) : null}
              {/* Per distribution, not per record. A dataset commonly has an
                  anonymous bulk copy and an account-gated API, and "this link
                  is broken" is about one of them — which is why the API has
                  taken `distribution_id` since it was written, and why filing
                  every dead URL against the whole record made the reports
                  harder to act on than the defects. */}
              <ReportIssue
                compact
                datasetId={dataset.id}
                datasetTitle={dataset.title}
                distributionId={dist.id}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function HealthTag({ health }: { health: LinkHealth }) {
  const t = useTranslations("downloads.health");
  const help = useTranslations("downloads");
  const colour =
    health.status === "verified"
      ? "var(--status-ok)"
      : health.status === "unreachable"
        ? "var(--status-alert)"
        : "var(--status-warn)";
  const key = health.status as "verified" | "degraded" | "unreachable" | "redirected";
  const when = formatDate(health.last_probed_at);
  return (
    <span
      className="text-xs"
      style={{ color: colour }}
      title={when ? help("healthHelp", { when }) : undefined}
    >
      ● {t.has(key) ? t(key) : health.status}
    </span>
  );
}

// ---------------------------------------------------------------------------

function Rows({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <dl className={`grid gap-x-6 gap-y-2 sm:grid-cols-[10rem_1fr] ${className}`}>{children}</dl>;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-sm text-[color:var(--muted)]">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </>
  );
}

function Inline({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="inline text-[color:var(--muted)]">{label}: </dt>
      <dd className="inline">{children}</dd>
    </div>
  );
}

function Bool({ value }: { value?: boolean | null }) {
  const t = useTranslations("common");
  if (value === null || value === undefined) return <NotCaptured />;
  return <>{value ? t("yes") : t("no")}</>;
}
