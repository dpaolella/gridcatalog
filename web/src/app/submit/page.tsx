import { getTranslations } from "next-intl/server";
import { IS_SNAPSHOT } from "@/lib/api";
import { SubmitForm } from "@/components/SubmitForm";
import { StaticNotice } from "@/components/StaticNotice";

export default async function SubmitPage() {
  const t = await getTranslations("submit");
  return (
    <div className="max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>
      {IS_SNAPSHOT ? <StaticNotice /> : <SubmitForm />}
    </div>
  );
}
