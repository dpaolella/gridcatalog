"use client";

import { useTranslations } from "next-intl";
import type { StudyDetail } from "@/lib/api";

/**
 * The other half of the pair (#83).
 *
 * The issue's rule is that a stored result must never sit next to a bare
 * "Run". Solving is out of scope, two of the vision's open questions ask
 * whether the Hub ever hosts compute, and a green Run answers both in the
 * affirmative on the product's behalf. What the registry *can* honestly offer
 * is the case itself, in a form somebody else can take away and run.
 *
 * So this exports what the Hub actually holds: every assumption value with its
 * path, unit, basis and source; the network the study bound, by IRI; and the
 * schema revision the set validated against. That is a case, and it is not a
 * solver input deck — the Hub does not know what tool the reader uses, and
 * generating a PyPSA file it had not tested would be the same over-claim one
 * layer down. The document says so in a `note` field rather than leaving the
 * reader to discover it.
 *
 * Built in the browser from data already on the page, so it works identically
 * on the published static copy, where there is no API to ask.
 */
export function ExportCase({ study }: { study: StudyDetail }) {
  const t = useTranslations("compare");

  const download = () => {
    const document_ = {
      note:
        "The case as the OpenGrid Hub holds it: the assumption values, the network " +
        "they were laid over, and the schema revision they validated against. This " +
        "is not a solver input deck — the Hub registers runs and does not execute " +
        "them, so translating this into your tool's format is yours to do.",
      study: { id: study.id, iri: study.iri, title: study.title, frozen_at: study.frozen_at },
      bound_reference_models: study.bound_reference_models,
      assumption_sets: study.assumption_sets.map((set) => ({
        id: set.id,
        iri: set.iri,
        schema_pin: set.schema_pin,
        assumptions: set.assumptions.map((row) => ({
          path: row.path,
          value: row.value,
          unit: row.unit,
          value_basis: row.value_basis,
          field_sources: row.field_sources,
        })),
      })),
      exported_at: new Date().toISOString(),
    };

    const blob = new Blob([JSON.stringify(document_, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = `${study.id}-case.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <button type="button" onClick={download} className="og-cta">
      {t("exportCase")}
    </button>
  );
}
