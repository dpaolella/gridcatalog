"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

/**
 * Report an issue on any record, field or distribution (PRD §F3).
 *
 * Three properties the requirement asks for and that are easy to lose:
 *
 * - **Anonymous reports are allowed.** Requiring an account to say "this link
 *   is broken" means the broken link stays broken. The email field is optional
 *   and says why it is there.
 * - **The reference is captured automatically.** A reporter should not have to
 *   copy an identifier, and one who does will copy the wrong one.
 * - **Confirmation without leaving the page.** A redirect to a thank-you page
 *   loses the record they were reading, and they were reading it for a reason.
 */
export function ReportIssue({
  datasetId,
  datasetTitle,
  fieldId,
  distributionId,
}: {
  datasetId: string;
  datasetTitle: string;
  fieldId?: string;
  distributionId?: string;
}) {
  const t = useTranslations("report");
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "sending" | "done" | "failed">("idle");

  async function submit(form: FormData) {
    setState("sending");
    const response = await fetch("/api/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: datasetId,
        field_id: fieldId,
        distribution_id: distributionId,
        issue_type: form.get("issue_type"),
        comment: form.get("comment") || null,
        reporter_email: form.get("email") || null,
      }),
    }).catch(() => null);
    setState(response?.ok ? "done" : "failed");
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="og-tag shrink-0 px-3 py-1.5 text-sm hover:text-[color:var(--foreground)]"
      >
        {t("title")}
      </button>
    );
  }

  return (
    <div
      className="og-card w-full max-w-sm p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="font-semibold">{t("title")}</h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-sm text-[color:var(--muted)]"
        >
          ✕
        </button>
      </div>

      {state === "done" ? (
        <p className="mt-3 text-sm">{t("thanks")}</p>
      ) : (
        <form action={submit} className="mt-3 space-y-3 text-sm">
          <p className="text-[color:var(--muted)]">{t("subtitle")}</p>

          <p className="text-xs text-[color:var(--muted)]">
            {t("target")}: <span className="font-medium">{datasetTitle}</span>
            {fieldId ? ` · ${fieldId}` : ""}
            {distributionId ? ` · ${distributionId}` : ""}
          </p>

          <label className="block">
            <span className="mb-1 block">{t("type")}</span>
            <select
              name="issue_type"
              required
              className="w-full px-2 py-1.5"
            >
              <option value="incorrect-metadata">{t("types.incorrect-metadata")}</option>
              <option value="broken-link">{t("types.broken-link")}</option>
              <option value="licence">{t("types.licence")}</option>
              <option value="duplicate">{t("types.duplicate")}</option>
              <option value="other">{t("types.other")}</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block">{t("comment")}</span>
            <textarea
              name="comment"
              rows={3}
              className="w-full px-2 py-1.5"
            />
          </label>

          <label className="block">
            <span className="mb-1 block">{t("email")}</span>
            <input
              name="email"
              type="email"
              className="w-full px-2 py-1.5"
            />
            <span className="mt-1 block text-xs text-[color:var(--muted)]">{t("emailHelp")}</span>
          </label>

          {state === "failed" ? (
            <p className="text-sm" style={{ color: "var(--status-alert)" }}>
              {t("failed")}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={state === "sending"}
            className="og-cta disabled:opacity-60"
          >
            {state === "sending" ? t("sending") : t("submit")}
          </button>
        </form>
      )}
    </div>
  );
}
