import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";
import { NetworkMap } from "@/components/NetworkMap";
import { type DatasetSummary, listReferenceModels } from "@/lib/api";
import { perRequest } from "@/lib/rendering";
import { ModelFidelity, ModelLicenseNotice, ModelPartition } from "@/components/ReferenceModelSuitability";
import { MAPPED_MODELS } from "@/lib/reference-models";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("referenceModels.title") };
}

/**
 * Networks to start from, each leading with what it is good for.
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
 */

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

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
        <ul className="space-y-4">
          {models.map((model) => (
            <li key={model.id}>
              <ModelCard model={model} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

async function ModelCard({ model }: { model: DatasetSummary }) {
  const mapped = MAPPED_MODELS[model.id];
  return (
    <article id={`model-${model.id}`} className="og-card scroll-mt-5 px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <Link
          href={`/datasets/${model.id}`}
          className="text-lg font-semibold hover:text-[color:var(--accent-text)]"
        >
          {model.title}
        </Link>
        <ModelFidelity model={model} />
      </div>

      {model.summary ? (
        <p className="mt-2 text-sm text-[color:var(--muted)]">{model.summary}</p>
      ) : null}

      <ModelLicenseNotice model={model} />

      {/* The map, where a model ships one. Between the summary and the
          partition on purpose: a reader recognises the place first, then reads
          what it is rated for. The other order asks them to weigh a fitness
          claim about somewhere they have not seen. */}
      {mapped ? (
        <div className="mt-4">
          <NetworkMap
            basemapUrl={`${BASE_PATH}/reference-models/${mapped.dir}/basemap.json`}
            systemUrl={`${BASE_PATH}/reference-models/${mapped.dir}/${mapped.document}`}
            synthetic={model.provenance_class === "synthetic"}
          />
        </div>
      ) : null}

      <ModelPartition model={model} />
    </article>
  );
}

