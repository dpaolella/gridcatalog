import Link from "next/link";
import { Suspense } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { ModelComponents } from "@/components/ModelComponents";
import { NotFoundError, getDataset, getSchema } from "@/lib/api";
import { MAPPED_MODELS } from "@/lib/reference-models";
import { perRequest } from "@/lib/rendering";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/**
 * The model document, rendered (#98).
 *
 * The bytes behind a reference model were reachable only as a file on the
 * Downloads tab. A reader deciding whether to build on the GB model had to
 * fetch 2.9 MB and open it in something else to see what any of it said — so
 * the per-field `og:valueBasis` work, which is the most useful thing the
 * catalog knows about this model, was invisible at the moment it would have
 * changed a decision.
 *
 * ## Why this route and not `/datasets/[id]/model`
 *
 * The page is about the *model*, not about the catalog record of it. Reference
 * Models is the section that holds models, and
 * `/reference-models/gb-osm-reference` says what the page is without a reader
 * having to know that a model is also a dataset. The record stays at
 * `/datasets/[id]` and links here; so does the map.
 *
 * The id is the record id, deliberately, and not the asset directory name. A
 * reader who has a record id — from the catalog, from the API, from a citation
 * — can construct this URL, and the same id is what every other page keys on.
 *
 * ## What is rendered where
 *
 * The shell is a server component and comes from the catalog: the title, the
 * field declarations, the links out. Everything read out of the model file is
 * client-side, fetched from the same origin the map fetches from — which keeps
 * this page's own weight a constant rather than a function of how many
 * components the model has, and is why `ops/check-page-weight.sh` has nothing
 * to catch here.
 *
 * The split is also the honest one about authority: the record says whether a
 * value is measured, estimated or modelled, and the model file says what the
 * value is. A model that declared its own trustworthiness would be marking its
 * own homework.
 */

/** No model has a page here unless its bytes are staged beside it. `false`
 *  makes that true of the live build as well as the export: a record with no
 *  document 404s rather than rendering a shell around a fetch that cannot
 *  succeed. */
export const dynamicParams = false;

export function generateStaticParams() {
  return Object.keys(MAPPED_MODELS).map((id) => ({ id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!MAPPED_MODELS[id]) return {};
  try {
    const dataset = await getDataset(id);
    return { title: dataset.title };
  } catch {
    return {};
  }
}

export default async function ModelPage({ params }: { params: Promise<{ id: string }> }) {
  await perRequest();
  const { id } = await params;
  const asset = MAPPED_MODELS[id];
  if (!asset) notFound();

  const t = await getTranslations("modelPage");
  const nav = await getTranslations("nav");

  let dataset;
  let schema;
  try {
    [dataset, schema] = await Promise.all([getDataset(id), getSchema(id)]);
  } catch (error) {
    if (error instanceof NotFoundError) notFound();
    throw error;
  }

  const base = `${BASE_PATH}/reference-models/${asset.dir}`;

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-7 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-3xl">
          <p className="og-eyebrow">{nav("referenceModels")}</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            {t("title", { model: dataset.title })}
          </h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">{t("blurb")}</p>
          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <Link
              href={`/datasets/${id}`}
              className="font-medium text-[color:var(--accent-text)] hover:underline"
            >
              {t("toRecord")}
            </Link>
            <Link
              href={`/reference-models#model-${id}`}
              className="font-medium text-[color:var(--accent-text)] hover:underline"
            >
              {t("toMap")}
            </Link>
            {/* The document itself, still. This page is a reading of it, not a
                replacement for it: a modeller who wants to run the network
                wants the file, and hiding it behind a rendering would be the
                mirror of the problem the page exists to fix. */}
            <a
              href={`${base}/${asset.document}`}
              className="font-medium text-[color:var(--accent-text)] hover:underline"
              download
            >
              {t("toDocument")}
            </a>
          </div>
        </div>
      </section>

      {/* `ModelComponents` reads `?component=` so a link from the map lands on
          the component it named. That makes it a `useSearchParams` consumer and
          so unprerenderable — which costs nothing here, because it renders
          nothing on the server anyway: every value on it comes from a fetch the
          browser makes. The shell above, which is the part a crawler and a
          reader with no JavaScript get, is real HTML in both builds. */}
      <Suspense fallback={<p className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">{t("loading")}</p>}>
        <ModelComponents
          systemUrl={`${base}/${asset.document}`}
          parametersUrl={`${base}/parameters.json`}
          fields={schema?.fields ?? []}
        />
      </Suspense>
    </div>
  );
}
