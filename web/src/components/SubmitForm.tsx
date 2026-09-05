"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

/**
 * The intake form (PRD §F3).
 *
 * Required: title, description, at least one access URL, licence. No login,
 * but a contact is captured — a submission nobody can ask a question about
 * usually cannot be catalogued, and the question is almost always "which
 * licence, exactly".
 *
 * **Fire and forget.** The PRD is explicit: confirm receipt, no status
 * tracking back to the submitter. So the confirmation says so, rather than
 * leaving somebody waiting for an update that is never coming.
 */
export function SubmitForm() {
  const t = useTranslations("submit");
  const [urls, setUrls] = useState([""]);
  const [state, setState] = useState<"idle" | "sending" | "done" | "failed">("idle");

  async function submit(form: FormData) {
    setState("sending");
    const response = await fetch("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: form.get("title"),
        description: form.get("description"),
        originator: form.get("originator") || null,
        data_domain: form.get("domain") || null,
        license: form.get("license"),
        access_urls: urls.filter(Boolean),
        format: form.get("format") || null,
        approximate_size: form.get("size") || null,
        update_cadence: form.get("cadence") || null,
        documentation_url: form.get("docs") || null,
        submitter_email: form.get("contact"),
      }),
    }).catch(() => null);
    setState(response?.ok ? "done" : "failed");
  }

  if (state === "done") {
    return (
      <div
        className="rounded-lg border p-6"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      >
        <p className="font-medium">{t("thanks")}</p>
        <p className="mt-2 text-sm text-[color:var(--muted)]">{t("thanksHelp")}</p>
      </div>
    );
  }

  return (
    <form action={submit} className="space-y-4 text-sm">
      <Field name="title" label={t("datasetTitle")} required />
      <Field name="description" label={t("description")} required textarea />
      <Field name="originator" label={t("originator")} />
      <Field name="domain" label={t("domain")} />
      <Field name="license" label={t("license")} required />

      <fieldset>
        <legend className="mb-1 block">
          {t("accessUrl")} <Required />
        </legend>
        {urls.map((url, index) => (
          <input
            key={index}
            type="url"
            value={url}
            required={index === 0}
            onChange={(event) => {
              const next = [...urls];
              next[index] = event.target.value;
              setUrls(next);
            }}
            className="mb-2 w-full rounded border px-2 py-1.5"
            style={{ borderColor: "var(--border)", background: "var(--surface)" }}
          />
        ))}
        <button
          type="button"
          onClick={() => setUrls([...urls, ""])}
          className="text-xs text-[color:var(--accent)] hover:underline"
        >
          + {t("addUrl")}
        </button>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field name="format" label={t("format")} />
        <Field name="size" label={t("size")} />
        <Field name="cadence" label={t("cadence")} />
      </div>

      <Field name="docs" label={t("docs")} />
      <Field name="contact" label={t("contact")} type="email" required help={t("contactHelp")} />

      {state === "failed" ? (
        <p style={{ color: "var(--grade-d)" }}>{t("failed")}</p>
      ) : null}

      <button
        type="submit"
        disabled={state === "sending"}
        className="rounded px-4 py-2 text-white disabled:opacity-60"
        style={{ background: "var(--accent)" }}
      >
        {state === "sending" ? t("sending") : t("submit")}
      </button>
    </form>
  );
}

function Required() {
  const t = useTranslations("submit");
  return (
    <span className="text-xs text-[color:var(--muted)]" title={t("required")}>
      *
    </span>
  );
}

function Field({
  name,
  label,
  required,
  textarea,
  type = "text",
  help,
}: {
  name: string;
  label: string;
  required?: boolean;
  textarea?: boolean;
  type?: string;
  help?: string;
}) {
  const style = { borderColor: "var(--border)", background: "var(--surface)" };
  return (
    <label className="block">
      <span className="mb-1 block">
        {label} {required ? <Required /> : null}
      </span>
      {textarea ? (
        <textarea
          name={name}
          required={required}
          rows={4}
          className="w-full rounded border px-2 py-1.5"
          style={style}
        />
      ) : (
        <input
          name={name}
          type={type}
          required={required}
          className="w-full rounded border px-2 py-1.5"
          style={style}
        />
      )}
      {help ? <span className="mt-1 block text-xs text-[color:var(--muted)]">{help}</span> : null}
    </label>
  );
}
