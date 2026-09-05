import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { getDomains } from "@/lib/api";
import { formatNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * The ten data domains (PRD §4.1 D3), each a way into the catalog.
 *
 * Counts are entitlement-scoped, so a domain whose only records are restricted
 * reads as empty rather than as a number the caller cannot reach.
 */
export default async function DomainsPage() {
  const nav = await getTranslations("nav");
  const domains = await getDomains();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">{nav("domains")}</h1>
      <ul className="grid gap-3 sm:grid-cols-2">
        {domains.map((domain) => (
          <li key={domain.id}>
            <Link
              href={`/?data_domain=${encodeURIComponent(domain.iri)}`}
              className="block h-full rounded-lg border p-4 hover:bg-[color:var(--accent-soft)]"
              style={{ borderColor: "var(--border)", background: "var(--surface)" }}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{domain.label}</span>
                <span className="tabular-nums text-sm text-[color:var(--muted)]">
                  {formatNumber(domain.dataset_count)}
                </span>
              </div>
              {domain.definition ? (
                <p className="mt-1 text-sm text-[color:var(--muted)]">{domain.definition}</p>
              ) : null}
              {domain.structural_note ? (
                <p className="mt-2 border-l-2 pl-2 text-xs text-[color:var(--muted)]" style={{ borderColor: "var(--grade-c)" }}>
                  {domain.structural_note}
                </p>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
