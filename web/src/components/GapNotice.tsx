import { getTranslations } from "next-intl/server";
import type { DataGap } from "@/lib/api";

/**
 * What the catalog says when it has nothing to offer (#56).
 *
 * PRD §5: saying what does not exist is a feature. "No datasets match this
 * search" tells a reader the catalog is small. "Nothing open supplies this,
 * here is why, here is who found that and when" tells them something true
 * about the field, and it is the one answer this catalog can give that a
 * search engine cannot.
 *
 * The attribution is not decoration. A gap claims something about the whole
 * open landscape, which is a far stronger claim than one about a single
 * dataset, and a reader who cannot see who made it and when has no way to
 * weigh it. `stale` says the register's own review date has passed — shown
 * rather than hidden, because losing the finding is worse than showing an
 * ageing one.
 */
export async function GapNotice({ gaps }: { gaps: DataGap[] }) {
  const t = await getTranslations("empty");
  if (gaps.length === 0) return null;

  return (
    <section className="og-card mt-4 space-y-3 p-4 text-left">
      <h2 className="font-semibold text-[color:var(--accent-text)]">{t("gapTitle")}</h2>
      <p className="text-sm text-[color:var(--muted)]">{t("gapHelp")}</p>
      <ul className="space-y-3">
        {gaps.map((gap) => (
          <li key={gap.id} className="space-y-1">
            <p className="text-sm font-medium">{gap.title}</p>
            <p className="text-sm text-[color:var(--muted)]">{gap.reason}</p>
            <p className="text-xs text-[color:var(--muted)]">
              {t("gapObserved", { observer: gap.observed_by, year: gap.observed })}
              {gap.stale ? ` · ${t("gapStale", { year: gap.observed })}` : null}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
