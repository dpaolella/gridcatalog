import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ApiError, IS_SNAPSHOT, type ReviewQueueResponse, reviewQueue } from "@/lib/api";
import { StaticNotice } from "@/components/StaticNotice";
import { perRequest } from "@/lib/rendering";
import { EmptyState } from "@/components/EmptyState";
import { Rule } from "@/components/Brand";
import { formatDate } from "@/lib/format";

/**
 * The steward queue (WP-9.5, PRD §7.6).
 *
 * Read-only here. Confirming a record is a `POST` a steward makes from the
 * record itself, because confirming means "I checked these fields" and the
 * fields are on the record, not in a list of dataset ids.
 *
 * The two refusals are deliberately different, and this page shows the
 * difference rather than flattening it: 401 means sign in, 403 means you are
 * signed in as somebody who is not a steward. Everywhere else in this system a
 * caller who may not see something is told it does not exist — here the
 * queue's *existence* is not a secret, only its contents.
 */

type SearchParams = Promise<{ state?: string }>;

const STATES = ["draft", "in-review", "flagged", "confirmed"] as const;

export default async function ReviewPage({ searchParams }: { searchParams: SearchParams }) {
  // The queue is a signed-in steward's view of unpublished records. A static
  // copy has neither a session nor those records, so it says so rather than
  // rendering an empty queue that looks like "nothing to review".
  if (IS_SNAPSHOT) return <StaticQueue />;
  return <Queue searchParams={searchParams} />;
}

async function StaticQueue() {
  const t = await getTranslations("review");
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <Rule />
        <p className="mt-4 max-w-prose text-sm text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>
      <StaticNotice />
    </div>
  );
}

async function Queue({ searchParams }: { searchParams: SearchParams }) {
  await perRequest();
  const { state = "draft" } = await searchParams;
  const t = await getTranslations("review");

  let queue: ReviewQueueResponse;
  try {
    queue = await reviewQueue(state);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <EmptyState title={status === 403 ? t("forbidden") : t("signIn")}>
        <p>{status === 403 ? t("forbiddenHelp") : t("signIn")}</p>
      </EmptyState>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <Rule />
        <p className="mt-4 max-w-prose text-sm text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>

      <nav className="flex flex-wrap gap-2 text-sm" aria-label={t("state")}>
        {STATES.map((name) => (
          <Link
            key={name}
            href={`/admin/review?state=${name}`}
            className="og-tag px-3 py-1"
            style={
              name === state
                ? { borderColor: "var(--accent)", color: "var(--accent)", fontWeight: 600 }
                : undefined
            }
          >
            {t(`states.${name}`)}
          </Link>
        ))}
      </nav>

      {queue.items.length === 0 ? (
        <EmptyState title={t("empty")}>
          <p>{t("emptyHelp")}</p>
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {queue.items.map((item) => (
            <li
              key={item.dataset_id}
              className="og-card p-4"
              style={
                item.conflict_detail.length
                  ? { borderLeft: "3px solid var(--status-alert)" }
                  : undefined
              }
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <Link
                  href={`/datasets/${item.dataset_id}`}
                  className="font-semibold hover:underline"
                >
                  {item.dataset_id}
                </Link>
                <span className="flex gap-3 text-xs text-[color:var(--muted)]">
                  {item.data_domain ? <span>{item.data_domain}</span> : null}
                  <span>
                    {t("level")} {item.completeness_level}
                  </span>
                  <span>
                    {t("inbound")} {item.inbound_link_count}
                  </span>
                  <span style={{ color: item.validation_conforms ? "var(--status-ok)" : "var(--status-alert)" }}>
                    {t("conforms")}: {item.validation_conforms ? "✓" : "✗"}
                  </span>
                </span>
              </div>

              {item.conflict_detail.length ? (
                <p
                  className="mt-2 border-l-2 pl-3 text-sm"
                  style={{ borderColor: "var(--status-alert)" }}
                  title={t("conflictHelp")}
                >
                  △ {t("conflict")} ({item.conflict_detail.length})
                </p>
              ) : null}

              {item.violations.length ? (
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer text-[color:var(--muted)]">
                    {t("violations")} ({item.violations.length})
                  </summary>
                  <ul className="mt-1 space-y-0.5 text-xs">
                    {item.violations.slice(0, 8).map((violation, index) => (
                      <li key={index}>
                        <code>{typeof violation === "string" ? violation : JSON.stringify(violation)}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {item.confirmed_fields.length ? (
                <p className="mt-2 text-xs text-[color:var(--muted)]">
                  {t("confirmed")}: {item.confirmed_fields.join(", ")}
                </p>
              ) : null}

              {item.reviewed_by ? (
                <p className="mt-1 text-xs text-[color:var(--muted)]">
                  {t("reviewedBy")} {item.reviewed_by} · {formatDate(item.reviewed_at)}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
