import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { HexWash, Rule } from "@/components/Brand";
import { EmptyState } from "@/components/EmptyState";
import { listStudies } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { perRequest } from "@/lib/rendering";
import { type StudyRow, type StudyThread, isKnownKind, studyDate, threads } from "@/lib/studies";

export async function generateMetadata() {
  const t = await getTranslations("hub");
  return { title: t("studies.title") };
}

/**
 * Studies and the assumption sets they stand on (#82).
 *
 * The section was an honest empty state for as long as nothing was projected
 * into the index under these types. Now something is, and the question becomes
 * what a list of studies should look like — which is not a list.
 *
 * A regulatory docket is a contested thing: a utility files, an intervenor
 * contests one number in it, staff files its own analysis. Flat and sorted by
 * date, that is four unrelated rows in which the reader meets the rebuttal
 * before the thing rebutted, and the most important fact about them — that
 * three exist *because of* the first — is the one fact the list does not carry.
 * So they group into threads, on the docket and the parent each record already
 * declares. Nothing is inferred from titles or dates: "this contests that" is a
 * strong claim and the catalog only makes it where a record made it.
 *
 * All of them, unpaged. There are two. When there are two hundred this needs
 * the same facets the catalog has, and the thread is the unit that would be
 * paged — a docket split across a page boundary is the failure this page is
 * arranged to avoid.
 */

export default async function StudiesPage() {
  await perRequest();
  const t = await getTranslations("hub");
  const s = await getTranslations("study");
  const empty = await getTranslations("empty");

  // Null is the read failing, and it is a different statement from an empty
  // list. "No study is registered" is a claim about the catalog; a page that
  // made it because a fetch timed out would be telling a reader the registry is
  // empty on the strength of a network error.
  const studies = await listStudies();
  const grouped = threads(studies ?? []);

  return (
    <div className="space-y-8">
      <section className="relative -mx-5 -mt-10 overflow-hidden px-5 pb-8 pt-10">
        <HexWash color="var(--og-petrol)" opacity={0.08} />
        <div className="relative max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight">{t("studies.title")}</h1>
          <Rule />
          <p className="mt-5 text-base text-[color:var(--muted)]">{t("studies.blurb")}</p>
          {grouped.length > 0 ? (
            <p className="mt-3 text-sm text-[color:var(--muted)]">{s("threadsBlurb")}</p>
          ) : null}
        </div>
      </section>

      {studies === null ? (
        <EmptyState title={empty("catalogUnavailable")}>
          <p>{empty("catalogUnavailableHelp")}</p>
        </EmptyState>
      ) : grouped.length === 0 ? (
        <EmptyState title={empty("noStudies")}>
          <p>{empty("noStudiesHelp")}</p>
        </EmptyState>
      ) : (
        <ul className="space-y-6">
          {grouped.map((thread) => (
            <Thread key={thread.key} thread={thread} s={s} />
          ))}
        </ul>
      )}
    </div>
  );
}

type Translate = Awaited<ReturnType<typeof getTranslations<"study">>>;

function Thread({ thread, s }: { thread: StudyThread; s: Translate }) {
  return (
    <li className="og-card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="og-eyebrow">
          {thread.docket ? s("docket", { docket: thread.docket }) : s("noDocket")}
        </p>
        <p className="text-xs text-[color:var(--muted)]">
          {thread.jurisdiction ? `${thread.jurisdiction} · ` : ""}
          {s("threadCount", { count: thread.size })}
        </p>
      </div>

      <div className="mt-3">
        <StudyLine study={thread.lead} s={s} />
      </div>

      {/* Indented and marked, because the relationship is the point. An
          intervention rendered at the same level as the filing is an opinion
          beside an opinion; rendered under it, it is an answer to something. */}
      {thread.replies.length > 0 ? (
        <ul
          className="mt-4 space-y-4 border-l pl-4"
          style={{ borderColor: "var(--border)" }}
        >
          {thread.replies.map((reply) => (
            <li key={reply.id}>
              <p className="mb-1 text-xs text-[color:var(--muted)]">{s("contests")}</p>
              <StudyLine study={reply} s={s} />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function StudyLine({ study, s }: { study: StudyRow; s: Translate }) {
  const kind = study.study_kind;
  const frozen = formatDate(studyDate(study) || null);
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link href={`/studies/${study.id}`} className="text-lg font-semibold hover:underline">
          {study.title}
        </Link>
        {/* A utility's filing and an intervenor's response carry different
            weight and must not render the same. */}
        {kind ? (
          <span className="og-tag">
            {isKnownKind(kind) ? s(`kinds.${kind}` as "kinds.filing") : kind}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-[color:var(--muted)]" title={s("frozenHelp")}>
        {frozen ? s("frozen", { date: frozen }) : s("frozenUnknown")}
      </p>
    </div>
  );
}
