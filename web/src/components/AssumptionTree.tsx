import Link from "next/link";
import { getTranslations } from "next-intl/server";
import type { Assumption } from "@/lib/api";
import { iriTail } from "@/lib/format";
import { type PathNode, assumptionTree, isKnownBasis, splitPath } from "@/lib/studies";

/**
 * An assumption set, as the tree its field paths already describe (#82).
 *
 * `<details>` and no `"use client"`. The tree opens and closes with no
 * JavaScript, which matters more here than on most pages: the reader this is
 * for is as likely to be a regulator opening the published static copy as a
 * modeller on the live site, and a browsable tree that needs a hydration pass
 * to browse is not browsable on the copy that gets emailed around.
 *
 * Three things sit on every row, because each answers a question the others
 * cannot. The **value** is what the study assumed. The **basis** is what kind
 * of claim it is — 0.098 asserted and 0.098 measured are different claims, and
 * a filing that does not distinguish them cannot be argued with. The **source**
 * is where it came from, and the reason "trace every data source behind this
 * proposal" is a query rather than a reading exercise.
 */
export async function AssumptionTree({ assumptions }: { assumptions: Assumption[] }) {
  const s = await getTranslations("study");
  const root = assumptionTree(assumptions);

  return (
    <div className="space-y-2">
      {root.leaves.length > 0 ? <Rows rows={root.leaves} s={s} /> : null}
      {root.children.map((child) => (
        <Branch key={child.path} node={child} s={s} depth={0} />
      ))}
    </div>
  );
}

type Translate = Awaited<ReturnType<typeof getTranslations<"study">>>;

function Branch({ node, s, depth }: { node: PathNode<Assumption>; s: Translate; depth: number }) {
  const count = countLeaves(node);
  return (
    <details
      open={depth < 2}
      className="border-l pl-3"
      style={{ borderColor: "var(--border)" }}
    >
      <summary className="cursor-pointer text-sm font-medium">
        {node.segment}{" "}
        <span className="text-xs font-normal text-[color:var(--muted)]">
          {s("assumptionCount", { count })}
        </span>
      </summary>
      <div className="mt-2 space-y-2">
        {node.leaves.length > 0 ? <Rows rows={node.leaves} s={s} /> : null}
        {node.children.map((child) => (
          <Branch key={child.path} node={child} s={s} depth={depth + 1} />
        ))}
      </div>
    </details>
  );
}

function countLeaves(node: PathNode<Assumption>): number {
  return node.leaves.length + node.children.reduce((n, c) => n + countLeaves(c), 0);
}

function Rows({ rows, s }: { rows: Assumption[]; s: Translate }) {
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <Row key={row.id} row={row} s={s} />
      ))}
    </ul>
  );
}

function Row({ row, s }: { row: Assumption; s: Translate }) {
  const { parameter } = splitPath(row.path);
  const basis = row.value_basis;

  return (
    <li className="text-sm">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-xs">{parameter || iriTail(row.id)}</span>
        <span className="font-semibold">
          {row.value ?? "—"}
          {row.unit ? (
            <span className="ml-1 font-normal text-[color:var(--muted)]">
              {row.unit_label ?? iriTail(row.unit)}
            </span>
          ) : null}
        </span>
        <span className="og-tag text-[11px]" title={s("basisHelp")}>
          {basis
            ? isKnownBasis(basis)
              ? s(`basis.${basis}` as "basis.measured")
              : basis
            : s("basisUnstated")}
        </span>
        {/* An inherited value needs no defence; a changed one always does. The
            marker is what makes a single-factor intervention checkable without
            diffing two tables by eye. */}
        {row.inherited_from ? (
          <span className="text-xs text-[color:var(--muted)]">{s("inherited")}</span>
        ) : (
          <span className="text-xs font-medium text-[color:var(--accent-text)]">
            {s("changed")}
          </span>
        )}
      </div>

      {row.field_sources.length > 0 ? (
        <p className="mt-0.5 text-xs text-[color:var(--muted)]">
          {s("colSource")}:{" "}
          {row.field_sources.map((iri, index) => {
            const slug = iriTail(iri);
            const held = row.field_source_ids.includes(slug);
            return (
              <span key={iri}>
                {index > 0 ? ", " : ""}
                {held ? (
                  <Link
                    href={`/datasets/${slug}`}
                    className="text-[color:var(--accent-text)] hover:underline"
                  >
                    {slug}
                  </Link>
                ) : (
                  /* Named, not linked. A rate-case exhibit is a real document
                     this catalog has no record of: dropping the citation would
                     lose the trace, and linking it would promise a page that
                     404s. */
                  <span title={s("notInCatalogHelp")}>
                    {slug} ({s("notInCatalog")})
                  </span>
                )}
              </span>
            );
          })}
        </p>
      ) : null}

      {row.justification ? (
        <p className="mt-1 max-w-prose text-xs text-[color:var(--muted)]" title={s("changedHelp")}>
          {row.justification}
        </p>
      ) : null}
    </li>
  );
}
