import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Rule } from "@/components/Brand";

/**
 * What a static copy of this site cannot do, said plainly.
 *
 * The site built for GitHub Pages is files. Reading works — it is the same
 * data, exported by driving the real API, so the records, schemas, grades and
 * connections are byte-identical to the live ones. Writing does not, and
 * neither does anything that depends on who you are.
 *
 * A disabled form with no explanation is worse than no form: the reader
 * assumes it is broken and reports it. So the page says which half of the
 * product they are looking at, and where the other half lives.
 */
export async function StaticNotice({ children }: { children?: React.ReactNode }) {
  const t = await getTranslations("static");

  return (
    <section className="og-card p-6">
      <p className="og-eyebrow" style={{ color: "var(--accent-text)" }}>
        {t("badge")}
      </p>
      <p className="mt-2 font-semibold">{t("title")}</p>
      <Rule className="!mt-3" />
      <p className="mt-4 max-w-prose text-sm text-[color:var(--muted)]">{t("help")}</p>
      {children}
      <p className="mt-4">
        <Link href="/developers" className="og-cta">
          {t("runIt")}
        </Link>
      </p>
    </section>
  );
}
