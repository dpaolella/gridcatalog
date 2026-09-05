"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, useTransition } from "react";

/**
 * Search-while-typing, no submit step (PRD §F3).
 *
 * Debounced at 250 ms and pushed into the URL rather than held in component
 * state. The URL is the state: a search a user can send to a colleague is
 * worth more than one that is fractionally faster, and it also means the back
 * button does what a back button should.
 *
 * `replace` rather than `push`, so typing eight characters leaves one history
 * entry rather than eight — otherwise the back button walks the user backwards
 * through their own keystrokes.
 */
const DEBOUNCE_MS = 250;

export function SearchBar() {
  const t = useTranslations("search");
  const router = useRouter();
  const params = useSearchParams();
  const [value, setValue] = useState(params.get("q") ?? "");
  const [isPending, startTransition] = useTransition();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  function onChange(next: string) {
    setValue(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const query = new URLSearchParams(params.toString());
      if (next) query.set("q", next);
      else query.delete("q");
      // Any change to the query resets paging: staying on page 4 of a
      // different search shows an empty page and looks like no results.
      query.delete("offset");
      startTransition(() => router.replace(`/?${query}`, { scroll: false }));
    }, DEBOUNCE_MS);
  }

  return (
    <div className="relative">
      <label htmlFor="q" className="sr-only">
        {t("label")}
      </label>
      <input
        id="q"
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={t("placeholder")}
        autoComplete="off"
        className="w-full rounded-lg border px-4 py-3 text-base"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      />
      <p className="mt-1.5 text-xs text-[color:var(--muted)]" aria-live="polite">
        {isPending ? "…" : t("typing")}
      </p>
    </div>
  );
}
