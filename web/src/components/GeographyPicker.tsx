"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { selectedPlace, type Place } from "@/lib/places";

/** What the picker needs about a place. Not the models themselves: they are
 * rendered by the server as siblings of this component, for the reason in the
 * doc comment below. */
export type PlaceChip = Pick<Place, "token" | "label"> & { count: number };

/**
 * The geography chips, and the only part of the inventory that is client-side.
 *
 * The models are **not** children of this component, and that separation is
 * load bearing rather than tidy. `useSearchParams` makes a component
 * client-rendered, and in a static export Next prerenders the Suspense
 * fallback in its place — so a first pass that put the whole inventory inside
 * here shipped a published Reference Models page with zero model cards in the
 * HTML. The live build rendered two and the export rendered none, which no
 * type and no build error catches: it renders correctly the moment JavaScript
 * arrives, and is empty to a crawler, a reader with scripting off, and anyone
 * on the wrong side of a slow network.
 *
 * So the server renders every geography's section, always, and this hides the
 * ones the reader did not ask for. Without JavaScript the page is the whole
 * inventory with no picker, which is the honest degradation: showing
 * everything over-answers the question, where showing nothing would answer it
 * wrongly.
 *
 * Toggling `hidden` on siblings is imperative, and it is the price of the
 * above. It is scoped to `[data-place]` nodes, which only the inventory emits.
 */
export function GeographyPicker({ places }: { places: PlaceChip[] }) {
  const t = useTranslations("hub.referenceModels");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const requested = params.get("place");
  const selected = selectedPlace(places, requested);

  useEffect(() => {
    const sections = document.querySelectorAll<HTMLElement>("[data-place]");
    for (const section of sections) {
      section.hidden = selected !== null && section.dataset.place !== selected;
    }
    // Leave the page showing everything if this component ever unmounts, so a
    // stale `hidden` cannot outlive the control that set it.
    return () => {
      for (const section of sections) section.hidden = false;
    };
  }, [selected]);

  function choose(next: string | null) {
    const query = new URLSearchParams(params.toString());
    if (next) query.set("place", next);
    else query.delete("place");
    const search = query.toString();
    router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
  }

  return (
    <div className="og-card p-5">
      <h2 className="og-eyebrow">{t("pickerLabel")}</h2>
      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={t("pickerLabel")}>
        <button
          type="button"
          className={`og-tag px-3 py-1.5 ${selected ? "" : "og-tag-active"}`}
          aria-pressed={!selected}
          onClick={() => choose(null)}
        >
          {t("allPlaces", { count: places.length })}
        </button>
        {places.map((place) => (
          <button
            key={place.token}
            type="button"
            className={`og-tag px-3 py-1.5 ${selected === place.token ? "og-tag-active" : ""}`}
            aria-pressed={selected === place.token}
            onClick={() => choose(place.token)}
          >
            {place.label}
            {place.count > 1 ? ` · ${place.count}` : ""}
          </button>
        ))}
      </div>
      {requested && !selected ? (
        <p className="mt-3 text-sm text-[color:var(--muted)]" role="status">
          {t("unknownPlace", { place: requested })}
        </p>
      ) : null}
      <p className="mt-3 text-sm text-[color:var(--muted)]">{t("notBuiltHere")}</p>
    </div>
  );
}
