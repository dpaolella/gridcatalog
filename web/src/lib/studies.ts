/**
 * Reading a list of studies as the conversations they actually are (#82).
 *
 * A regulatory docket is a contested thing. A utility files an IRP; a ratepayer
 * coalition intervenes on one assumption in it; staff files its own analysis;
 * the utility replies. Listed flat, that is four unrelated rows sorted by date,
 * and the most important fact about them — that three of them exist *because
 * of* the first — is the one fact the list does not carry. A reader scanning it
 * sees four opinions rather than one argument.
 *
 * So the list groups. The grouping is derived entirely from what the records
 * already declare: `docket` for the proceeding, `parent_study` for what a study
 * contests. Nothing here infers a relationship from titles or dates, because a
 * wrongly asserted relationship is worse than none — "this contests that" is a
 * strong claim and the catalog should only make it where a record made it.
 */

/** What the grouping needs off a row. A structural type rather than
 *  `DatasetSummary`, so the tests can build one without building a record. */
export interface StudyRow {
  id: string;
  title: string;
  study_kind?: string | null;
  docket?: string | null;
  jurisdiction?: string | null;
  parent_study?: string | null;
  frozen_at?: string | null;
  modified?: string | null;
  iri?: string;
}

export interface StudyThread {
  /** Stable across renders, and the docket where there is one. */
  key: string;
  docket: string | null;
  jurisdiction: string | null;
  /** The study the others are answering: the one with no parent inside this
   *  thread, earliest first where several qualify. */
  lead: StudyRow;
  /** Everything else in the thread, oldest first, so the thread reads forward
   *  in time the way the proceeding happened. */
  replies: StudyRow[];
  /** How many studies in total. `replies.length + 1`, named because every
   *  caller wants it and `+ 1` at four call sites is four chances to forget. */
  size: number;
}

/** A study's own moment, preferring the date it declares over the date the
 *  record was touched. A filing frozen in April and re-catalogued in September
 *  belongs in April: `frozen_at` is a statement about the study, `modified` is
 *  a statement about the record of it. */
export function studyDate(study: StudyRow): string {
  return study.frozen_at ?? study.modified ?? "";
}

const byDate = (a: StudyRow, b: StudyRow) =>
  studyDate(a).localeCompare(studyDate(b)) || a.id.localeCompare(b.id);

/**
 * Group studies into threads, most recently active first.
 *
 * A thread is a docket where the studies name one, and a single study where
 * they do not — a working paper is not a proceeding and pretending otherwise
 * would put unrelated papers in one box. Studies naming a parent but no docket
 * follow their parent, so a fork of a paper stays with the paper.
 */
export function threads(studies: readonly StudyRow[]): StudyThread[] {
  const byId = new Map(studies.map((s) => [s.id, s]));

  /** The thread a study belongs to: its docket, or the root of its parent
   *  chain, or itself. Walked with a seen-set because `parent_study` is a
   *  declaration in a record and two records declaring each other would
   *  otherwise spin forever — a malformed pair should cost a slightly odd
   *  grouping, not a hung render. */
  const keyOf = (study: StudyRow): string => {
    if (study.docket) return `docket:${study.docket}`;
    const seen = new Set<string>([study.id]);
    let current = study;
    for (;;) {
      const parentId = tail(current.parent_study);
      const parent = parentId ? byId.get(parentId) : undefined;
      if (!parent || seen.has(parent.id)) return `study:${current.id}`;
      if (parent.docket) return `docket:${parent.docket}`;
      seen.add(parent.id);
      current = parent;
    }
  };

  const groups = new Map<string, StudyRow[]>();
  for (const study of studies) {
    const key = keyOf(study);
    const group = groups.get(key);
    if (group) group.push(study);
    else groups.set(key, [study]);
  }

  const out: StudyThread[] = [];
  for (const [key, group] of groups) {
    const members = [...group].sort(byDate);
    const ids = new Set(members.map((s) => s.id));
    // The lead is the study nothing in this thread answers. "No parent at all"
    // is the common case; "a parent outside this page" is the one that matters,
    // because a thread whose filing is on the next page would otherwise have no
    // lead and render as a headless list of interventions.
    const lead = members.find((s) => !insideThread(s, ids)) ?? members[0];
    out.push({
      key,
      docket: members.find((s) => s.docket)?.docket ?? null,
      jurisdiction: members.find((s) => s.jurisdiction)?.jurisdiction ?? null,
      lead,
      replies: members.filter((s) => s.id !== lead.id),
      size: members.length,
    });
  }

  // Most recently active first, on the newest study in the thread rather than
  // on the lead: a docket whose filing is two years old and whose latest
  // intervention landed last week is the live one, and sorting on the filing
  // would bury it under dockets nobody has touched since.
  return out.sort(
    (a, b) => latest(b).localeCompare(latest(a)) || a.key.localeCompare(b.key),
  );
}

function insideThread(study: StudyRow, ids: Set<string>): boolean {
  const parent = tail(study.parent_study);
  return parent !== null && ids.has(parent);
}

function latest(thread: StudyThread): string {
  return [thread.lead, ...thread.replies].reduce(
    (newest, s) => (studyDate(s) > newest ? studyDate(s) : newest),
    "",
  );
}

/** The slug at the end of an IRI. `parent_study` is carried as an IRI because
 *  that is what the record says, and every page keys on the slug. */
export function tail(iri: string | null | undefined): string | null {
  if (!iri) return null;
  const slug = iri.replace(/\/+$/, "").split("/").pop();
  return slug || null;
}

// ---------------------------------------------------------------------------
// Assumption paths
// ---------------------------------------------------------------------------

/**
 * A Sienna field path is an address, and addresses nest.
 *
 * `Investments/Financials/TechnologyFinancialData#return_on_equity` says which
 * parameter, on which component, under which part of the model. Rendered as a
 * flat table of full paths, a set of a few hundred values is a wall of repeated
 * prefixes in which the reader cannot see that eleven of them are financial
 * assumptions and two are policy ones. Rendered as the tree the path already
 * describes, the shape of what the study assumed is the shape of the page.
 *
 * The split is on the record's own text and nothing is invented: a path with no
 * `#` keeps its whole value as the parameter, and a path with no `/` is one
 * level deep. A value with no path at all is still shown — it is a number the
 * study stands on, and hiding it because the catalog cannot file it would lose
 * the one thing this page exists to show.
 */
export interface PathSplit {
  /** The component path, `#` stripped: the segments that nest. */
  segments: string[];
  /** The parameter on that component. */
  parameter: string;
}

export function splitPath(path: string | null | undefined): PathSplit {
  const value = (path ?? "").trim();
  if (!value) return { segments: [], parameter: "" };
  const hash = value.indexOf("#");
  const head = hash === -1 ? "" : value.slice(0, hash);
  const parameter = hash === -1 ? value : value.slice(hash + 1);
  return { segments: head.split("/").filter(Boolean), parameter };
}

export interface PathNode<T> {
  /** One segment of the component path — what the row is labelled with. */
  segment: string;
  /** The segments joined, so a key is stable and unique among siblings. */
  path: string;
  children: PathNode<T>[];
  /** Values addressed to exactly this node. */
  leaves: T[];
}

/**
 * Nest assumptions under the component path each one names.
 *
 * Values whose path the catalog cannot split — no path recorded — collect under
 * a single unnamed root rather than being dropped or scattered, so the page can
 * say plainly that these are the values with no address.
 */
export function assumptionTree<T extends { path?: string | null }>(
  rows: readonly T[],
): PathNode<T> {
  const root: PathNode<T> = { segment: "", path: "", children: [], leaves: [] };

  for (const row of rows) {
    const { segments } = splitPath(row.path);
    let node = root;
    for (const segment of segments) {
      const path = node.path ? `${node.path}/${segment}` : segment;
      let child = node.children.find((c) => c.segment === segment);
      if (!child) {
        child = { segment, path, children: [], leaves: [] };
        node.children.push(child);
      }
      node = child;
    }
    node.leaves.push(row);
  }
  return root;
}

// ---------------------------------------------------------------------------
// Controlled values
// ---------------------------------------------------------------------------
//
// The shapes constrain both of these, so a record carrying anything else did
// not validate — and yet the UI still renders whatever it is given rather than
// blanking it. A value the interface has no word for is still a fact about the
// record, and a silent blank would hide it at exactly the moment it matters.

export const STUDY_KINDS = ["filing", "intervention", "academic", "reanalysis"] as const;
export const VALUE_BASES = ["measured", "estimated", "modeled", "synthetic"] as const;

export const isKnownKind = (kind: string): boolean =>
  (STUDY_KINDS as readonly string[]).includes(kind);

export const isKnownBasis = (basis: string): boolean =>
  (VALUE_BASES as readonly string[]).includes(basis);
