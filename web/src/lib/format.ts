/**
 * The locale-aware layer (PRD §F3).
 *
 * Every date, number and byte size in the UI goes through here. Not for
 * tidiness: `toLocaleString()` scattered through components is a second-locale
 * migration that has to visit every file, and the point of shipping English
 * only *with* this layer is that the migration is a message file instead.
 *
 * The other rule these functions carry is PRD principle 2. A missing value
 * renders as an explicit "not captured" and never as an em dash, a zero, or a
 * blank cell — each of which a reader takes as a statement about the dataset.
 */

const LOCALE = "en";
const TZ = "UTC";

export function formatDate(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(LOCALE, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: TZ,
  }).format(date);
}

export function formatYear(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : new Intl.DateTimeFormat(LOCALE, { year: "numeric", timeZone: TZ }).format(date);
}

export function formatNumber(value?: number | null): string | null {
  if (value === null || value === undefined) return null;
  return new Intl.NumberFormat(LOCALE).format(value);
}

/**
 * Bytes, in the units a person reads.
 *
 * Binary prefixes (KiB, MiB) rather than decimal, because that is what object
 * stores report and a catalog that disagreed with the download would look
 * wrong to whoever checked.
 */
export function formatBytes(bytes?: number | null): string | null {
  if (bytes === null || bytes === undefined) return null;
  if (bytes === 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${new Intl.NumberFormat(LOCALE, {
    maximumFractionDigits: value < 10 && exponent > 0 ? 1 : 0,
  }).format(value)} ${units[exponent]}`;
}

/**
 * An ISO 8601 duration as a *message key*, or the raw value.
 *
 * The update cadence is displayed beside the currency grade so a correctly
 * maintained annual dataset does not read as stale next to an hourly one
 * (PRD §F5). "P1Y" beside "Aging" tells a reader nothing; "yearly" does.
 *
 * It returns a key rather than the phrase because the phrases were English
 * written into this file — "yearly", "twice a year", "on demand" — which is the
 * one thing this module exists to prevent. PRD §F3 wants a second locale to be
 * a message file, and a table of English inside the locale-aware layer makes it
 * a code change instead.
 *
 * `{key}` means "render `cadence.<key>`". `{literal}` means the value is not one
 * this build recognises, and is shown verbatim: a cadence of `P17D` is a real
 * statement by a publisher, and dropping it would be worse than showing it raw.
 */
export type Cadence = { key: string } | { literal: string };

export function formatCadence(value?: string | null): Cadence | null {
  if (!value) return null;

  const NAMED: Record<string, string> = {
    irregular: "irregular",
    "on-demand": "onDemand",
    discontinued: "discontinued",
  };
  if (NAMED[value]) return { key: NAMED[value] };

  const match = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(
    value,
  );
  if (!match) return { literal: value };
  const [, years, months, weeks, days, hours, minutes] = match;
  const SIMPLE: Record<string, string> = {
    "1Y": "yearly",
    "6M": "twiceAYear",
    "3M": "quarterly",
    "1M": "monthly",
    "1W": "weekly",
    "1D": "daily",
    "1H": "hourly",
  };
  const token =
    (years && `${years}Y`) ||
    (months && `${months}M`) ||
    (weeks && `${weeks}W`) ||
    (days && `${days}D`) ||
    (hours && `${hours}H`) ||
    (minutes && `${minutes}min`) ||
    "";
  const key = SIMPLE[token];
  return key ? { key } : { literal: value };
}

/**
 * A cadence rendered, given a translator for the `cadence` namespace.
 *
 * Takes `t` rather than importing one: this module has no locale of its own to
 * consult, which is precisely the property that keeps the English in the
 * message file. Both a server component's `getTranslations` and a client
 * component's `useTranslations` satisfy the parameter.
 */
export function cadenceText(
  cadence: Cadence | null,
  t: (key: string) => string,
): string | null {
  if (cadence === null) return null;
  return "key" in cadence ? t(cadence.key) : cadence.literal;
}

/** A dataset's coverage as a phrase: "2015 – 2026", or one open end. */
export function formatSpan(start?: string | null, end?: string | null): string | null {
  const from = formatYear(start);
  const to = formatYear(end);
  if (!from && !to) return null;
  if (from && to) return from === to ? from : `${from} – ${to}`;
  return from ? `${from} –` : `– ${to}`;
}

/** A bounding box as WKT, for a user who wants to paste it into GIS. */
export function bboxToWkt(bbox?: number[] | null): string | null {
  if (!bbox || bbox.length !== 4) return null;
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return `POLYGON((${minLon} ${minLat}, ${maxLon} ${minLat}, ${maxLon} ${maxLat}, ${minLon} ${maxLat}, ${minLon} ${minLat}))`;
}

export function isGlobal(bbox?: number[] | null): boolean {
  if (!bbox || bbox.length !== 4) return false;
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return minLon <= -179 && maxLon >= 179 && minLat <= -60 && maxLat >= 60;
}

/** The last segment of an IRI, for when a label is genuinely absent.
 *
 * Used only as a last resort and never to *invent* a label: a concept IRI with
 * no label in this deployment renders as its IRI tail, which is honest, rather
 * than as a prettified guess at what it means. */
export function iriTail(value: unknown): string {
  // `unknown`, not `string`: facet values are whatever the field holds, and
  // `completeness_level` holds a number while `anonymous_access` holds a
  // boolean. Typing this as `string` did not stop either from arriving — it
  // only moved the failure to runtime, as "a.split is not a function" on the
  // landing page.
  const text = String(value ?? "");
  return text.split(/[/#]/).filter(Boolean).pop() ?? text;
}
