import { getTranslations } from "next-intl/server";
import Link from "next/link";
import type { DatasetDetail } from "@/lib/api";

/**
 * What a dataset was built from, and how deep that goes (#52).
 *
 * `og:provenanceClass` is one hop. It calls NREL ATB *modeled*, and it calls a
 * capacity expansion portfolio built on ATB *modeled*, and it calls a resource
 * adequacy study built on that *modeled* — three layers, one word, read as
 * peers. The framework behind this states the consequence: "each layer looks
 * like data to the layer above it."
 *
 * **Unknown is rendered, not hidden.** A modelled product with no recorded
 * lineage is not one hop from measurement; nobody has said what it came from,
 * and saying so is the honest output. Suppressing the row would let absence
 * read as shallowness, which is the misreading the whole feature exists to
 * prevent.
 */
export async function Lineage({ dataset }: { dataset: DatasetDetail }) {
  const t = await getTranslations("dataset");
  const upstream = dataset.derived_from ?? [];
  const producedBy = dataset.output_of_analysis ?? [];
  const depth = dataset.assumption_depth;
  const modelled = dataset.provenance_class?.includes("modeled");

  // Nothing to say only when there is genuinely nothing: no chain, no
  // direction, and no reason to think a chain is missing.
  if (upstream.length === 0 && producedBy.length === 0 && !(modelled && depth == null)) {
    return null;
  }

  return (
    <section className="og-card max-w-prose space-y-2 p-3 text-sm">
      {producedBy.length > 0 ? (
        <p>
          <span className="font-semibold text-[color:var(--accent-text)]">{t("outputOf")}</span>{" "}
          <span className="text-[color:var(--muted)]">
            {producedBy.map((a) => a.label ?? a.iri.split("/").pop()).join(", ")}
          </span>
        </p>
      ) : null}
      {upstream.length > 0 ? (
        <p>
          <span className="font-semibold text-[color:var(--accent-text)]">{t("lineage")}</span>{" "}
          <span className="text-[color:var(--muted)]">
            {upstream.map((iri, i) => {
              const slug = iri.split("/").pop() ?? iri;
              const internal = iri.includes("/ds/");
              return (
                <span key={iri}>
                  {i > 0 ? ", " : ""}
                  {internal ? (
                    <Link href={`/datasets/${slug}`} className="underline">
                      {slug}
                    </Link>
                  ) : (
                    slug
                  )}
                </span>
              );
            })}
          </span>
        </p>
      ) : null}
      <p className="text-xs text-[color:var(--muted)]">
        {depth == null ? t("lineageUnknown") : t("lineageDepth", { depth })}
      </p>
    </section>
  );
}
