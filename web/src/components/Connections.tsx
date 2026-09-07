"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import type { LinkedDataset } from "@/lib/api";

/**
 * The connections tab (PRD §F3, §F6).
 *
 * The list is the representation. It carries the reasons, the strength and the
 * correlation flag, and it is the accessible one.
 *
 * **There used to be a one-hop graph above it, and it has been removed.** Every
 * link on this tab starts at the same dataset, so the picture was always a
 * star — and a star has no topology to show. Its layout carried no information,
 * its nodes were unlabelled dots that could not be hovered or followed, and the
 * two things it did encode, strength and correlation, were already in the list
 * directly below as pips and a coloured rail. It cost 288px above the content
 * and returned nothing; it looked like information, which is the specific
 * failure PRD §F3 warns about for the hairball.
 *
 * What replaced it is a one-line breakdown by relation. That is a fact about
 * the set rather than a redrawing of its members — "eleven connections, mostly
 * alternative sources, one not independent" is the thing a reader wants before
 * deciding whether to read twelve cards, and no row of the list states it.
 *
 * **Capped at twelve, with "show more".** PRD §F3's reasoning about a
 * hairball applies to a wall of cards too: past a dozen, the strongest
 * connection stops being findable and the tab stops being read.
 *
 * **A correlated link is visibly flagged and never hidden.** PRD §F6.9: hiding
 * it removes exactly the information the user needs — that these two are not
 * independent — and leaves them believing they are, which is a stronger and
 * more wrong claim.
 */

/** The relations the message catalogue has a label for. Anything the linker
 *  emits outside this set falls back to "related" rather than rendering a
 *  missing-translation key at the reader. */
type RelationKey =
  | "complementary"
  | "substitute"
  | "supersedes"
  | "superseded-by"
  | "derived-from"
  | "related";

const RELATION_ORDER: RelationKey[] = [
  "complementary",
  "substitute",
  "supersedes",
  "superseded-by",
  "derived-from",
  "related",
];

const VISIBLE = 12;

export function Connections({ links }: { links: LinkedDataset[] }) {
  const t = useTranslations("connections");
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? links : links.slice(0, VISIBLE);
  const hidden = links.length - shown.length;

  return (
    <div className="space-y-6">
      <RelationBreakdown links={links} />
      {hidden > 0 ? (
        <p className="text-sm text-[color:var(--muted)]">{t("capHelp")}</p>
      ) : null}

      <ul className="space-y-3" data-testid="connection-list">
        {shown.map((link) => (
          <ConnectionRow key={link.dataset_id} link={link} />
        ))}
      </ul>

      {hidden > 0 || expanded ? (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="og-tag px-3 py-1.5 text-sm hover:text-[color:var(--foreground)]"
          style={{ borderColor: "var(--border)" }}
        >
          {expanded ? t("showFewer") : t("showMore", { count: hidden })}
        </button>
      ) : null}
    </div>
  );
}

function ConnectionRow({ link }: { link: LinkedDataset }) {
  const t = useTranslations("connections");
  const [open, setOpen] = useState(false);
  const relationKey = link.relation as RelationKey;

  return (
    <li
      data-testid="connection"
      className="og-card relative p-4"
      style={{
        // A correlated pairing is marked at its edge, not hidden. Orange is the
        // accent for the one thing on a page that most needs to be seen, and a
        // 3px rail is a mark rather than a wash.
        borderLeft: link.correlation_warning
          ? "3px solid var(--status-alert)"
          : "1px solid var(--border)",
      }}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link
          href={`/datasets/${link.dataset_id}`}
          className="font-medium hover:underline"
        >
          {link.title ?? link.dataset_id}
        </Link>
        <span className="flex items-center gap-2 text-xs text-[color:var(--muted)]">
          <StrengthPips strength={link.strength} />
          <span>{t(`relation.${relationKey}`)}</span>
        </span>
      </div>

      <p className="mt-1 text-sm">{link.descriptor}</p>

      {link.correlation_warning ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-semibold"
            style={{
              borderRadius: "var(--radius)",
              background: "color-mix(in srgb, var(--status-alert) 12%, transparent)",
              color: "var(--status-alert)",
            }}
            aria-expanded={open}
          >
            <span aria-hidden>△</span>
            {t("correlated")}
          </button>
          {open ? (
            <p
              className="mt-2 border-l-2 py-1 pl-3 text-sm"
              style={{ borderColor: "var(--status-alert)" }}
            >
              {link.correlation_warning}
            </p>
          ) : null}
        </div>
      ) : null}

      {link.reasons.length ? (
        <ul className="mt-2 space-y-0.5 text-xs text-[color:var(--muted)]">
          {link.reasons.map((reason) => (
            <li key={reason}>· {reason}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function StrengthPips({ strength }: { strength: number }) {
  const t = useTranslations("connections");
  return (
    <span className="inline-flex gap-0.5" title={t("strength", { strength })}>
      {[1, 2, 3, 4, 5].map((pip) => (
        <span
          key={pip}
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: pip <= strength ? "var(--rule)" : "var(--border)" }}
        />
      ))}
      <span className="sr-only">{t("strength", { strength })}</span>
    </span>
  );
}

/**
 * What kind of connections these are, in one line.
 *
 * A segmented bar over the whole link set — not the twelve shown — because the
 * question it answers ("is there anything here, and of what sort") is about the
 * set and not about the page. Segments are proportional so the shape reads at a
 * glance, and every segment is also named with its count, because a bar chart
 * of four bands is not readable by width alone and the numbers are short.
 *
 * A correlated pairing is called out separately and in the alert colour. It is
 * not a relation type — it cuts across them — and it is the one fact on this
 * tab that changes what a modeller may do with the data (PRD §F6.9).
 */
function RelationBreakdown({ links }: { links: LinkedDataset[] }) {
  const t = useTranslations("connections");
  if (links.length === 0) return null;

  const counts = new Map<RelationKey, number>();
  for (const link of links) {
    const key = (RELATION_ORDER.includes(link.relation as RelationKey)
      ? link.relation
      : "related") as RelationKey;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const bands = RELATION_ORDER
    .filter((key) => counts.has(key))
    .map((key, index) => ({
      key,
      count: counts.get(key) as number,
      // Stepped down one ramp rather than assigned six hues: these are
      // categories with no natural order and no meaning in colour, and six
      // colours would imply both. The step is wide enough to read at a glance —
      // at 0.14 two adjacent bands looked like one solid bar — and floored so
      // the sixth is still visible against the track.
      opacity: Math.max(0.86 - index * 0.16, 0.3),
    }));
  const correlated = links.filter((link) => link.correlation_warning).length;

  return (
    <div className="space-y-2">
      <div
        className="flex h-2 w-full gap-0.5 overflow-hidden"
        style={{ borderRadius: "var(--radius)", background: "var(--border)" }}
        aria-hidden
      >
        {bands.map((band) => (
          <div
            key={band.key}
            style={{
              width: `${(band.count / links.length) * 100}%`,
              background: "var(--rule)",
              opacity: band.opacity,
            }}
          />
        ))}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[color:var(--muted)]">
        {bands.map((band) => (
          <li key={band.key} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: "var(--rule)", opacity: band.opacity }}
            />
            <span className="font-medium text-[color:var(--foreground)]">{band.count}</span>
            {t(`relation.${band.key}`)}
          </li>
        ))}
        {correlated ? (
          <li
            className="flex items-center gap-1.5 font-medium"
            style={{ color: "var(--status-alert)" }}
          >
            <span aria-hidden>△</span>
            <span>
              {correlated} {t("correlated").toLowerCase()}
            </span>
          </li>
        ) : null}
      </ul>
    </div>
  );
}
