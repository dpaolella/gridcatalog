import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";
import { type DataGap, listGaps } from "@/lib/api";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("gaps.title") };
}

/**
 * The gap register, as its own section rather than a consolation prize.
 *
 * It used to appear only under an empty search — the reader who found nothing
 * got told why, and nobody else ever saw it. That is backwards for the one
 * user story aimed at the people who pay for this: "a funder can see where
 * data is missing, by domain and geography, and fund the gaps". A funder does
 * not arrive by searching for something that does not exist.
 *
 * Every entry carries who observed it and when, because a gap claims something
 * about the whole open landscape — a far stronger claim than anything said
 * about a single dataset — and a reader who cannot see who made it has no way
 * to weigh it. `stale` is shown rather than hidden: losing the finding is
 * worse than showing an ageing one.
 */
export default async function GapsPage() {
  const t = await getTranslations("hub");
  const empty = await getTranslations("empty");
  const gaps = await listGaps();

  const byDomain = new Map<string, DataGap[]>();
  for (const gap of gaps ?? []) {
    byDomain.set(gap.domain, [...(byDomain.get(gap.domain) ?? []), gap]);
  }

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight">{t("gaps.title")}</h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">{t("gaps.blurb")}</p>
        </div>
      </section>

      {gaps === null ? (
        /* Not the same as an empty register, and never rendered as one. A gap
           claims something about the whole open landscape; saying there are
           none because a file would not load is a strong claim made on no
           evidence at all. */
        <EmptyState title={empty("gapsUnavailable")}>
          <p>{empty("gapsUnavailableHelp")}</p>
        </EmptyState>
      ) : gaps.length === 0 ? (
        <EmptyState title={empty("noGaps")}>
          <p>{empty("noGapsHelp")}</p>
        </EmptyState>
      ) : (
        <div className="space-y-8">
          {[...byDomain.entries()].map(([domain, entries]) => (
            <section key={domain} className="space-y-3">
              <h2 className="og-eyebrow">{domain}</h2>
              <ul className="space-y-3">
                {entries.map((gap) => (
                  <li key={gap.id} className="og-card space-y-1 px-5 py-4">
                    <p className="font-medium">{gap.title}</p>
                    <p className="text-sm text-[color:var(--muted)]">{gap.reason}</p>
                    <p className="text-xs text-[color:var(--muted)]">
                      {empty("gapObserved", {
                        observer: gap.observed_by,
                        year: gap.observed,
                      })}
                      {gap.stale ? ` · ${empty("gapStale", { year: gap.observed })}` : null}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
