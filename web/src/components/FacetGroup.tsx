"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { FacetBucket } from "@/lib/api";
import { iriTail } from "@/lib/format";

/** How many values a facet shows before the rest are behind a disclosure. */
const VISIBLE = 8;

/**
 * One facet: its label, its values, and their counts.
 *
 * Shared by the API-backed filter panel and the static site's, because the two
 * showed the same list rendered twice and had already drifted apart in one
 * detail. A filter panel that disagrees with itself between two builds of the
 * same page is a bug nobody would think to look for.
 *
 * Counts are entitlement-scoped upstream. A count that included records the
 * caller cannot see would leak their existence through arithmetic, which is
 * the leak ADR-0006 is about.
 */
export function FacetGroup({
  field,
  buckets,
  isActive,
  onToggle,
}: {
  field: string;
  buckets: FacetBucket[];
  isActive: (value: string) => boolean;
  onToggle: (value: string) => void;
}) {
  const t = useTranslations("facets");
  const search = useTranslations("search");
  const [expanded, setExpanded] = useState(false);

  // Never hide a value the reader has already selected — a filter that is on
  // and out of sight cannot be turned off, and the result count says something
  // the visible controls cannot explain.
  const shown = expanded
    ? buckets
    : buckets.filter((b, i) => i < VISIBLE || isActive(String(b.value)));
  const hidden = buckets.length - shown.length;

  return (
    /* `min-w-0` on the fieldset, not only on the row. A `fieldset` carries a
       UA-level `min-width: min-content`, so it grows to fit its longest child
       whatever its container says — and then the row inside it has all the
       space it asked for and never truncates. This is the outer half of the
       same bug: without it the long licence identifiers pushed their counts
       past the edge of the filter column and the facet read as having none. */
    <fieldset className="min-w-0">
      <legend className="og-eyebrow mb-1.5 font-semibold">
        {t.has(field) ? t(field) : field}
      </legend>
      <ul className="space-y-1">
        {shown.map((bucket) => (
          <li key={String(bucket.value)}>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={isActive(String(bucket.value))}
                onChange={() => onToggle(String(bucket.value))}
              />
              {/* `min-w-0` is what makes `truncate` work inside a flex row.
                  A flex item defaults to `min-width: auto`, so without it a
                  long value refuses to shrink, overflows the column, and
                  pushes the count out of sight — which read as "this facet has
                  no counts" rather than as a layout bug. */}
              <span
                className="min-w-0 flex-1 truncate"
                title={bucket.label ?? String(bucket.value)}
              >
                {bucket.label ?? iriTail(bucket.value)}
              </span>
              <span className="shrink-0 tabular-nums text-xs text-[color:var(--muted)]">
                {bucket.count}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-1 text-xs font-medium text-[color:var(--accent)] hover:underline"
        >
          {search("showMore", { count: hidden })}
        </button>
      ) : null}
    </fieldset>
  );
}
