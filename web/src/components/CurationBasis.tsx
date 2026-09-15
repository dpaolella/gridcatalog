import { getTranslations } from "next-intl/server";
import type { DatasetSummary } from "@/lib/api";
import { formatDate } from "@/lib/format";

const KNOWN = ["staff-assessed", "self-reported", "machine-extracted"];

/**
 * How this record got here (#85).
 *
 * Showing a catalog in which every record is sourced, graded and complete
 * invites one conclusion above all others: that this happens automatically.
 * It does not, and whether assessment is staffed, self-reported or automated
 * is an open question with a budget attached — so a demo that answers it by
 * implication answers it wrongly, and in the direction that costs somebody a
 * headcount.
 *
 * One line per record fixes it, and turns the trap into the argument: a viewer
 * reads the ideal state as an achievable *mix* rather than as magic.
 *
 * The date and rubric are shown when the record carries them and are simply
 * absent otherwise — the catalog's "Not captured" house style. Inventing a
 * rubric version to fill the chip out would be this issue's own over-claim in
 * miniature.
 */
export async function CurationBasis({ record }: { record: DatasetSummary }) {
  const t = await getTranslations("curation");
  const basis = record.curation_basis;
  if (!basis && !record.demonstration) return null;

  const assessed = formatDate(record.assessed_at);

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {basis ? (
        <span className="og-tag" title={t(`help.${label(basis)}` as "help.staffAssessed")}>
          {t(`basis.${label(basis)}` as "basis.staffAssessed")}
          {assessed ? ` · ${assessed}` : ""}
          {record.rubric_version ? ` · ${t("rubric", { version: record.rubric_version })}` : ""}
        </span>
      ) : null}

      {/* Never merged with the provenance-class tags beside it. `Synthetic`
          there means a real, published dataset generated to be representative
          of a system it does not contain — TAMU's networks are exactly that,
          and genuinely published by a real institution. This means nobody
          published it at all. One badge for both would let the second borrow
          the first's credibility. */}
      {record.demonstration ? (
        <span
          className="px-1.5 py-0.5 text-[11px] font-semibold"
          style={{
            borderRadius: "var(--radius)",
            background: "color-mix(in srgb, var(--og-clay, #b4553a) 16%, transparent)",
            color: "var(--accent-text)",
          }}
          title={t("demonstrationHelp")}
        >
          {t("demonstration")}
        </span>
      ) : null}
    </div>
  );
}

/** The message key for a basis, or `unstated` for one this build has no word
 *  for — which is still a fact about the record and must not render blank. */
function label(basis: string): string {
  if (!KNOWN.includes(basis)) return "unstated";
  return basis.replace(/-(\w)/g, (_, c: string) => c.toUpperCase());
}
