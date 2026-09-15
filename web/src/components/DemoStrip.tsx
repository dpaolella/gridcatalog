import { getTranslations } from "next-intl/server";

/**
 * What this catalog is, on every page, permanently (#85).
 *
 * A strip rather than a splash, and the difference is the whole point: a
 * splash is dismissed once and then cropped out of every screenshot anybody
 * takes afterwards. What circulates is the part without the caveat. This sits
 * above the header on every route, costs one line, and cannot be separated
 * from the thing it qualifies.
 *
 * It says two things, because the demo makes two claims a viewer would
 * otherwise read wrongly:
 *
 * - **Some records here were invented.** They are marked individually too, but
 *   a reader who lands on a list needs to know before they start reading that
 *   the corpus is mixed.
 * - **The curation was done by people.** Every record says who assessed it, so
 *   a viewer reads the graded, sourced state as an achievable mix of staff
 *   work, publisher self-report and machine extraction — rather than as
 *   something that happened by itself. That turns the demo into an argument
 *   *for* a curation budget instead of evidence that none is needed.
 */
export async function DemoStrip() {
  const t = await getTranslations("provenanceStrip");

  return (
    <div
      className="w-full border-b px-5 py-2 text-xs"
      style={{
        borderColor: "var(--border)",
        background: "color-mix(in srgb, var(--accent) 8%, transparent)",
      }}
    >
      <p className="mx-auto max-w-6xl text-[color:var(--muted)]">
        <span className="font-semibold text-[color:var(--accent-text)]">{t("badge")}</span>{" "}
        {t("body")}
      </p>
    </div>
  );
}
