import Link from "next/link";
import { getTranslations } from "next-intl/server";
import type { FacetBucket } from "@/lib/api";
import { iriTail } from "@/lib/format";

/** How many values a group shows before the tail is folded away.
 *
 * Six rather than the filter panel's eight, because this is a doorway and not
 * a filter: the reader has not chosen to narrow anything yet, and a column of
 * twelve licence identifiers on a front page is a list nobody reads. The tail
 * is never hidden silently — the group says how many it did not show, and the
 * facet is complete one click away in the catalog. */
const VISIBLE = 6;

/** The registry's kinds, in the nav's order, each with the page that holds it.
 *
 * Listed here rather than derived from the `record_type` facet, and the
 * difference is the whole point: a kind with no records has no bucket, so a
 * panel built from the facet alone would show *datasets and reference models*
 * and say nothing about studies at all. The Hub holds four registry types and
 * a first screen that silently drops the empty one misdescribes it — which is
 * the same failure `/studies` exists to refuse, one level up. */
const KINDS = [
  { key: "studies", href: "/studies", recordType: "study" },
  { key: "referenceModels", href: "/reference-models", recordType: "reference_model" },
  { key: "datasets", href: "/datasets", recordType: "dataset" },
] as const;

/**
 * The left column of the front page: what the registry holds, and the doors in.
 *
 * Links, not checkboxes, and that is a decision with two reasons behind it.
 *
 * The first is honesty about where a click lands. A checkbox here would filter
 * a page that is not the catalog, leaving the reader on a front page showing
 * twelve of something with no way to page through the rest. A link says where
 * it goes and lands there filtered, which is the behaviour #100 asks for: the
 * count beside a value is the count of what arrives.
 *
 * The second is that `Facets` — the real filter panel, which this deliberately
 * does not reuse — reads `useSearchParams`, so Next renders its Suspense
 * fallback when it prerenders. On `/datasets` that costs nothing, because the
 * page is about a query the reader has yet to type. On a statically exported
 * front page it means shipping a page with no facets in it at all, which is
 * the identical trap `GeographyPicker` documents. Rendered on the server as
 * anchors, this panel is in the HTML of both builds and works with no
 * JavaScript at all.
 */
export async function BrowsePanel({
  facets,
}: {
  facets: Record<string, FacetBucket[]>;
}) {
  const t = await getTranslations("home");
  const hub = await getTranslations("hub");
  const facetNames = await getTranslations("facets");
  const dataset = await getTranslations("dataset");

  /* Completeness arrives as 1, 2 and 3, and a facet value has no label because
     the level *is* its own identifier. `iriTail` therefore renders it as a bare
     numeral, which is what `/datasets` has always shown under the heading
     "Completeness" — tolerable beside a filter panel a reader has chosen to
     read, and not on a front page, where three unexplained digits are the
     first thing about quality anybody sees. The names are already written for
     the record page; this is the same three. */
  const valueLabel = (field: string, bucket: FacetBucket): string => {
    if (field === "completeness_level" && dataset.has(`levelNames.${bucket.value}`)) {
      return `L${bucket.value} · ${dataset(`levelNames.${bucket.value}`)}`;
    }
    return bucket.label ?? iriTail(bucket.value);
  };

  const counts = new Map(
    (facets.record_type ?? []).map((bucket) => [String(bucket.value), bucket.count]),
  );

  return (
    <aside aria-label={t("browseLabel")} className="min-w-0 space-y-6 text-sm">
      <nav aria-label={hub("sectionsLabel")}>
        <h2 className="og-eyebrow mb-1.5 font-semibold">{t("sections")}</h2>
        <ul className="space-y-1">
          {KINDS.map((kind) => {
            const count = counts.get(kind.recordType) ?? 0;
            return (
              <li key={kind.href}>
                <Link href={kind.href} className="flex items-baseline gap-2 hover:underline">
                  <span className="min-w-0 flex-1">{hub(`${kind.key}.title`)}</span>
                  {/* Zero says "none registered yet" in words rather than as a
                      numeral. A grey 0 beside two real counts reads as a
                      failure to load, and the section behind it explains what
                      it would hold — which a number cannot. */}
                  <span className="shrink-0 text-xs text-[color:var(--muted)]">
                    {count === 0 ? t("noneYet") : count}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {GROUPS.map((field) => {
        const buckets = facets[field] ?? [];
        if (buckets.length === 0) return null;
        const shown = buckets.slice(0, VISIBLE);
        const hidden = buckets.length - shown.length;
        return (
          <section key={field} aria-labelledby={`browse-${field}`}>
            <h2 id={`browse-${field}`} className="og-eyebrow mb-1.5 font-semibold">
              {facetNames.has(field) ? facetNames(field) : field}
            </h2>
            <ul className="space-y-1">
              {shown.map((bucket) => (
                <li key={String(bucket.value)}>
                  <Link
                    href={{ pathname: "/datasets", query: { [field]: String(bucket.value) } }}
                    className="flex items-baseline gap-2 hover:underline"
                  >
                    <span
                      className="min-w-0 flex-1 truncate"
                      title={bucket.label ?? String(bucket.value)}
                    >
                      {valueLabel(field, bucket)}
                    </span>
                    <span className="shrink-0 tabular-nums text-xs text-[color:var(--muted)]">
                      {bucket.count}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {hidden > 0 ? (
              /* Where the rest are, not a disclosure that expands in place. The
                 whole facet is on the catalog page with the filter panel around
                 it, and sending the reader there is both shorter and the thing
                 they were going to need anyway. */
              <Link
                href="/datasets"
                className="mt-1 inline-block text-xs font-medium text-[color:var(--accent-text)] hover:underline"
              >
                {t("moreValues", { count: hidden })}
              </Link>
            ) : null}
          </section>
        );
      })}
    </aside>
  );
}

/** The axes a modeller chooses on, in the order #100 names them.
 *
 * `record_type` is not among them although it is a real facet and a real
 * filter: the Sections block above is that facet, rendered as the three places
 * a reader can go rather than as three values they can filter by. Offering
 * both would put the same crossing on the page twice and make the reader
 * decide which of two identical-looking controls they meant. */
const GROUPS = ["data_domain", "completeness_level", "provenance_class", "license"] as const;
