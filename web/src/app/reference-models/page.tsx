import { Suspense } from "react";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";
import { GeographyPicker } from "@/components/GeographyPicker";
import { ReferenceModelCard } from "@/components/ReferenceModelCard";
import { type DatasetSummary, listReferenceModels } from "@/lib/api";
import { groupByPlace } from "@/lib/places";
import { perRequest } from "@/lib/rendering";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("referenceModels.title") };
}

/**
 * Networks to start from, entered by geography (#97).
 *
 * A card leads with the declared fidelity class and the robust / fragile /
 * unknown counts, not with a description. That ordering is the argument: the
 * vision is explicit that this has to be a *partition* rather than a single
 * fidelity number, because framing it as accuracy "invites a result that says
 * the synthetic network is bad, which is both true and beside the point". A
 * card that led with prose would leave the reader to infer fitness from tone.
 *
 * These stay in `/datasets` too. A reference model is a dataset — licence,
 * access path, publisher — and a modeller searching for a network should find
 * one. This section is a view over the catalog, not a second corpus.
 *
 * Every geography's section is rendered here, by the server, in both builds.
 * The picker is a small client component beside them that hides the ones the
 * reader did not ask for — not a wrapper around them, because a wrapper that
 * reads the URL is client-only in a static export and publishes a page with no
 * models in it. `GeographyPicker` carries that story and the rest of what the
 * picker may and may not claim.
 */

export default async function ReferenceModelsPage() {
  // This is a view over the live index — records land in it as they are
  // loaded — so it is rendered per request wherever there is a request, the
  // same as every other page that reads the catalog. In the static export
  // there is no request and this is a no-op, which is the whole point of
  // `perRequest` over a `force-dynamic` literal.
  await perRequest();

  const t = await getTranslations("hub");
  const empty = await getTranslations("empty");
  const models = await listReferenceModels();

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

      {models === null ? (
        /* Not an empty shelf. "No reference models published" is a claim about
           what this build contains; a catalog that would not answer is no
           evidence for it, and rendering the two the same way turns an outage
           into a false statement about the corpus. */
        <EmptyState title={empty("referenceModelsUnavailable")}>
          <p>{empty("referenceModelsUnavailableHelp")}</p>
        </EmptyState>
      ) : models.length === 0 ? (
        <EmptyState title={empty("noReferenceModels")}>
          <p>{empty("noReferenceModelsHelp")}</p>
        </EmptyState>
      ) : (
        <Inventory models={models} />
      )}
    </div>
  );
}

function Inventory({ models }: { models: DatasetSummary[] }) {
  const { places, unplaced } = groupByPlace(models);

  return (
    <div className="space-y-6">
      <Suspense fallback={null}>
        <GeographyPicker
          places={places.map(({ token, label, models: held }) => ({
            token,
            label,
            count: held.length,
          }))}
        />
      </Suspense>

      {places.map((place) => (
        <section
          key={place.token}
          data-place={place.token}
          aria-label={place.label}
          className="space-y-4"
        >
          {/* Named even when one place is selected: a shared link should say
              where it landed, and a reader arriving at a filtered view should
              not have to infer it from the model's title. */}
          <h2 className="og-eyebrow">{place.label}</h2>
          {place.models.map((model) => (
            <ReferenceModelCard key={model.id} model={model} />
          ))}
        </section>
      ))}

      {unplaced.length > 0 ? (
        <UnplacedSection models={unplaced} />
      ) : null}
    </div>
  );
}

async function UnplacedSection({ models }: { models: DatasetSummary[] }) {
  const t = await getTranslations("hub.referenceModels");
  return (
    /* No `data-place`, so the picker never hides it: a model the catalog
       cannot place is not evidence about any geography, and filtering it away
       under one would be filing it under a guess. */
    <section aria-label={t("unplaced")} className="space-y-4">
      <h2 className="og-eyebrow">{t("unplaced")}</h2>
      <p className="text-sm text-[color:var(--muted)]">{t("unplacedHelp")}</p>
      {models.map((model) => (
        <ReferenceModelCard key={model.id} model={model} />
      ))}
    </section>
  );
}
