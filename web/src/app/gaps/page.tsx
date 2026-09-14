import Link from "next/link";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { EmptyState } from "@/components/EmptyState";
import { IS_SNAPSHOT } from "@/lib/api";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("gapsMoved.title") };
}

/**
 * The gap register moved into the catalog (#99).
 *
 * Kept as a URL rather than deleted, because this one was published: it was a
 * nav peer for several releases and is in whatever anyone bookmarked or linked.
 * Answering 404 would make the register look withdrawn, when what happened is
 * that it moved to where it is read — beside the domain it concerns, at the
 * moment somebody is deciding whether this catalog can answer their question.
 *
 * Two behaviours, because the builds can do different things and pretending
 * otherwise breaks one of them. A server can redirect; a static export cannot
 * — `redirect()` needs a runtime, and calling it during `next build` with
 * `output: export` fails the build rather than emitting a file. So the export
 * gets a page that says where the register went and links to it, which is
 * what a redirect is for anyway, and the live build gets the redirect.
 */
export default async function GapsPage() {
  if (!IS_SNAPSHOT) redirect("/datasets");

  const t = await getTranslations("hub");
  return (
    <EmptyState title={t("gapsMoved.title")}>
      <p>{t("gapsMoved.blurb")}</p>
      <Link href="/datasets" className="og-cta mt-3 inline-block">
        {t("gapsMoved.link")}
      </Link>
    </EmptyState>
  );
}
