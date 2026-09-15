import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { ExportCase } from "@/components/ExportCase";
import { MaterialityVerdict } from "@/components/MaterialityVerdict";
import {
  IS_SNAPSHOT,
  NotFoundError,
  type AssumptionDelta,
  type StudyComparison,
  compareStudies,
  getStudy,
  snapshotStudyIds,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { perRequest } from "@/lib/rendering";
import { isKnownBasis } from "@/lib/studies";

/**
 * Two studies, diffed, with the verdict a regulator would actually ask for (#83).
 *
 * The screen the whole registry argues towards. A filing and the intervention
 * against it are two documents of a few hundred parameters each, and the only
 * question anybody has is *which number changed, and did it matter*. Answering
 * it by hand means diffing them by eye.
 *
 * ## What is computed and what is stored
 *
 * **Computed, on every load:** the structural match between the two sets, the
 * unit normalisation, the per-row relation, and the materiality partition. A
 * reader can fetch both assumption sets and redo all of it, which is what
 * makes it checkable rather than authoritative.
 *
 * **Stored, and labelled:** every simulation output. The objectives come from
 * run records their authors registered, each carrying its tool, version,
 * solver and case hash. Nothing on this page was solved to answer the
 * reader's question.
 *
 * ## The button that is not here
 *
 * There is no Run. Whether the Hub ever hosts compute is an open question in
 * the vision, and a green Run button answers it in the affirmative and commits
 * the product to the most expensive possible scope. What the page offers
 * instead is the pair the registry can honestly support: the stored result,
 * and the case exported in a form somebody else can run.
 */

type Params = Promise<{ id: string; other: string }>;

/** Every ordered pair of studies. Enumerable because the comparison is
 *  between two records the catalog holds, so the static build can write a page
 *  per pair — the same set the live build renders on demand. */
export const generateStaticParams = IS_SNAPSHOT
  ? async () => {
      const ids = await snapshotStudyIds();
      return ids.flatMap((id) =>
        ids.filter((other) => other !== id).map((other) => ({ id, other })),
      );
    }
  : undefined;

export async function generateMetadata({ params }: { params: Params }) {
  const { id, other } = await params;
  await perRequest();
  const t = await getTranslations("compare");
  try {
    const comparison = await compareStudies(id, other);
    return {
      title: t("title", { left: comparison.left_title, right: comparison.right_title }),
    };
  } catch {
    return { title: "Not found" };
  }
}

export default async function ComparePage({ params }: { params: Params }) {
  const { id, other } = await params;
  await perRequest();
  const t = await getTranslations("compare");

  let comparison: StudyComparison;
  try {
    comparison = await compareStudies(id, other);
  } catch (error) {
    if (error instanceof NotFoundError) notFound();
    throw error;
  }

  const runs = comparison.runs;
  // The side whose case is offered for export: the study being compared *to*,
  // which is the one a reader arriving from an intervention wants to run.
  // Allowed to fail — a missing export button is better than a page that
  // cannot render because a secondary fetch did not land.
  const right = await getStudy(comparison.right_id).catch(() => null);

  return (
    <article className="space-y-8">
      <nav className="text-sm text-[color:var(--muted)]">
        <Link href={`/studies/${comparison.left_id}`} className="hover:underline">
          ← {t("back", { title: comparison.left_title })}
        </Link>
      </nav>

      <header className="relative -mx-5 overflow-hidden px-5 pb-6">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("title", { left: comparison.left_title, right: comparison.right_title })}
          </h1>
          <Rule />
          <p className="mt-5 text-sm text-[color:var(--muted)]">{t("blurb")}</p>
          {comparison.comparable && comparison.left_schema_pin ? (
            <p className="mt-3 text-xs text-[color:var(--muted)]">
              {t("schemaPins", { pin: comparison.left_schema_pin.slice(0, 8) })}
            </p>
          ) : null}
        </div>
      </header>

      {/* The box the value proposition implies, and it is not a footnote: a
          result on a reference network establishes that a question is worth a
          commission's time. It does not establish what the real system does,
          and a reader who takes it for the second has been misled by the
          page's own polish. */}
      <section
        className="og-card max-w-prose p-4 text-sm"
        style={{ background: "color-mix(in srgb, var(--accent) 8%, transparent)" }}
      >
        <p className="font-semibold text-[color:var(--accent-text)]">{t("aboutTheNetwork")}</p>
        <p className="mt-1 text-[color:var(--muted)]">{t("aboutTheNetworkHelp")}</p>
      </section>

      {!comparison.comparable ? (
        <section className="og-card p-5">
          <p className="font-semibold">{t("notComparable")}</p>
          <p className="mt-1 max-w-prose text-sm text-[color:var(--muted)]">{comparison.reason}</p>
        </section>
      ) : (
        <>
          <section className="space-y-3">
            <p className="text-sm font-semibold">
              {t("summary", { changed: comparison.changed, total: comparison.rows.length })}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-sm">
                <thead>
                  <tr className="text-left text-xs text-[color:var(--muted)]">
                    <th className="py-2 pr-4 font-medium">{t("colParameter")}</th>
                    <th className="py-2 pr-4 font-medium">{comparison.left_title}</th>
                    <th className="py-2 pr-4 font-medium">{comparison.right_title}</th>
                    <th className="py-2 font-medium">{t("colChange")}</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.rows.map((row) => (
                    <Row key={row.path} row={row} comparison={comparison} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <MaterialityVerdict rows={comparison.rows} runs={runs} />
        </>
      )}

      <section className="space-y-3">
        <div className="max-w-prose">
          <h2 className="text-xl font-semibold tracking-tight">{t("results")}</h2>
          <p className="mt-1 text-sm text-[color:var(--muted)]">{t("resultsBlurb")}</p>
        </div>

        {runs?.left || runs?.right ? (
          <ul className="grid gap-3 md:grid-cols-2">
            {[runs.left, runs.right].map((run, index) =>
              run ? (
                <li key={run.iri} className="og-card p-4 text-sm">
                  <p className="og-eyebrow">
                    {index === 0 ? comparison.left_title : comparison.right_title}
                  </p>
                  <p className="mt-2 text-lg font-semibold">
                    {formatNumber(run.objective_value)}{" "}
                    <span className="text-sm font-normal text-[color:var(--muted)]">
                      {run.objective_unit_label ?? ""}
                    </span>
                  </p>
                  {/* The chip the issue asks for: what produced this number,
                      and which document it actually solved. */}
                  <p className="mt-2 break-all font-mono text-[11px] text-[color:var(--muted)]">
                    {t("runChip", {
                      tool: run.tool ?? "—",
                      version: run.tool_version ?? "",
                      solver: run.solver ?? "—",
                      hash: (run.case_hash ?? "").slice(0, 8),
                    })}
                  </p>
                  <p className="mt-1 text-xs text-[color:var(--muted)]">{run.executed_by}</p>
                </li>
              ) : (
                <li key={`absent-${index}`} className="og-card p-4 text-sm">
                  <p className="font-semibold">{t("noResult")}</p>
                  <p className="mt-1 text-[color:var(--muted)]">{t("notPrecomputed")}</p>
                </li>
              ),
            )}
          </ul>
        ) : null}

        {runs && !runs.comparable && runs.reason ? (
          <div className="og-card max-w-prose p-4 text-sm">
            <p className="font-semibold">{t("uncontrolled")}</p>
            <p className="mt-1 text-[color:var(--muted)]">{runs.reason}</p>
          </div>
        ) : runs?.comparable && runs.objective_delta != null ? (
          <p className="text-sm">
            {t("objectiveDelta", {
              delta: `${formatNumber(runs.objective_delta)} ${runs.objective_unit_label ?? ""}`,
              percent: `${(runs.objective_relative ?? 0) * 100 > 0 ? "+" : ""}${(
                (runs.objective_relative ?? 0) * 100
              ).toFixed(2)}%`,
            })}
          </p>
        ) : null}

        {/* Paired with the stored result, and never a bare Run: solving is out
            of scope, and a green button would answer two of the vision's open
            questions in the affirmative on the product's behalf. */}
        <p className="max-w-prose text-sm">
          {right ? <ExportCase study={right} /> : null}
          <span className="ml-3 text-xs text-[color:var(--muted)]">{t("exportCaseHelp")}</span>
        </p>
      </section>
    </article>
  );
}

function Row({ row, comparison }: { row: AssumptionDelta; comparison: StudyComparison }) {
  return (
    <tr className="border-t align-top" style={{ borderColor: "var(--border)" }}>
      <td className="py-2 pr-4">
        <span className="font-mono text-xs">{row.parameter}</span>
        <span className="block text-[11px] text-[color:var(--muted)]">{row.component}</span>
      </td>
      <td className="py-2 pr-4">
        <Value value={row.left_value} unit={row.left_unit_label} basis={row.left_basis} />
      </td>
      <td className="py-2 pr-4">
        <Value value={row.right_value} unit={row.right_unit_label} basis={row.right_basis} />
      </td>
      <td className="py-2">
        <Change row={row} comparison={comparison} />
      </td>
    </tr>
  );
}

async function Value({
  value,
  unit,
  basis,
}: {
  value?: string | null;
  unit?: string | null;
  basis?: string | null;
}) {
  const s = await getTranslations("study");
  return (
    <>
      <span className="font-semibold">{value ?? "—"}</span>
      {unit ? <span className="ml-1 text-[color:var(--muted)]">{unit}</span> : null}
      {/* The same words the study page uses. A basis the interface has no word
          for still renders, because it is a fact about the record. */}
      {basis ? (
        <span className="block text-[11px] text-[color:var(--muted)]">
          {isKnownBasis(basis) ? s(`basis.${basis}` as "basis.measured") : basis}
        </span>
      ) : null}
    </>
  );
}

async function Change({
  row,
  comparison,
}: {
  row: AssumptionDelta;
  comparison: StudyComparison;
}) {
  const t = await getTranslations("compare");
  const known = ["identical", "equivalent", "different", "incomparable", "unknown"];
  const label = known.includes(row.relation)
    ? t(`relation.${row.relation}` as "relation.identical")
    : t(`relation.${row.relation}` as "relation.added", {
        title: row.relation === "added" ? comparison.right_title : comparison.left_title,
      });

  return (
    <div className="space-y-1">
      <span className="og-tag" title={row.relation === "equivalent" ? t("equivalentHelp") : row.note ?? undefined}>
        {label}
      </span>
      {row.relative_delta != null && row.relation === "different" ? (
        <span className="block font-mono text-xs tabular-nums">
          {row.relative_delta > 0 ? "+" : ""}
          {(row.relative_delta * 100).toFixed(2)}%
        </span>
      ) : null}
      {/* A value can be unchanged and still differ in standing. Shown even when
          the number is identical, because that is precisely the case a numeric
          diff loses. */}
      {row.basis_changed ? (
        <span className="block text-[11px] text-[color:var(--accent-text)]" title={t("basisChangedHelp")}>
          {t("basisChanged", { from: row.left_basis ?? "—", to: row.right_basis ?? "—" })}
        </span>
      ) : null}
      {row.justification ? (
        <span className="block max-w-sm text-[11px] text-[color:var(--muted)]">
          {row.justification}
        </span>
      ) : null}
    </div>
  );
}
