/**
 * next-intl configuration.
 *
 * PRD §F3: *all user-facing strings externalized, all dates, numbers and units
 * through a locale-aware layer. Ship English only.* Architectural readiness,
 * not a second-locale deliverable — and much cheaper now than retrofitted,
 * because retrofitting means finding every string in every component.
 *
 * One locale is loaded. Adding a second is a file and a line here.
 */
import { getRequestConfig } from "next-intl/server";

export const locales = ["en"] as const;
export const defaultLocale = "en";

export type Locale = (typeof locales)[number];

export default getRequestConfig(async () => ({
  locale: defaultLocale,
  messages: (await import(`../messages/${defaultLocale}.json`)).default,
  // Fixed rather than the server's, so a date renders the same for every
  // reader. A coverage window that shifted by a day depending on where the
  // renderer ran would be a genuinely confusing bug to chase.
  timeZone: "UTC",
}));
