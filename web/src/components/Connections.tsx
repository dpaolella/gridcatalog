"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import type { LinkedDataset } from "@/lib/api";

/**
 * The connections tab (PRD §F3, §F6).
 *
 * Two representations of the same twelve links: a one-hop graph with edge
 * thickness proportional to the 5-point strength, and the list it mirrors. The
 * list is not a fallback — it is the accessible representation, and it carries
 * the reasons, which a graph cannot.
 *
 * **Capped at twelve, with "show more".** PRD §F3: a full graph of a
 * well-connected catalog is an unreadable hairball, and an unreadable picture
 * is worse than no picture because it looks like information.
 *
 * **A correlated link is visibly flagged and never hidden.** PRD §F6.9: hiding
 * it removes exactly the information the user needs — that these two are not
 * independent — and leaves them believing they are, which is a stronger and
 * more wrong claim.
 *
 * The graph uses the structural line colour for edges and the Orange accent,
 * dashed, for a correlated one — so the flag survives a glance at the picture
 * rather than living only in the list below it.
 */

const VISIBLE = 12;

export function Connections({ links }: { links: LinkedDataset[] }) {
  const t = useTranslations("connections");
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? links : links.slice(0, VISIBLE);
  const hidden = links.length - shown.length;

  return (
    <div className="space-y-6">
      <LinkGraph links={shown} />
      <p className="text-sm text-[color:var(--muted)]">{t("capHelp")}</p>

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
  const relationKey = link.relation as
    | "complementary"
    | "substitute"
    | "supersedes"
    | "superseded-by"
    | "derived-from"
    | "related";

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
 * The one-hop graph. Edge thickness is the 5-point strength; a correlated edge
 * is dashed and coloured, so the flag survives a glance at the picture rather
 * than living only in the list.
 */
function LinkGraph({ links }: { links: LinkedDataset[] }) {
  const size = 320;
  const centre = size / 2;
  const radius = 118;

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      className="mx-auto h-72 w-full max-w-md"
      role="img"
      aria-label={`One-hop graph with ${links.length} connected datasets. The same connections are listed below.`}
    >
      {links.map((link, index) => {
        const angle = (index / links.length) * 2 * Math.PI - Math.PI / 2;
        const x = centre + radius * Math.cos(angle);
        const y = centre + radius * Math.sin(angle);
        const correlated = Boolean(link.correlation_warning);
        return (
          <g key={link.dataset_id}>
            <line
              x1={centre}
              y1={centre}
              x2={x}
              y2={y}
              stroke={correlated ? "var(--status-alert)" : "var(--rule)"}
              strokeOpacity={0.55}
              strokeWidth={link.strength}
              strokeDasharray={correlated ? "4 3" : undefined}
            />
            <circle cx={x} cy={y} r={5} fill={correlated ? "var(--status-alert)" : "var(--rule)"} />
          </g>
        );
      })}
      <circle cx={centre} cy={centre} r={9} fill="var(--og-petrol)" />
    </svg>
  );
}
