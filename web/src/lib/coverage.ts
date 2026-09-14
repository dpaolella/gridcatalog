import type { DataGap, FacetBucket } from "./api";

/** The completeness levels, in the order a reader should meet them.
 *
 * Level 1 first because it is the honest starting point — discoverable and
 * nothing more — and because a table that led with the best column would read
 * as a scoreboard rather than a description. */
export const LEVELS = [1, 2, 3] as const;

export interface DomainCoverage {
  iri: string;
  /** `DD5`, the notation the PRD fixes. */
  code: string;
  label: string;
  /** Records at each completeness level, indexed by level. */
  counts: Record<number, number>;
  total: number;
  /** Register entries filed against this domain. Not derived from the counts:
   * a gap is an attributed statement about the open landscape, and a domain
   * with no entry has not been surveyed and found complete. */
  gaps: DataGap[];
}

const DOMAIN_ORDER = /DD(\d+)$/;

/** Split a `domain_coverage` key back into its two halves.
 *
 * The key is `{domain IRI}|{level}` and the IRI contains no pipe, so the split
 * is unambiguous. Returns null for anything that is not that shape rather than
 * guessing, so a malformed bucket is dropped instead of counted against a
 * domain it does not name. */
export function splitCoverageKey(value: unknown): { iri: string; level: number } | null {
  const text = String(value ?? "");
  const cut = text.lastIndexOf("|");
  if (cut < 1) return null;
  const tail = text.slice(cut + 1);
  // `Number("")` is 0, and 0 is an integer, so a key ending in a bare pipe
  // parsed as a valid level and invented a column no record is at. Levels
  // start at 1; anything else is a key this function did not understand.
  const level = Number(tail);
  if (!tail || !Number.isInteger(level) || level < 1) return null;
  return { iri: text.slice(0, cut), level };
}

/** The cell a link has to carry to select exactly what it counted. */
export function coverageKey(iri: string, level: number): string {
  return `${iri}|${level}`;
}

/**
 * The catalog's domain-by-completeness table.
 *
 * Built from the `domain_coverage` facet, which the search backend aggregates
 * over the same filtered set as the results — so a cell's count and the list
 * it links to are the same set by construction. The alternative, counting rows
 * on the page, would describe a page of twenty and call it the catalog.
 *
 * Labels come from the `data_domain` facet, because the crossing's keys are
 * identifiers and a table of IRIs is not a thing anyone reads. A domain that
 * appears in the crossing and not in the labels still renders, under its
 * notation: losing a row is worse than showing one with a terse name.
 */
export function domainCoverage(
  facets: Record<string, FacetBucket[]>,
  gaps: DataGap[] | null,
): DomainCoverage[] {
  const labels = new Map<string, string>();
  for (const bucket of facets.data_domain ?? []) {
    if (bucket.label) labels.set(String(bucket.value), bucket.label);
  }

  const rows = new Map<string, DomainCoverage>();
  for (const bucket of facets.domain_coverage ?? []) {
    const cell = splitCoverageKey(bucket.value);
    if (!cell) continue;
    const code = cell.iri.slice(cell.iri.lastIndexOf("/") + 1);
    const row = rows.get(cell.iri) ?? {
      iri: cell.iri,
      code,
      label: labels.get(cell.iri) ?? code,
      counts: {},
      total: 0,
      gaps: [],
    };
    row.counts[cell.level] = (row.counts[cell.level] ?? 0) + bucket.count;
    row.total += bucket.count;
    rows.set(cell.iri, row);
  }

  for (const gap of gaps ?? []) {
    for (const row of rows.values()) {
      if (row.code === gap.domain) row.gaps.push(gap);
    }
  }

  return [...rows.values()].sort((a, b) => {
    const left = Number(DOMAIN_ORDER.exec(a.code)?.[1] ?? Number.MAX_SAFE_INTEGER);
    const right = Number(DOMAIN_ORDER.exec(b.code)?.[1] ?? Number.MAX_SAFE_INTEGER);
    return left === right ? a.code.localeCompare(b.code) : left - right;
  });
}

/**
 * Register entries whose domain no record in the catalog carries.
 *
 * These are the ones the table above cannot show, and dropping them would be
 * the exact failure this view has to avoid: a gap in a domain the catalog
 * holds *nothing* for is the strongest finding the register has, and it is the
 * one that disappears if gaps are only ever rendered beside a row.
 */
export function unplacedGaps(rows: DomainCoverage[], gaps: DataGap[] | null): DataGap[] {
  const covered = new Set(rows.map((row) => row.code));
  return (gaps ?? []).filter((gap) => !covered.has(gap.domain));
}
