"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FacetBucket } from "@/lib/api";
import { FacetGroup } from "@/components/FacetGroup";

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
    <aside aria-label={search("filters")} className="min-w-0 space-y-6 text-sm">
      <div className="flex items-baseline justify-between">
        <h2 className="font-semibold">{search("filters")}</h2>
        {hasFilters ? (
          <button
            type="button"
            onClick={() => {
              const query = new URLSearchParams();
              const q = params.get("q");
              if (q) query.set("q", q);
              router.replace(`/?${query}`, { scroll: false });
            }}
            className="text-xs font-medium text-[color:var(--accent-text)] hover:underline"
          >
            {search("clearFilters")}
          </button>
        ) : null}
      </div>

      {entries.map(([field, buckets]) => (
        <FacetGroup
          key={field}
          field={field}
          buckets={buckets}
          isActive={(value) => active(field, value)}
          onToggle={(value) => toggle(field, value)}
        />
      ))}
    </aside>
  );
}
