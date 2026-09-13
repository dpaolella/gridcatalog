"use client";

import { useTranslations } from "next-intl";
import type { DatasetSummary } from "@/lib/api";

export function ModelFidelity({ model }: { model: DatasetSummary }) {
  const t = useTranslations("referenceModel");
  return <span className="og-eyebrow">
    {model.fidelity_class ? t(`fidelity.${model.fidelity_class}`) : t("fidelity.undeclared")}
    {model.network_element_count != null ? ` · ${t("elements", { count: model.network_element_count })}` : ""}
  </span>;
}

export function ModelLicenseNotice({ model }: { model: DatasetSummary }) {
  const t = useTranslations("referenceModel");
  if (!model.share_alike) return null;
  return <p className="og-share-alike mt-3 text-sm">
    <strong>{t("shareAlike")}{model.license_id ? ` · ${model.license_id}` : ""}</strong>{" "}
    {t("shareAlikeHelp")}
    {model.license_url ? <>{" "}<a href={model.license_url} className="underline">{t("licenceLabel")} ↗</a></> : null}
  </p>;
}

export function ModelPartition({ model }: { model: DatasetSummary }) {
  const t = useTranslations("referenceModel");
  if (!model.question_classes?.length) return <p className="mt-4 text-sm text-[color:var(--muted)]">{t("noPartition")}</p>;
  return <section className="mt-4">
    <h3 className="og-eyebrow">{t("partition")}</h3>
    <ul className="mt-2 space-y-2">
      {model.question_classes.map((qc) => <li key={qc.question_class} className="flex flex-wrap items-baseline gap-x-3 text-sm">
        <span className={`og-tag shrink-0 og-robustness-${qc.robustness}`}>{t(`robustness.${qc.robustness}`)}</span>
        <span className="min-w-0">{qc.question_class}</span>
        <span className="w-full text-xs text-[color:var(--muted)]">{qc.basis ?? t("noBasis")}</span>
      </li>)}
    </ul>
  </section>;
}

export function ReferenceModelSuitability({ model }: { model: DatasetSummary }) {
  const t = useTranslations("referenceModel");
  return <section aria-label={t("suitability")} className="og-card p-5">
    <h2 className="mb-2 font-semibold">{t("suitability")}</h2>
    <ModelFidelity model={model} />
    <ModelLicenseNotice model={model} />
    <ModelPartition model={model} />
  </section>;
}
