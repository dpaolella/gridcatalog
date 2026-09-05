import { getTranslations } from "next-intl/server";
import { EmptyState } from "@/components/EmptyState";

/**
 * One page for two cases, deliberately.
 *
 * The API answers a restricted record and an absent one identically, and this
 * page must not undo that. It says so plainly rather than pretending the
 * ambiguity is not there — a reader who understands why cannot be misled by
 * it, and one who does not is not misled either.
 */
export default async function DatasetNotFound() {
  const empty = await getTranslations("empty");
  return (
    <EmptyState title={empty("notFound")} action={{ href: "/", label: empty("backToSearch") }}>
      <p>{empty("notFoundHelp")}</p>
    </EmptyState>
  );
}
