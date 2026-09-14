import Link from "next/link";
import { getTranslations } from "next-intl/server";
import type { DataGap, FacetBucket } from "@/lib/api";
import {
  LEVELS,
  coverageKey,
  domainCoverage,
  unplacedGaps,
} from "@/lib/coverage";

/**
 * What the catalog holds, by domain and by how well described it is (#99).
 *
 * The two facts were already on the page and neither answered the question a
 * modeller has. "DD8 holds one record" says nothing about whether it is usable;
 * "seven records are level 1" says nothing about where. Crossed, the table says
 * the useful thing in one glance: this domain has four records and all of them
 * are discoverable-only, that one has three and two are linked.
 *
 * **The line this must not cross.** A thin row means *the catalog holds little
 * here*. It does not mean little exists — that is a claim about the whole open
 * landscape, it goes stale faster than anything else on this site, and the gap
 * register is the only thing in the repository that carries an observer and a
 * date for it (see the header of `data/data-gaps.yaml`). So the counts and the
 * register entries sit in the same row and are never merged: the numbers
 * describe this corpus, the entries are attributed statements about the field,
 * and a domain with no entry has not been surveyed and found complete.
 *
 * Replaces `/gaps` as a section. The register was a nav peer nobody stood in
 * front of at the moment it would have changed what they did; this is that
 * moment.
 */
export async function CatalogCoverage({
  facets,
  gaps,
}: {
  facets: Record<string, FacetBucket[]>;
  gaps: DataGap[] | null;
}) {
  const t = await getTranslations("catalogCoverage");
  const rows = domainCoverage(facets, gaps);
  if (rows.length === 0) return null;
  const orphans = unplacedGaps(rows, gaps);

  return (
    <section aria-labelledby="coverage-heading" className="og-card p-5">
      <h2 id="coverage-heading" className="font-semibold">
        {t("title")}
      </h2>
      <p className="mt-1 text-sm text-[color:var(--muted)]">{t("help")}</p>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <caption className="sr-only">{t("caption")}</caption>
          <thead>
            <tr className="text-left">
              <th scope="col" className="py-1.5 pr-4 font-medium">
                {t("domain")}
              </th>
              {LEVELS.map((level) => (
                <th
                  scope="col"
                  key={level}
                  className="py-1.5 pr-4 text-right font-medium"
                >
                  {t(`level.${level}`)}
                </th>
              ))}
              <th scope="col" className="py-1.5 pr-4 text-right font-medium">
                {t("total")}
              </th>
              <th scope="col" className="py-1.5 font-medium">
                {t("register")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.iri}
                className="border-t"
                style={{ borderColor: "var(--border)" }}
              >
                <th scope="row" className="py-2 pr-4 text-left font-normal">
                  <Link
                    href={{
                      pathname: "/datasets",
                      query: { data_domain: row.iri },
                    }}
                    className="hover:underline"
                  >
                    <span className="font-medium">{row.code}</span>
                    <span className="text-[color:var(--muted)]">
                      {" "}
                      · {row.label}
                    </span>
                  </Link>
                </th>
                {LEVELS.map((level) => {
                  const count = row.counts[level] ?? 0;
                  return (
                    <td
                      key={level}
                      className="py-2 pr-4 text-right tabular-nums"
                    >
                      {count === 0 ? (
                        /* A zero is not a link. There is nothing to show, and a
                           control that lands on an empty result reads as a
                           broken filter rather than as an absence. */
                        <span
                          className="text-[color:var(--muted)]"
                          aria-label={t("none")}
                        >
                          —
                        </span>
                      ) : (
                        <Link
                          href={{
                            pathname: "/datasets",
                            query: {
                              domain_coverage: coverageKey(row.iri, level),
                            },
                          }}
                          className="hover:underline"
                        >
                          {count}
                        </Link>
                      )}
                    </td>
                  );
                })}
                <td className="py-2 pr-4 text-right font-medium tabular-nums">
                  {row.total}
                </td>
                <td className="py-2 text-[color:var(--muted)]">
                  {row.gaps.length === 0 ? (
                    <span className="text-xs">{t("noEntries")}</span>
                  ) : (
                    <GapList
                      gaps={row.gaps}
                      summary={t("entries", { count: row.gaps.length })}
                      observed={t("observed")}
                      stale={t("stale")}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {orphans.length > 0 ? (
        <div
          className="mt-5 border-t pt-4"
          style={{ borderColor: "var(--border)" }}
        >
          <h3 className="og-eyebrow">{t("orphanTitle")}</h3>
          <p className="mt-1 text-sm text-[color:var(--muted)]">
            {t("orphanHelp")}
          </p>
          <div className="mt-2 text-sm text-[color:var(--muted)]">
            <GapList
              gaps={orphans}
              summary={t("entries", { count: orphans.length })}
              observed={t("observed")}
              stale={t("stale")}
              showDomain
            />
          </div>
        </div>
      ) : null}
    </section>
  );
}

/** The register entries for one row, counted first and readable on demand.
 *
 * Collapsed because expanded it takes the table over. One domain carries seven
 * entries, each with its observer and date, and rendered inline that row was
 * eight lines tall while the coverage numbers it exists to qualify sat alone
 * at the top of it — a coverage table that had become a list of gaps with some
 * numbers beside it. Found by looking at it.
 *
 * The count is the at-a-glance signal and it is the useful one: "two records,
 * both discoverable-only, seven known gaps" is the whole finding, and the
 * titles are what you read once that has your attention. `details` rather than
 * a toggle, so it opens with no JavaScript and is in the accessibility tree
 * either way.
 */
function GapList({
  gaps,
  summary,
  observed,
  stale,
  showDomain = false,
}: {
  gaps: DataGap[];
  summary: string;
  observed: string;
  stale: string;
  showDomain?: boolean;
}) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-[color:var(--accent-text)]">
        {summary}
      </summary>
      <ul className="mt-1.5 space-y-1.5">
        {gaps.map((gap) => (
          <li key={gap.id} className="text-xs">
            <span className="font-medium text-[color:var(--foreground)]">
              {showDomain ? `${gap.domain} · ` : ""}
              {gap.title}
            </span>
            {/* Observer and date on every entry, in the open once the list is.
              Never on hover, and never a tooltip: a gap is a much stronger
              claim than a dataset record and a reader who cannot see who made
              it has no way to weigh it. */}
            <span className="block text-[color:var(--muted)]">
              {observed} {gap.observed_by} · {gap.observed}
              {gap.stale ? ` · ${stale}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
