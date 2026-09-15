"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { AssumptionDelta, RunComparison } from "@/lib/api";

/**
 * The verdict a regulator actually asks for, over a threshold they choose (#83).
 *
 * The slider is the point, and it is why this is a client component: watching
 * the verdict flip as the threshold moves is what tells an audience the
 * machinery is real rather than a screenshot. Nothing is re-solved and nothing
 * is fetched — every number here was computed once, on the server, from two
 * schema-validated documents, and the slider only re-partitions them.
 *
 * Which is also the honest limit. "Material" is not a property of the data; it
 * is a line somebody draws. Shipping a fixed threshold would be the catalog
 * making a regulatory judgement it has no standing to make, so the reader
 * draws it and the page says what follows.
 */
export function MaterialityVerdict({
  rows,
  runs,
}: {
  rows: AssumptionDelta[];
  runs?: RunComparison | null;
}) {
  const t = useTranslations("compare");
  const [threshold, setThreshold] = useState(5);

  // Only rows with a relative delta can be judged against a percentage.
  // "Equivalent" rows have a delta of zero by construction, and rows the
  // catalog could not compare have none at all — counting either as immaterial
  // would be a claim rather than an absence.
  const material = rows.filter(
    (row) =>
      row.relation === "different" &&
      row.relative_delta != null &&
      Math.abs(row.relative_delta) * 100 >= threshold,
  );

  const moved = runs?.comparable ? runs.objective_relative : null;
  const resultAbove = moved != null && Math.abs(moved) * 100 >= threshold;

  return (
    <section className="og-card p-5">
      <label className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-semibold">{t("threshold")}</span>
        <input
          type="range"
          min={0}
          max={50}
          step={0.5}
          value={threshold}
          onChange={(event) => setThreshold(Number(event.target.value))}
          className="min-w-0 flex-1"
          aria-describedby="materiality-verdict"
        />
        <span className="font-mono text-sm tabular-nums">{threshold.toFixed(1)}%</span>
      </label>
      <p className="mt-1 text-xs text-[color:var(--muted)]">{t("thresholdHelp")}</p>

      <div id="materiality-verdict" role="status" aria-live="polite" className="mt-4 text-sm">
        <p className="font-semibold">
          {material.length > 0
            ? t("verdictMaterial", { count: material.length })
            : t("verdictImmaterial")}
        </p>
        {material.length > 0 ? (
          <ul className="mt-1 list-disc pl-5 text-[color:var(--muted)]">
            {material.map((row) => (
              <li key={row.path}>
                <span className="font-mono text-xs">{row.parameter}</span>{" "}
                {formatPercent(row.relative_delta)}
              </li>
            ))}
          </ul>
        ) : null}

        {moved != null ? (
          <p className="mt-3">
            {t("verdictResult", {
              percent: formatPercent(moved),
              judgement: resultAbove ? t("above") : t("below"),
            })}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function formatPercent(value?: number | null): string {
  if (value == null) return "—";
  const percent = value * 100;
  // A sign always, because the direction is half the finding and a bare "1.9%"
  // reads as an increase to some readers and a magnitude to others.
  return `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`;
}
