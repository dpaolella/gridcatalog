"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { SORT_OPTIONS } from "@/lib/catalog-search";

/**
 * Sort order, in the URL (PRD §F3: "sorting by relevance or any sortable field").
 *
 * The API has accepted `sort` since M4 and `SORT_FIELDS` lists eight fields;
 * nothing in the UI ever sent one, and the `search.sort*` message keys sat
 * unused in the catalogue, which made the feature look shipped to anyone
 * auditing translations. `SORT_OPTIONS` names the four those keys cover.
 *
 * The options and the comparator that applies them live together in
 * `@/lib/catalog-search`, one import away, so that an option nobody taught the
 * comparator about is a test failure rather than a control that silently does
 * nothing. They used to sit in this file, next to each other, which stopped
 * being possible once the server had to sort too: a `"use client"` module's
 * exports reach a server component as client references, not as functions, so
 * calling one there fails at render.
 */

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
