import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("referenceModels.title") };
}

/**
 * Networks to start from, each declaring what it is good for.
 *
 * Empty for the same reason as `/studies`, and see that file for why the empty
 * state is written rather than filled with the nearest available thing.
 */
export default async function ReferenceModelsPage() {
  const t = await getTranslations("hub");
  const empty = await getTranslations("empty");

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight">
            {t("referenceModels.title")}
          </h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">
            {t("referenceModels.blurb")}
          </p>
        </div>
      </section>

      <EmptyState title={empty("noReferenceModels")}>
        <p>{empty("noReferenceModelsHelp")}</p>
      </EmptyState>
    </div>
  );
}
