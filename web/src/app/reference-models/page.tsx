import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";
import { NetworkMap } from "@/components/NetworkMap";
import { type DatasetSummary, type QuestionClass, listReferenceModels } from "@/lib/api";
import { perRequest } from "@/lib/rendering";

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

const ROBUSTNESS_ORDER = ["robust", "fragile", "unknown"] as const;

/** Record id -> where this deployment serves that model's bytes from.
 *
 *  A map rather than a set, because the two are not the same string and
 *  assuming they were cost a debugging round: the catalog record is
 *  `cascade-interconnect-reference` (a record *about* a model) and the data
 *  ships under `cascade-interconnect` (the model). Writing the relationship
 *  down makes the next mismatch a compile-time edit rather than a 404 that
 *  renders as "the network could not be loaded".
 *
 *  `document` names the file because the two models do not want the same one.
 *  Cascade has no route geometry, so its whole document is 281 KB and the
 *  viewer reads it directly. The GB model carries a real surveyed corridor per
 *  circuit and is 2.9 MB, which is a download rather than a page asset, so it
 *  ships a second copy at display resolution. Same schema, same reader, one
 *  eighth of the vertices — and the full-resolution file stays the thing a
 *  modeller downloads, because simplifying a corridor for a study is a
 *  different and much worse decision than simplifying it for a screen.
 *
 *  Named rather than inferred from the record's `distribution`: that says a
 *  file exists *somewhere*, and the viewer needs one *here*, same-origin. */
const MAPPED: Record<string, { dir: string; document: string }> = {
  "cascade-interconnect-reference": {
    dir: "cascade-interconnect",
    document: "system.json",
  },
  "gb-osm-reference": {
    dir: "gb-osm",
    document: "system.view.json",
  },
};

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
  const t = await getTranslations("referenceModel");
  const mapped = MAPPED[model.id];
  const classes = model.question_classes ?? [];
  const counts = ROBUSTNESS_ORDER.map((r) => ({
    robustness: r,
    count: classes.filter((c) => c.robustness === r).length,
  })).filter((c) => c.count > 0);

  return (
    <article className="og-card px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <Link
          href={`/datasets/${model.id}`}
          className="text-lg font-semibold hover:text-[color:var(--accent-text)]"
        >
          {model.title}
        </Link>
        <p className="og-eyebrow">
          {model.fidelity_class ? t(`fidelity.${model.fidelity_class}`) : t("fidelity.undeclared")}
          {model.network_element_count
            ? ` · ${t("elements", { count: model.network_element_count })}`
            : ""}
        </p>
      </div>

      {model.summary ? (
        <p className="mt-2 text-sm text-[color:var(--muted)]">{model.summary}</p>
      ) : null}

      {/* Above the map, not below the partition.
          The Data Hub writeup files ODbL share-alike as an open question — "may
          pull [internal utility data] into scope. Needs an answer before Phase 2
          architecture" — and the useful thing a demo can do with an open
          question is put it where somebody meets it before they act, rather
          than record that it was considered. A reader who scrolls to a licence
          line under the fold has already decided to use the model. */}
      {model.share_alike ? (
        <p className="og-share-alike mt-3 text-sm">
          <strong className="font-semibold">
            {t("shareAlike")}
            {model.license_id ? ` · ${model.license_id}` : ""}
          </strong>{" "}
          {t("shareAlikeHelp")}
        </p>
      ) : null}

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

      {counts.length > 0 ? (
        <>
          <p className="og-eyebrow mt-4">{t("partition")}</p>
          <ul className="mt-2 space-y-2">
            {classes.map((qc, i) => (
              <li key={i} className="text-sm">
                <QuestionClassRow qc={qc} />
              </li>
            ))}
          </ul>
        </>
      ) : (
        /* Not "this network is unrated". No partition recorded is a fact about
           the record, and saying otherwise would put a claim about the network
           behind a gap in what has been written down. */
        <p className="mt-4 text-sm text-[color:var(--muted)]">{t("noPartition")}</p>
      )}
    </article>
  );
}

async function QuestionClassRow({ qc }: { qc: QuestionClass }) {
  const t = await getTranslations("referenceModel");
  return (
    <div className="flex flex-wrap items-baseline gap-x-3">
      <span className={`og-tag shrink-0 og-robustness-${qc.robustness}`}>
        {t(`robustness.${qc.robustness}`)}
      </span>
      <span className="min-w-0">{qc.question_class}</span>
      {qc.basis ? (
        <span className="w-full text-xs text-[color:var(--muted)]">{qc.basis}</span>
      ) : (
        /* Only "robust" is required to carry a basis — admitting a limit costs
           a reader nothing, claiming one does — so this line is about an
           unevidenced strength and nothing else. */
        qc.robustness === "robust" && (
          <span className="w-full text-xs text-[color:var(--muted)]">{t("noBasis")}</span>
        )
      )}
    </div>
  );
}
