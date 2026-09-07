"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { DatasetSummary } from "@/lib/api";
import { cadenceText, formatCadence, formatSpan, iriTail } from "@/lib/format";
import { CoverageMap, CoverageTimeline } from "@/components/Coverage";
import { QualityBadges } from "@/components/QualityBadges";

/**
 * One list row (PRD §F3): title, creator, summary, domain, provenance and
 * licence tags, temporal coverage as a timeline, geographic coverage as an
 * area, completeness level, and the three quality badges.
 *
 * Everything a modeller needs to reject a dataset without opening it. Rejecting
 * quickly is most of what a catalog is for — there are ten thousand datasets
 * and one of them is right.
 *
 * **A client component, and the reason is payload.** The page used to build
 * every row on the server and hand the lot to `StaticSearch` as elements, so
 * the landing page carried each record twice: once as the summary the filter
 * reads, and again as a serialised React element tree in the flight payload.
 * At 444 records that was 9 MB and `ops/check-page-weight.sh` stopped the
 * deploy.
 *
 * The second copy bought nothing. `StaticSearch` calls `useSearchParams`, so
 * Next bails out of prerendering its whole subtree and the built HTML contains
 * no result rows either way — measured, not assumed: the old build's
 * `index.html` has zero links to a dataset in it. Those 443 rows were paid for
 * on every page load and rendered on none of them.
 *
 * Rendering here, from the data that already ships, means the page carries each
 * record once and renders a page of them at a time.
 */
export function ResultRow({ dataset }: { dataset: DatasetSummary }) {
  const t = useTranslations("dataset");
  const c = useTranslations("coverage");
  // Hoisted rather than called inline: a hook inside an argument list reads as
  // conditional even where it is not, and the rules-of-hooks lint is right to
  // dislike it.
  const cadenceMessages = useTranslations("cadence");

  const span = formatSpan(dataset.temporal?.start, dataset.temporal?.end);
  const cadence = cadenceText(
    formatCadence(dataset.temporal?.update_cadence),
    cadenceMessages,
  );
  const levelKey = String(dataset.completeness_level) as "1" | "2" | "3";

  return (
    <li className="og-card p-5">
      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <Link
              href={`/datasets/${dataset.id}`}
              className="text-lg font-semibold hover:underline"
            >
              {dataset.title}
            </Link>
            {dataset.reference_only ? (
              <span
                className="px-1.5 py-0.5 text-[11px] font-semibold"
                style={{
                  borderRadius: "var(--radius)",
                  background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                  color: "var(--accent-text)",
                }}
                title={t("referenceOnlyHelp")}
              >
                {t("referenceOnly")}
              </span>
            ) : null}
          </div>

          {dataset.publisher ? (
            <p className="mt-0.5 text-sm text-[color:var(--muted)]">{dataset.publisher}</p>
          ) : null}

          {dataset.summary ? <p className="mt-2 text-sm">{dataset.summary}</p> : null}

          <ul className="mt-3 flex flex-wrap gap-1.5 text-xs">
            {dataset.data_domains.map((domain) => (
              <Tag key={domain.iri}>{domain.label ?? iriTail(domain.iri)}</Tag>
            ))}
            {dataset.provenance_class ? <Tag>{iriTail(dataset.provenance_class)}</Tag> : null}
            {dataset.license_id ? <Tag>{iriTail(dataset.license_id)}</Tag> : null}
            <Tag title={t(`levelHelp.${levelKey}`)}>
              {t("level", { level: dataset.completeness_level })} ·{" "}
              {t(`levelNames.${levelKey}`)}
            </Tag>
          </ul>

          <div className="mt-3">
            <QualityBadges facets={dataset.quality} />
          </div>

          {span ? (
            <div className="mt-3 max-w-sm">
              <CoverageTimeline
                start={dataset.temporal?.start}
                end={dataset.temporal?.end}
              />
              {cadence ? (
                <p className="mt-1 text-xs text-[color:var(--muted)]">
                  {c("cadence")}: {cadence}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>

        {dataset.spatial?.bbox ? (
          <div className="w-full shrink-0 sm:w-40">
            <CoverageMap
              bbox={dataset.spatial.bbox}
              className="h-20 w-full border"
              style={{ borderColor: "var(--border)", borderRadius: "var(--radius)" }}
            />
          </div>
        ) : null}
      </div>
    </li>
  );
}

function Tag({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <li className="og-tag" title={title}>
      {children}
    </li>
  );
}
