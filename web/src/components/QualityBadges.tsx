import { useTranslations } from "next-intl";
import type { QualityFacet } from "@/lib/api";

/**
 * Three badges. Never a composite (ADR-0007, PRD §F5: "a hard constraint, not
 * a preference").
 *
 * There is no place in this component where two grades meet. That is
 * structural rather than disciplined: the component maps over facets and
 * renders each one, so there is no variable holding more than one grade and
 * nothing to average even by accident.
 *
 * An unassessed facet renders as "not yet assessed" and never as D. A record
 * below completeness level 2 has no field metadata to grade, and showing D
 * would condemn every harvested record for having been harvested.
 */

const GRADE_COLOR: Record<string, string> = {
  A: "var(--grade-a)",
  B: "var(--grade-b)",
  C: "var(--grade-c)",
  D: "var(--grade-d)",
};

export function QualityBadges({
  facets,
  size = "sm",
}: {
  facets: QualityFacet[];
  size?: "sm" | "lg";
}) {
  const t = useTranslations("quality");
  const order: QualityFacet["facet"][] = ["provenance", "documentation", "currency"];
  const byName = new Map(facets.map((f) => [f.facet, f]));

  return (
    <ul className={`flex flex-wrap ${size === "lg" ? "gap-3" : "gap-2"}`}>
      {order.map((name) => {
        const facet = byName.get(name);
        const grade = facet?.grade ?? null;
        return (
          <li key={name}>
            <span
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 ${
                size === "lg" ? "text-sm" : "text-xs"
              }`}
              style={{ borderColor: "var(--border)" }}
              title={facet?.rationale ?? undefined}
            >
              <span
                aria-hidden
                className="inline-flex h-4 w-4 items-center justify-center rounded-sm text-[10px] font-semibold text-white"
                style={{ background: grade ? GRADE_COLOR[grade] : "var(--grade-none)" }}
              >
                {grade ?? "–"}
              </span>
              <span className="text-[color:var(--muted)]">{t(name)}</span>
              <span className="font-medium">{facet?.label ?? t("notAssessed")}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
