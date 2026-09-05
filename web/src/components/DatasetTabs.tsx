"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import type {
  DatasetDetail,
  DistributionDetail,
  LinkHealth,
  LinksResponse,
  QualityResponse,
  SchemaResponse,
} from "@/lib/api";
import { BboxSummary, CoverageMap, CoverageTimeline, bboxToWkt } from "@/components/Coverage";
import { Connections } from "@/components/Connections";
import { EmptyState, NotCaptured } from "@/components/EmptyState";
import { formatBytes, formatCadence, formatDate, formatNumber, iriTail } from "@/lib/format";

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
            role="tab"
            id={`tab-${tab}`}
            aria-selected={active === tab}
            aria-controls={`panel-${tab}`}
            onClick={() => setActive(tab)}
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
            {tab === "schema" ? <Schema schema={schema} /> : null}
            {tab === "quality" ? <Quality quality={quality} dataset={dataset} /> : null}
            {tab === "connections" ? <ConnectionsTab links={links} /> : null}
            {tab === "downloads" ? <Downloads distributions={distributions} /> : null}
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
                  <Link href={`/datasets/${iriTail(iri)}`} className="text-[color:var(--accent)] hover:underline">
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
              className="text-[color:var(--accent)] hover:underline"
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
              className="text-[color:var(--accent)] hover:underline"
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
            {formatCadence(dataset.temporal?.update_cadence) ?? <NotCaptured />}
          </Row>
          <Row label={t("resolution")}>
            {formatCadence(dataset.temporal?.time_resolution) ?? <NotCaptured />}
          </Row>
        </Rows>
      </section>
    </div>
  );
}

function Schema({ schema }: { schema: SchemaResponse | null }) {
  const t = useTranslations("schema");
  const empty = useTranslations("empty");

  if (!schema || schema.fields.length === 0) {
    return (
      <EmptyState title={empty("noSchema")}>
        <p>{schema?.unavailable_reason ?? empty("noSchema")}</p>
      </EmptyState>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] text-sm">
        <thead>
          <tr className="border-b text-left" style={{ borderColor: "var(--border)" }}>
            <th className="py-2 pr-4 font-medium">{t("field")}</th>
            <th className="py-2 pr-4 font-medium">{t("definition")}</th>
            <th className="py-2 pr-4 font-medium">{t("type")}</th>
            <th className="py-2 pr-4 font-medium">{t("unit")}</th>
            <th className="py-2 pr-4 font-medium">{t("concept")}</th>
            <th className="py-2 font-medium">{t("basis")}</th>
          </tr>
        </thead>
        <tbody>
          {schema.fields.map((field) => (
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
                    {field.concept.label ?? iriTail(field.concept.iri)}
                    {field.concept_inferred ? (
                      <span
                        className="ml-1.5 px-1 text-[10px] font-medium"
                        style={{
                          borderRadius: "var(--radius)",
                          background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                          color: "var(--accent)",
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
              <td className="py-2">{field.value_basis ?? <NotCaptured />}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 max-w-prose text-sm text-[color:var(--muted)]">{t("gapHelp")}</p>
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

function Downloads({ distributions }: { distributions: DistributionDetail[] }) {
  const t = useTranslations("downloads");
  const empty = useTranslations("empty");

  if (!distributions.length) {
    return <EmptyState title={empty("noDistributions")} />;
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

            {dist.access_url ? (
              <a
                href={dist.access_url}
                rel="noreferrer noopener"
                className="og-cta mt-3"
              >
                {t("openSource")} ↗
              </a>
            ) : null}
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
