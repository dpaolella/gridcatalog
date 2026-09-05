"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

/**
 * Paging through the URL, like every other piece of search state.
 *
 * The counts here are entitlement-scoped upstream: a record the caller may not
 * see contributes to no total and shifts no page boundary. That is what stops
 * existence leaking through pagination arithmetic (ADR-0006), and it is why
 * this component can be as naive as it looks.
 */
export function Pagination({
  total,
  offset,
  limit,
}: {
  total: number;
  offset: number;
  limit: number;
}) {
  const t = useTranslations("search");
  const router = useRouter();
  const params = useSearchParams();

  if (total <= limit) return null;

  function go(next: number) {
    const query = new URLSearchParams(params.toString());
    if (next > 0) query.set("offset", String(next));
    else query.delete("offset");
    router.replace(`/?${query}`);
  }

  return (
    <nav className="flex items-center gap-3 pt-2 text-sm" aria-label="Pagination">
      <button
        type="button"
        onClick={() => go(Math.max(offset - limit, 0))}
        disabled={offset === 0}
        className="og-tag px-3 py-1.5 text-sm hover:text-[color:var(--foreground)] disabled:opacity-40"
      >
        {t("previous")}
      </button>
      <button
        type="button"
        onClick={() => go(offset + limit)}
        disabled={offset + limit >= total}
        className="og-tag px-3 py-1.5 text-sm hover:text-[color:var(--foreground)] disabled:opacity-40"
      >
        {t("next")}
      </button>
      <span className="text-[color:var(--muted)]">
        {Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}
      </span>
    </nav>
  );
}
