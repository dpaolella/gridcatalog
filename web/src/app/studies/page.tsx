import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("studies.title") };
}

/**
 * Studies and the assumption sets they stand on.
 *
 * Empty, and saying so plainly, because the four registry types exist in the
 * schema and nothing has been projected into the search index under them yet.
 * The honest empty state is the point: a section that quietly rendered the
 * dataset catalog under a studies heading would be a lie told by an
 * information architecture, and it is exactly the failure this nav shape was
 * chosen to avoid.
 */
export default async function StudiesPage() {
  const t = await getTranslations("hub");
  const empty = await getTranslations("empty");

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight">{t("studies.title")}</h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">{t("studies.blurb")}</p>
        </div>
      </section>

      <EmptyState title={empty("noStudies")}>
        <p>{empty("noStudiesHelp")}</p>
      </EmptyState>
    </div>
  );
}
