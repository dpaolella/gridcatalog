"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FacetBucket } from "@/lib/api";
import { iriTail } from "@/lib/format";

/**
 * Filters, over whatever facets the API returned.
 *
 * Driven by the response rather than by a hardcoded list, so a facet added
 * server-side appears here without a UI change — and, more usefully, a facet
 * that stops being computed disappears instead of rendering as an empty box
 * that looks broken.
 *
 * Counts are entitlement-scoped upstream. A facet count that included records
 * the caller cannot see would leak their existence through arithmetic, which
 * is the leak ADR-0006 is about.
 */
export function Facets({ facets }: { facets: Record<string, FacetBucket[]> }) {
  const t = useTranslations("facets");
  const search = useTranslations("search");
  const router = useRouter();
  const params = useSearchParams();

  const active = (field: string, value: string) => params.getAll(field).includes(value);

  function toggle(field: string, value: string) {
    const query = new URLSearchParams(params.toString());
    const current = query.getAll(field);
    query.delete(field);
    for (const item of current) if (item !== value) query.append(field, item);
    if (!current.includes(value)) query.append(field, value);
    query.delete("offset");
    router.replace(`/?${query}`, { scroll: false });
  }

  const entries = Object.entries(facets).filter(([, buckets]) => buckets.length > 0);
  if (!entries.length) return null;

  const hasFilters = [...params.keys()].some((key) => key !== "q" && key !== "offset");

  return (
    <aside aria-label={search("filters")} className="space-y-6 text-sm">
      <div className="flex items-baseline justify-between">
        <h2 className="font-medium">{search("filters")}</h2>
        {hasFilters ? (
          <button
            type="button"
            onClick={() => {
              const query = new URLSearchParams();
              const q = params.get("q");
              if (q) query.set("q", q);
              router.replace(`/?${query}`, { scroll: false });
            }}
            className="text-xs text-[color:var(--accent)] hover:underline"
          >
            {search("clearFilters")}
          </button>
        ) : null}
      </div>

      {entries.map(([field, buckets]) => (
        <fieldset key={field}>
          <legend className="mb-1.5 font-medium text-[color:var(--muted)]">
            {t.has(field) ? t(field) : field}
          </legend>
          <ul className="space-y-1">
            {buckets.slice(0, 8).map((bucket) => (
              <li key={String(bucket.value)}>
                <label className="flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    checked={active(field, String(bucket.value))}
                    onChange={() => toggle(field, String(bucket.value))}
                  />
                  <span className="flex-1 truncate" title={bucket.label ?? String(bucket.value)}>
                    {bucket.label ?? iriTail(bucket.value)}
                  </span>
                  <span className="tabular-nums text-xs text-[color:var(--muted)]">
                    {bucket.count}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      ))}
    </aside>
  );
}
