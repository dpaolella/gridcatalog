import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { BrowsePanel } from "@/components/BrowsePanel";
import { CatalogCoverage } from "@/components/CatalogCoverage";
import { EmptyState } from "@/components/EmptyState";
import { ResultRow } from "@/components/ResultRow";
import { listGaps, search } from "@/lib/api";
import { perRequest } from "@/lib/rendering";

/**
 * The Hub's front door: the catalog itself, not a menu that describes it.
 *
 * It was three cards and two paragraphs. That answered a real question — the
 * vision asks whether the registry and the reference datasets are one product
 * or two, and a nav of peers settles it before anybody reads a word — but it
 * answered it by making the first thing a visitor does a click. #100 is about
 * the cost of that: a registry whose front page is a table of contents reads
 * as a link list, and the one claim it is entitled to make, that these records
 * are *described* rather than merely listed, is exactly what a table of
 * contents cannot show.
 *
 * So the peers stay and move into the browse panel, where they carry the
 * counts the index actually has, and the records come to the front.
 *
 * ## The scale cues this deliberately does not borrow
 *
 * The page this mirrors leads with *2,555,000 models*, and much of its
 * arrangement exists to make that number felt. This corpus is double digits and
 * authored by hand. So: no counter styled as a statement number, no infinite
 * scroll performing a depth that is not there, no "browse everything" over
 * nineteen records. The total is stated plainly, once, in the sentence that
 * links to the catalog — because *stating* a small number is the honest move
 * and hiding it is the dishonest one. What the hero claims is custody and
 * description, which are true of nineteen records and would still be true of
 * nineteen thousand.
 *
 * ## One search, and it runs on the server in both builds
 *
 * Everything here comes from a single `search()` — the same call `/datasets`
 * makes, with the same facets — so a count in the panel and the list beside it
 * cannot disagree. Nothing on this page reads the query string, which is what
 * lets all of it prerender: the static export ships the panel, the crossing and
 * the rows as HTML rather than as a Suspense fallback. That is the trap
 * `GeographyPicker` and `StaticSearch` both document, met here by not filtering
 * in place at all — every control is a link into `/datasets`, where filtering
 * lives.
 */

/** Records on the front page.
 *
 * Eight, against `/datasets`' twenty. `ResultRow` is a client component, so
 * each row is serialised into the flight payload whether or not anybody scrolls
 * to it, and #34 is the standing constraint on how much of the catalog a first
 * screen may carry.
 *
 * Chosen by looking at the rendered page rather than by arithmetic. Twelve ran
 * past five thousand pixels and turned a front page into the catalog with a
 * header on it, which is the opposite failure to the one this replaced: the
 * point is that the records are here, not that all of them are. Eight is a
 * selection, and the sentence beside it says how many there are in total and
 * links to them.
 */
const SHOWN = 8;

/** Aggregated over the whole catalog, because nothing here narrows it.
 *
 * `domain_coverage` is the crossing the table reads and is not a panel entry —
 * see `catalog-search`'s `PANEL_HIDDEN`. `record_type` is requested for the
 * Sections block's counts, and requesting it is what makes a *missing* bucket
 * mean "none of these" rather than "nobody asked". */
const FACETS = [
  "record_type",
  "data_domain",
  "provenance_class",
  "license",
  "completeness_level",
  "domain_coverage",
];

export default async function HubPage() {
  // A view over the live index, so it is rendered per request wherever there is
  // one, and prerendered where there is not.
  await perRequest();

  const app = await getTranslations("app");
  const t = await getTranslations("home");
  const hub = await getTranslations("hub");
  const empty = await getTranslations("empty");

  // `-modified` rather than relevance: with no query there is nothing to rank,
  // and "what changed most recently" is the one ordering a catalog can offer
  // honestly without inventing a popularity it does not measure. Both builds
  // answer the same question — `search()` sorts the snapshot index before
  // slicing it — though not always with the same eight records: a restricted
  // record is ordered live by the date in the index and exported as a stub
  // with no date at all, so it places differently. Each build orders by what
  // it knows, which is the property that matters.
  const response = await search({
    sort: "-modified",
    limit: String(SHOWN),
    facets: FACETS.join(","),
  });
  const register = await listGaps();

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-7 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {app("tagline")}
          </h1>
          <Rule />
          {/* The custody rule, in the hero rather than in a panel at the foot of
              the page. It is the question a publisher asks first and a funder
              asks second, and it is the claim this front page is making instead
              of a claim about size. Below the fold it was neither. */}
          <p className="mt-5 text-base text-[color:var(--muted)]">
            {app("description")}
          </p>
          <p className="mt-3 max-w-prose text-sm text-[color:var(--muted)]">
            {hub("custody.body")}
          </p>
        </div>
      </section>

      <div className="grid gap-10 md:grid-cols-[13rem_1fr]">
        <BrowsePanel facets={response.facets} />

        <div className="min-w-0 space-y-6">
          {/* The crossing first, then the records it describes. Domain against
              how well described, which is the useful first glance — "how many
              good ones, where" rather than "how many" — and the one thing a
              list of eight cannot say about a catalog. Same `facets` object as
              the panel beside it, so the two are one aggregation. */}
          <CatalogCoverage facets={response.facets} gaps={register} />

          <section aria-labelledby="recent-heading" className="space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 id="recent-heading" className="og-eyebrow">
                {t("recent")}
              </h2>
              {/* The size of the catalog, said once and in a sentence rather
                  than set as a number to be impressed by. */}
              <Link
                href="/datasets"
                className="text-sm font-medium text-[color:var(--accent-text)] hover:underline"
              >
                {t("browseAll", { total: response.total })}
              </Link>
            </div>

            {response.results.length === 0 ? (
              /* A front page with no records is either an empty catalog or an
                 API that would not answer, and this page cannot tell them
                 apart — so it says neither. */
              <EmptyState title={empty("catalogUnavailable")}>
                <p>{empty("catalogUnavailableHelp")}</p>
              </EmptyState>
            ) : (
              <ul className="space-y-4">
                {response.results.map((dataset) => (
                  <ResultRow key={dataset.id} dataset={dataset} />
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
