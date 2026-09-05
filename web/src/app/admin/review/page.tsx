import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ApiError, type ReviewQueueResponse, reviewQueue } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
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
export const dynamic = "force-dynamic";

type SearchParams = Promise<{ state?: string }>;

const STATES = ["draft", "in-review", "flagged", "confirmed"] as const;

export default async function ReviewPage({ searchParams }: { searchParams: SearchParams }) {
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
        <p className="mt-1 max-w-prose text-sm text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>

      <nav className="flex flex-wrap gap-2 text-sm" aria-label={t("state")}>
        {STATES.map((name) => (
          <Link
            key={name}
            href={`/admin/review?state=${name}`}
            className="rounded border px-3 py-1"
            style={{
              borderColor: name === state ? "var(--accent)" : "var(--border)",
              background: name === state ? "var(--accent-soft)" : undefined,
            }}
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
              className="rounded-lg border p-4"
              style={{
                borderColor: item.conflict_detail.length ? "var(--grade-c)" : "var(--border)",
                background: "var(--surface)",
              }}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <Link
                  href={`/datasets/${item.dataset_id}`}
                  className="font-medium text-[color:var(--accent)] hover:underline"
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
                  <span style={{ color: item.validation_conforms ? "var(--grade-a)" : "var(--grade-d)" }}>
                    {t("conforms")}: {item.validation_conforms ? "✓" : "✗"}
                  </span>
                </span>
              </div>

              {item.conflict_detail.length ? (
                <p
                  className="mt-2 border-l-2 pl-2 text-sm"
                  style={{ borderColor: "var(--grade-c)" }}
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
