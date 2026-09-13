import Link from "next/link";
import type { DatasetSummary } from "@/lib/api";
import { NetworkMap } from "@/components/NetworkMap";
import {
  ModelFidelity,
  ModelLicenseNotice,
  ModelPartition,
} from "@/components/ReferenceModelSuitability";
import { MAPPED_MODELS } from "@/lib/reference-models";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** One model, as the inventory shows it.

 * A server component on purpose. It renders client components (the map, the
 * suitability panels) and is not one itself, so its content is in the HTML of
 * both builds — see `GeographyPicker` for what went wrong when it was not. */
export function ReferenceModelCard({ model }: { model: DatasetSummary }) {
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
