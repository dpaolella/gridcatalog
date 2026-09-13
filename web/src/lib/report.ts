export const REPORT_TYPES = [
  { value: "incorrect-metadata", label: "incorrect-metadata" },
  { value: "broken-link", label: "broken-link" },
  { value: "license-question", label: "licence" },
  { value: "duplicate-record", label: "duplicate" },
  { value: "other", label: "other" },
] as const;

export type ReportType = (typeof REPORT_TYPES)[number]["value"];

export interface ReportPayload {
  dataset_id: string;
  target_kind: "dataset" | "field" | "distribution";
  target_id: string | null;
  issue_type: ReportType;
  comment: string | null;
  reporter_contact: string | null;
  captcha_token: string | null;
}

export function reportPayload(form: FormData, target: {
  datasetId: string; fieldId?: string; distributionId?: string;
}, token: string | null): ReportPayload {
  const issueType = REPORT_TYPES.find((type) => type.value === form.get("issue_type"));
  if (!issueType) throw new Error("Invalid report category");
  return {
    dataset_id: target.datasetId,
    target_kind: target.fieldId ? "field" : target.distributionId ? "distribution" : "dataset",
    target_id: target.fieldId ?? target.distributionId ?? null,
    issue_type: issueType.value,
    comment: String(form.get("comment") ?? "") || null,
    reporter_contact: String(form.get("email") ?? "") || null,
    captcha_token: token,
  };
}
