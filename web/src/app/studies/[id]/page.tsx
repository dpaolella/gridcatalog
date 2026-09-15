import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { AssumptionTree } from "@/components/AssumptionTree";
import {
  IS_SNAPSHOT,
  NotFoundError,
  type StudyDetail,
  getStudy,
  snapshotStudyIds,
} from "@/lib/api";
import { formatDate, formatNumber, iriTail } from "@/lib/format";
import { perRequest } from "@/lib/rendering";
import { isKnownKind } from "@/lib/studies";

/**
 * One study: what it assumed, what it ran on, and who ran it (#82).
 *
 * The page the registry was built for and did not have. A filing's assumption
 * set was writable, validated, and reachable by nobody — so the most useful
 * thing the catalog knew about a proposal, that the 0.098 in it is an
 * *estimate* drawn from a named exhibit and landing on a named field of a
 * published network, reached no reader at any layer.
 *
 * ## Why not `/datasets/[id]`
 *
 * A study is not a dataset. It has no distributions, no licence and no link
 * health, and rendering it through the record page would give it a Downloads
 * tab with nothing in it — which a reader reads as a broken record rather than
 * as a different kind of thing. The seven tabs answer "can I use this data";
 * this page answers "what did they assume", and those are different questions.
 *
 * ## The tree
 *
 * Assumptions are grouped by the Sienna field path each one names, because the
 * path is an address and addresses nest. A few hundred values in a flat table
 * is a wall of repeated prefixes; the same values under the tree the paths
 * already describe let a reader see that eleven of them are financial and two
 * are policy without reading a single row.
 *
 * Nothing here is `"use client"`. `<details>` is native, so the tree opens and
 * closes on the published static site with no JavaScript at all — which is the
 * deployment a regulator is most likely to be reading it on.
 */

type Params = Promise<{ id: string }>;

/** See the note on `datasets/[id]`: the export exists only in the build that
 *  has a use for it, because any `generateStaticParams` makes the segment
 *  static and an empty list makes every request an on-demand render in the
 *  static store. */
export const generateStaticParams = IS_SNAPSHOT
  ? async () => (await snapshotStudyIds()).map((id) => ({ id }))
  : undefined;

export async function generateMetadata({ params }: { params: Params }) {
  const { id } = await params;
  await perRequest();
  try {
    const study = await getStudy(id);
    return { title: study.title, description: study.summary ?? undefined };
  } catch {
    return { title: "Not found" };
  }
}

export default async function StudyPage({ params }: { params: Params }) {
  const { id } = await params;
  await perRequest();
  const s = await getTranslations("study");
  const hub = await getTranslations("hub");

  let study: StudyDetail;
  try {
    study = await getStudy(id);
  } catch (error) {
    if (error instanceof NotFoundError) notFound();
    throw error;
  }

  // The filing this contests, for its title. Allowed to fail: a study whose
  // parent is not in this catalog still says it has one, and the link degrades
  // to the slug rather than taking the page down.
  const parent = study.parent_study_id
    ? await getStudy(study.parent_study_id).catch(() => null)
    : null;

  const frozen = formatDate(study.frozen_at);

  return (
    <article className="space-y-8">
      <nav className="text-sm text-[color:var(--muted)]">
        <Link href="/studies" className="hover:underline">
          ← {s("backToStudies")}
        </Link>
      </nav>

      <header className="relative -mx-5 overflow-hidden px-5 pb-6">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-3xl">
          <p className="og-eyebrow">{hub("studies.title")}</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">{study.title}</h1>
          <Rule />

          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
            {study.study_kind ? (
              <span className="og-tag">
                {isKnownKind(study.study_kind)
                  ? s(`kinds.${study.study_kind}` as "kinds.filing")
                  : study.study_kind}
              </span>
            ) : null}
            {study.docket ? (
              <span className="og-tag">{s("docket", { docket: study.docket })}</span>
            ) : null}
            {study.jurisdiction ? <span className="og-tag">{study.jurisdiction}</span> : null}
            {study.version ? (
              <span className="og-tag">{s("version", { version: study.version })}</span>
            ) : null}
            {/* What question it answers, from the analysis-type scheme. Not
                decoration: an IRP, a production-cost run and a power-flow study
                are different objects, and a reader who wants one of them can
                reject the other two here. */}
            {study.analysis_types.map((type) => (
              <span key={type.iri} className="og-tag">
                {type.label ?? iriTail(type.iri)}
              </span>
            ))}
          </div>

          {study.publisher ? (
            <p className="mt-4 text-sm text-[color:var(--muted)]">{study.publisher}</p>
          ) : null}
          <p className="mt-1 text-sm text-[color:var(--muted)]" title={s("frozenHelp")}>
            {frozen ? s("frozen", { date: frozen }) : s("frozenUnknown")}
          </p>

          {study.summary ? <p className="mt-4 max-w-prose">{study.summary}</p> : null}
          {study.description ? (
            <p className="mt-3 max-w-prose text-sm text-[color:var(--muted)]">
              {study.description}
            </p>
          ) : null}

          {/* An intervention that does not name what it intervenes in is an
              assertion floating free of the filing it contests, and a reader
              cannot put the two side by side — which is the whole evaluation
              story. The shapes require the link; this renders it. */}
          {study.parent_study ? (
            <p className="mt-4 text-sm">
              {s("contests")}:{" "}
              {study.parent_study_id ? (
                <Link
                  href={`/studies/${study.parent_study_id}`}
                  className="font-medium text-[color:var(--accent-text)] hover:underline"
                >
                  {parent?.title ?? study.parent_study_id}
                </Link>
              ) : (
                <span className="text-[color:var(--muted)]">{iriTail(study.parent_study)}</span>
              )}
            </p>
          ) : null}

          {study.citation_id ? (
            <p className="mt-4 text-xs text-[color:var(--muted)]">
              {s("citation", { id: study.citation_id })}
            </p>
          ) : null}
        </div>
      </header>

      {/* The arrangement the registry exists to make visible: a study is a set
          of choices laid over a network somebody else can download and check.
          Only the choices are the filer's. */}
      {study.bound_reference_models.length > 0 ? (
        <section className="og-card p-5">
          <h2 className="text-sm font-semibold">{s("boundTo")}</h2>
          <p className="mt-1 text-xs text-[color:var(--muted)]">{s("boundToHelp")}</p>
          <ul className="mt-3 space-y-1 text-sm">
            {study.bound_reference_models.map((iri) => {
              const slug = study.bound_reference_model_ids.includes(iriTail(iri))
                ? iriTail(iri)
                : null;
              return (
                <li key={iri}>
                  {slug ? (
                    <Link
                      href={`/datasets/${slug}`}
                      className="font-medium text-[color:var(--accent-text)] hover:underline"
                    >
                      {slug}
                    </Link>
                  ) : (
                    <span title={s("notInCatalogHelp")}>
                      {iriTail(iri)}{" "}
                      <span className="text-[color:var(--muted)]">({s("notInCatalog")})</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <section className="space-y-4">
        <div className="max-w-prose">
          <h2 className="text-xl font-semibold tracking-tight">{s("assumptions")}</h2>
          <p className="mt-1 text-sm text-[color:var(--muted)]">{s("assumptionsBlurb")}</p>
        </div>

        {study.assumption_sets.length === 0 ? (
          <div className="og-card p-5">
            <p className="font-semibold">{s("noAssumptions")}</p>
            <p className="mt-1 text-sm text-[color:var(--muted)]">{s("noAssumptionsHelp")}</p>
          </div>
        ) : (
          study.assumption_sets.map((set) => (
            <section key={set.iri} className="og-card p-5">
              <h3 className="font-semibold">{set.title ?? set.id}</h3>
              {set.description ? (
                <p className="mt-1 max-w-prose text-sm text-[color:var(--muted)]">
                  {set.description}
                </p>
              ) : null}
              <ul className="mt-3 flex flex-wrap gap-2 text-xs">
                <li className="og-tag">{s("assumptionCount", { count: set.assumptions.length })}</li>
                {set.schema_pin ? (
                  <li className="og-tag" title={s("schemaPinHelp")}>
                    {s("schemaPin", { pin: set.schema_pin.slice(0, 8) })}
                  </li>
                ) : null}
                {set.receipt_errors !== null && set.receipt_errors !== undefined ? (
                  <li className="og-tag">
                    {s("receipt", {
                      errors: set.receipt_errors,
                      warnings: set.receipt_warnings ?? 0,
                    })}
                  </li>
                ) : null}
                {set.forked_from ? (
                  <li className="og-tag">
                    {s("forkedFrom", { title: iriTail(set.forked_from) })}
                  </li>
                ) : null}
              </ul>

              <div className="mt-4">
                <AssumptionTree assumptions={set.assumptions} />
              </div>
            </section>
          ))
        )}
      </section>

      <section className="space-y-4">
        <div className="max-w-prose">
          <h2 className="text-xl font-semibold tracking-tight">{s("runs")}</h2>
          <p className="mt-1 text-sm text-[color:var(--muted)]">{s("runsBlurb")}</p>
        </div>

        {study.run_records.length === 0 ? (
          <div className="og-card p-5">
            <p className="font-semibold">{s("noRuns")}</p>
            <p className="mt-1 text-sm text-[color:var(--muted)]">{s("noRunsHelp")}</p>
          </div>
        ) : (
          <ul className="space-y-3">
            {study.run_records.map((run) => (
              <li key={run.iri} className="og-card p-5 text-sm">
                {run.executed_by ? (
                  <p className="font-semibold">{s("runBy", { party: run.executed_by })}</p>
                ) : null}
                <p className="mt-1 text-[color:var(--muted)]">
                  {run.tool
                    ? run.tool_version
                      ? s("runTool", {
                          tool: run.tool,
                          version: run.tool_version,
                          solver: run.solver ?? "—",
                        })
                      : s("runToolNoVersion", { tool: run.tool, solver: run.solver ?? "—" })
                    : null}
                </p>
                <ul className="mt-2 flex flex-wrap gap-2 text-xs">
                  {run.executed_at ? (
                    <li className="og-tag">
                      {s("runAt", { date: formatDate(run.executed_at) ?? run.executed_at })}
                    </li>
                  ) : null}
                  {run.objective_value !== null && run.objective_value !== undefined ? (
                    <li className="og-tag">
                      {s("runObjective", {
                        value: `${formatNumber(run.objective_value)} ${
                          run.objective_unit_label ?? iriTail(run.objective_unit)
                        }`,
                      })}
                    </li>
                  ) : null}
                  {run.wall_time_seconds ? (
                    <li className="og-tag">{s("runWallTime", { seconds: run.wall_time_seconds })}</li>
                  ) : null}
                  {run.on_reference_model_id ? (
                    <li>
                      <Link
                        href={`/datasets/${run.on_reference_model_id}`}
                        className="og-tag hover:underline"
                      >
                        {run.on_reference_model_id}
                      </Link>
                    </li>
                  ) : null}
                </ul>
                {run.case_hash ? (
                  <p
                    className="mt-2 break-all font-mono text-xs text-[color:var(--muted)]"
                    title={s("caseHashHelp")}
                  >
                    {s("caseHash", { hash: run.case_hash })}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
