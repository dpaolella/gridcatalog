import { useTranslations } from "next-intl";
import type { QualityFacet } from "@/lib/api";

/**
 * Three badges. Never a composite (a hard constraint, not a preference).
 *
 * There is no place in this component where two grades meet. That is structural
 * rather than disciplined: it maps over facets and renders each one, so no
 * variable holds more than one grade and there is nothing to average even by
 * accident.
 *
 * The colour is a single Petrol ramp rather than a red-to-green scale, and the
 * reason is the same constraint: a traffic light across three independent
 * facets invites exactly the averaging the design forbids. The ramp reads as
 * *how much is established*, which is what these facets measure — so a lighter
 * chip is less established, not "bad".
 *
 * An unassessed facet is an outlined chip belonging to no step of the ramp, and
 * never grade D. A record below completeness level 2 has no field metadata to
 * grade; showing D would condemn every harvested record for having been
 * harvested.
 */
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
        const step = grade ? grade.toLowerCase() : null;
        return (
          <li key={name}>
            <span
              className={`og-tag ${size === "lg" ? "px-2 py-1 text-[13px]" : ""}`}
              title={facet?.rationale ?? undefined}
            >
              <span
                aria-hidden
                className="inline-flex h-[18px] w-[18px] items-center justify-center text-[10px] font-semibold"
                style={{
                  borderRadius: "var(--radius)",
                  background: step ? `var(--grade-${step})` : "transparent",
                  color: step ? `var(--grade-${step}-ink)` : "var(--grade-none-ink)",
                  border: step ? "none" : "1px dashed var(--border)",
                }}
              >
                {grade ?? "–"}
              </span>
              <span>{t(name)}</span>
              <span className="font-medium text-[color:var(--foreground)]">
                {facet?.label ?? t("notAssessed")}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
