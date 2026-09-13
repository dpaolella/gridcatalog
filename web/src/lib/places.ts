import type { DatasetSummary } from "./api";

/** The last segment of a place concept IRI — `greatBritain` out of
 * `https://schema.opengrid.org/concept/place/greatBritain`.
 *
 * The token rather than the IRI goes in the URL. It is what a person reads in
 * a shared link and what they can guess, and it round-trips exactly because
 * the scheme mints one concept per segment (`docs/conventions.md`). */
export function placeToken(iri: string): string {
  return iri.slice(iri.lastIndexOf("/") + 1);
}

export interface Place {
  token: string;
  label: string;
  models: DatasetSummary[];
}

/**
 * Group models by the place each is about.
 *
 * Keyed on `place_iris`, not on `place_labels`. "Great Britain", "GB" and
 * "Britain" are three strings and one place, and a picker built on labels
 * offers a reader that choice as though it meant something.
 *
 * A model with no place recorded is not filed under a guess. It comes back in
 * `unplaced` and is still shown, because "the catalog does not know where this
 * is" and "this network is nowhere" are different statements and only the
 * first is true.
 */
export function groupByPlace(models: DatasetSummary[]): {
  places: Place[];
  unplaced: DatasetSummary[];
} {
  const places = new Map<string, Place>();
  const unplaced: DatasetSummary[] = [];

  for (const model of models) {
    const iri = model.spatial?.place_iris?.[0];
    if (!iri) {
      unplaced.push(model);
      continue;
    }
    const token = placeToken(iri);
    const existing = places.get(token);
    if (existing) existing.models.push(model);
    else
      places.set(token, {
        token,
        label: model.spatial?.place_labels?.[0] ?? token,
        models: [model],
      });
  }

  return {
    places: [...places.values()].sort((a, b) => a.label.localeCompare(b.label)),
    unplaced,
  };
}

/** The place a `?place=` token selects, or null for "show everything".
 *
 * An unrecognised token resolves to null rather than to an empty result. A
 * stale or mistyped link is a reason to widen the view and say so; rendering
 * nothing would tell the reader there is no network for that place, which is a
 * claim this page is not entitled to make and would be making by accident.
 */
export function selectedPlace(
  places: readonly { token: string }[],
  requested: string | null,
): string | null {
  return places.some((place) => place.token === requested) ? requested : null;
}
