"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

/**
 * Sort order, in the URL (PRD §F3: "sorting by relevance or any sortable field").
 *
 * The API has accepted `sort` since M4 and `SORT_FIELDS` lists eight fields;
 * nothing in the UI ever sent one, and the message keys below sat unused in the
 * catalogue, which made the feature look shipped to anyone auditing
 * translations. These four are the ones those keys name.
 *
 * Unlike free-text ranking, an explicit sort is safe to mirror in the static
 * build: ordering by a field is deterministic, so both modes genuinely agree.
 * Relevance is the exception and is expressed as the absence of a `sort`
 * parameter — the server ranks, and the static site leaves the order it was
 * given rather than inventing a score. That asymmetry is the point of the note
 * at the top of `StaticSearch.tsx`.
 */
export const SORT_OPTIONS = [
  { value: "", labelKey: "sortRelevance" },
  { value: "-modified", labelKey: "sortRecent" },
  { value: "title", labelKey: "sortTitle" },
  { value: "-temporal_start", labelKey: "sortCoverage" },
] as const;

export function SortSelect() {
  const t = useTranslations("search");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const current = params.get("sort") ?? "";

  const onChange = useCallback(
    (value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set("sort", value);
      else next.delete("sort");
      // Any change to the ordering resets paging: staying on page 4 of a
      // different order shows a page the reader never asked for.
      next.delete("offset");
      const search = next.toString();
      router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-[color:var(--muted)]">{t("sort")}</span>
      <select
        value={current}
        onChange={(event) => onChange(event.target.value)}
        className="px-2 py-1 text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      >
        {SORT_OPTIONS.map((option) => (
          <option key={option.value || "relevance"} value={option.value}>
            {t(option.labelKey)}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Apply a `sort` value client-side, for the static build.
 *
 *  Mirrors what the API does for the same parameter. Kept next to the control
 *  so the option list and the comparator cannot drift apart. */
/** Only the fields a sort reads — so this stays usable without importing the
 *  whole `DatasetSummary`, and so adding a sortable field is a compile error
 *  here rather than a silent no-op. */
export interface Sortable {
  title?: string | null;
  modified?: string | null;
  completeness_level?: number | null;
  temporal?: { start?: string | null } | null;
}

export function compareBySort<T extends Sortable>(sort: string) {
  const descending = sort.startsWith("-");
  const field = descending ? sort.slice(1) : sort;

  const read = (row: T): string | number | null => {
    if (field === "title") return row.title ?? null;
    if (field === "modified") return row.modified ?? null;
    if (field === "temporal_start") return row.temporal?.start ?? null;
    if (field === "completeness_level") return row.completeness_level ?? null;
    return null;
  };

  return (a: T, b: T): number => {
    const left = read(a);
    const right = read(b);
    // Missing values sort last whichever way the order runs. A record with no
    // coverage window is not "earliest"; it is unknown, and putting it first
    // under "Coverage start" would read as a claim about the data.
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    const order = left < right ? -1 : left > right ? 1 : 0;
    return descending ? -order : order;
  };
}
