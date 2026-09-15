import Link from "next/link";
import { getTranslations } from "next-intl/server";
import type { StudyUsage as Usage } from "@/lib/api";
import { formatDate } from "@/lib/format";

/**
 * Registered studies standing on this record (#82).
 *
 * The count that means something. `usage_evidence` on the Connections tab is a
 * citation a harvest found in a source's own metadata — a string, unverifiable,
 * and absent for most records because most sources have no field that could
 * carry one. Every study here is an object in this catalog with an assumption
 * set behind it, so the number is a claim a reader can open and check.
 *
 * Two ways to stand on a record, kept apart because they are different claims.
 * Binding it as the network a study ran on says the study's results are about
 * this topology. Citing it as the source of an assumption value says one number
 * in the study came from here — which may matter more to somebody deciding
 * whether the filing's conclusion survives a correction to this dataset.
 */
export async function StudyUsage({
  usage,
  isReferenceModel,
}: {
  usage: Usage | null;
  isReferenceModel?: boolean;
}) {
  const s = await getTranslations("study");

  // Null is "not known" — the read failed — and says nothing at all rather than
  // reporting a zero the catalog did not establish.
  if (!usage) return null;

  if (usage.total === 0) {
    // Zero is worth saying on a network, which exists to be run on, and is
    // noise on the other four hundred records: a dataset nobody has filed a
    // study against is the ordinary case, and a line saying so on every page
    // would train readers to skip the place where the real count appears.
    if (!isReferenceModel) return null;
    return (
      <section className="og-card max-w-prose p-3 text-sm">
        <p className="font-semibold">{s("usedByNone")}</p>
        <p className="mt-1 text-[color:var(--muted)]">{s("usedByNoneHelp")}</p>
      </section>
    );
  }

  return (
    <section className="og-card max-w-prose p-3 text-sm">
      <h2 className="font-semibold text-[color:var(--accent-text)]" title={s("usedByHelp")}>
        {s("usedBy", { count: usage.total })}
      </h2>
      <ul className="mt-2 space-y-2">
        {usage.studies.map((study) => {
          const frozen = formatDate(study.frozen_at);
          return (
            <li key={study.iri}>
              <Link href={`/studies/${study.id}`} className="font-medium hover:underline">
                {study.title}
              </Link>
              <p className="text-xs text-[color:var(--muted)]">
                {[
                  study.docket ? s("docket", { docket: study.docket }) : null,
                  frozen ? s("frozen", { date: frozen }) : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <ul className="mt-0.5 text-xs text-[color:var(--muted)]">
                {study.roles.includes("reference-model") ? (
                  <li>{s("roleReferenceModel")}</li>
                ) : null}
                {study.roles.includes("assumption-source") ? (
                  <li>
                    {s("roleAssumptionSource")}
                    {study.assumption_paths.length > 0
                      ? ` — ${s("rolePaths", {
                          count: study.assumption_paths.length,
                          paths: study.assumption_paths.join(", "),
                        })}`
                      : ""}
                  </li>
                ) : null}
              </ul>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
