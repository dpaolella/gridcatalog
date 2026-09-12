import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";

/**
 * The Hub's front door, and the answer to a question the vision left open.
 *
 * That vision asks whether "Data Hub" should be renamed, noting that the
 * catalog "is the smallest part of what is described here" and that the answer
 * depends on whether the registry and the reference datasets are one product
 * or two. A nav settles it before anybody reads a word: four peers establish
 * that the catalog is one of four things, and a catalog with extra tabs
 * establishes the opposite. This page is the four-peer answer, built
 * deliberately rather than arrived at by whichever section got finished first.
 *
 * Retrofitting the other shape is an information-architecture rewrite, which
 * is why it was decided before any of the sections were built.
 *
 * The counts are deliberately absent. A card reading "0 studies" on a
 * demonstration invites the reading that the Hub is empty rather than that the
 * demo has not populated that section yet, and a card reading "12 studies"
 * when the twelve are invented is worse. Each section says what it is for;
 * what is in it is the section's business.
 */

const SECTIONS = [
  {
    href: "/studies",
    key: "studies",
    /* First, because it is the part that is new. A reader who knows the old
       catalog needs to see immediately that this is not that. */
  },
  { href: "/reference-models", key: "referenceModels" },
  { href: "/datasets", key: "datasets" },
  { href: "/gaps", key: "gaps" },
] as const;

export default async function HubPage() {
  const app = await getTranslations("app");
  const t = await getTranslations("hub");

  return (
    <div className="space-y-10">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {app("tagline")}
          </h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">
            {app("description")}
          </p>
        </div>
      </section>

      <nav aria-label={t("sectionsLabel")}>
        <ul className="grid gap-4 sm:grid-cols-2">
          {SECTIONS.map((section) => (
            <li key={section.href}>
              <Link
                href={section.href}
                className="og-card block h-full px-6 py-6 transition-colors hover:border-[color:var(--og-petrol)]"
              >
                <p className="text-lg font-semibold">{t(`${section.key}.title`)}</p>
                <p className="mt-2 text-sm text-[color:var(--muted)]">
                  {t(`${section.key}.blurb`)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {/* The custody rule, in the one place every reader passes through. It is
          the question a publisher asks first and the one a funder asks second,
          and leaving it to the About page means neither finds it. */}
      <section className="og-panel px-6 py-6">
        <p className="og-eyebrow">{t("custody.eyebrow")}</p>
        <p className="mt-3 max-w-prose text-sm text-[color:var(--muted)]">
          {t("custody.body")}
        </p>
      </section>
    </div>
  );
}
