"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FieldDetail } from "@/lib/api";
import {
  type ComponentRow,
  type ModelParameters,
  type SystemDocument,
  columnsByKind,
  componentRows,
  filterRows,
} from "@/lib/model-view";

/** Rows on a page of the table.
 *
 * #98's page-weight criterion in its simplest form: the DE model has 855 lines
 * and 1,240 components in all, and a table that rendered them whole would be
 * the same mistake `StaticSearch` already made once — a page that grows with
 * the corpus. Paged, the page's size is a constant nobody has to re-check when
 * a bigger model is registered. */
const PAGE = 25;

/**
 * The model document, as a reader can actually read it (#98).
 *
 * The values were reachable only as a 2.9 MB file on the Downloads tab. A
 * reader deciding whether to build on the model had to download it and open it
 * in something else to see what any of it said — which in practice means they
 * did not look, and the per-field `og:valueBasis` work is invisible at exactly
 * the moment it would matter.
 *
 * **Client-side, and fetched rather than shipped.** Same reasoning as the map
 * beside it: the model bytes are the Hub's own static assets, so the page costs
 * nothing until somebody opens it, and the static export ships a shell rather
 * than a megabyte of components. It is also why this is one of the few places
 * the site renders data the server did not.
 *
 * **One vocabulary for basis.** The *record* says whether a field is measured,
 * estimated or modeled — `og:valueBasis`, the same declaration the Schema tab
 * renders — and this page reads that rather than inventing a second one. The
 * record is the claim; `parameters.json` is the working behind an estimated
 * value, and the two are shown as what they are.
 */
export function ModelComponents({
  systemUrl,
  parametersUrl,
  fields,
}: {
  systemUrl: string;
  parametersUrl: string;
  /** The record's field declarations, resolved on the server. The basis comes
   *  from the catalog, not from the model file — a model that described its own
   *  trustworthiness would be marking its own homework. */
  fields: FieldDetail[];
}) {
  const t = useTranslations("modelPage");
  /** A component named in the URL, by a link from the map's identity card.
   *  Read here rather than passed down, so that a shared link opens on the
   *  component it names in both builds — there is no request to read it from
   *  when the export is built, and this component renders on the client
   *  either way. */
  const selected = useSearchParams().get("component") ?? undefined;
  const [system, setSystem] = useState<SystemDocument | null>(null);
  const [parameters, setParameters] = useState<ModelParameters | null>(null);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState(selected ?? "");
  const [kind, setKind] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(systemUrl).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
      // The provenance file is allowed to be missing without taking the page
      // with it: the components and their declared bases are still worth
      // showing, and the derivation panel says it has nothing rather than the
      // whole page saying it failed.
      fetch(parametersUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([s, p]) => {
        if (cancelled) return;
        setSystem(s as SystemDocument);
        setParameters(p as ModelParameters | null);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [systemUrl, parametersUrl]);

  const columns = useMemo(() => columnsByKind(fields), [fields]);
  const kinds = useMemo(() => [...columns.keys()], [columns]);
  const active = kind ?? kinds[0] ?? null;

  const rows = useMemo(
    () => (active ? componentRows(system, active, columns.get(active) ?? [], parameters) : []),
    [system, active, columns, parameters],
  );
  const matched = useMemo(() => filterRows(rows, query), [rows, query]);
  const shown = matched.slice(page * PAGE, page * PAGE + PAGE);

  // A link from the map names one component, which is very likely not on the
  // type the page opens with. Following the name to its table is the whole
  // point of that link, so the type is chosen by what was asked for.
  useEffect(() => {
    if (!selected || !system) return;
    for (const candidate of columns.keys()) {
      const found = componentRows(system, candidate, columns.get(candidate) ?? [], null).some(
        (row) => row.name === selected,
      );
      if (found) {
        setKind(candidate);
        setOpen(selected);
        return;
      }
    }
  }, [selected, system, columns]);

  if (failed) {
    return (
      <div className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">
        <p className="font-medium text-[color:var(--foreground)]">{t("unavailable")}</p>
        <p className="mt-2">{t("unavailableHelp")}</p>
      </div>
    );
  }
  if (!system) {
    return <div className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">{t("loading")}</div>;
  }
  if (!active) {
    return (
      <div className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">
        <p className="font-medium text-[color:var(--foreground)]">{t("noFields")}</p>
        <p className="mt-2">{t("noFieldsHelp")}</p>
      </div>
    );
  }

  const active_columns = columns.get(active) ?? [];

  return (
    <section aria-labelledby="components-heading" className="og-card p-5">
      <h2 id="components-heading" className="font-semibold">
        {t("componentsTitle")}
      </h2>
      <p className="mt-1 text-sm text-[color:var(--muted)]">{t("componentsHelp")}</p>

      {/* The document's own note about itself, verbatim.
          This page reads the display copy — the one the map already fetched —
          and that copy is not the model: KPG's drops every generator cost
          curve, the OSM models simplify every corridor. Both say so in their
          `description`, and repeating it here is the difference between a table
          with gaps in it and a table that explains its gaps. Without it, a cost
          column reading "not in this copy" for all 201 units looks like a
          catalog that lost the cost curves. */}
      {system.description ? (
        <p
          className="mt-3 px-4 py-3 text-sm text-[color:var(--muted)]"
          style={{
            borderRadius: "var(--radius)",
            border: "1px solid var(--border)",
            background: "var(--surface)",
          }}
        >
          <span className="og-eyebrow">{t("documentNote")}</span> {system.description}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("typeLabel")}>
          {kinds.map((candidate) => (
            <button
              key={candidate}
              type="button"
              aria-pressed={candidate === active}
              onClick={() => {
                setKind(candidate);
                setPage(0);
                setOpen(null);
              }}
              className="og-tag px-3 py-1"
              style={
                candidate === active
                  ? {
                      background: "color-mix(in srgb, var(--accent) 16%, transparent)",
                      color: "var(--accent-text)",
                    }
                  : undefined
              }
            >
              {candidate}
            </button>
          ))}
        </div>
        <label className="flex min-w-0 flex-1 items-center gap-2 text-sm">
          <span className="sr-only">{t("filterLabel")}</span>
          <input
            type="search"
            value={query}
            placeholder={t("filterPlaceholder")}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            className="min-w-0 flex-1 px-2 py-1 text-sm"
            style={{
              borderRadius: "var(--radius)",
              border: "1px solid var(--border)",
              background: "var(--surface)",
            }}
          />
        </label>
      </div>

      <p className="mt-2 text-xs text-[color:var(--muted)]" aria-live="polite">
        {t("count", { shown: matched.length, total: rows.length })}
      </p>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[40rem] border-collapse text-sm">
          <caption className="sr-only">{t("caption", { kind: active })}</caption>
          <thead>
            <tr className="text-left align-bottom">
              <th scope="col" className="py-1.5 pr-4 font-medium">
                {t("component")}
              </th>
              <th scope="col" className="py-1.5 pr-4 font-medium">
                {t("upstream")}
              </th>
              {active_columns.map((column) => (
                <th key={column.fieldId} scope="col" className="py-1.5 pr-4 font-medium">
                  <span title={column.definition ?? undefined}>{column.label}</span>
                  {column.unit ? (
                    <span className="block font-normal text-[color:var(--muted)]">
                      {column.unit}
                    </span>
                  ) : null}
                  {/* The basis on the column header, because it is a property of
                      the field and not of the row: every line's reactance in
                      this model is modelled the same way, and repeating the
                      word on 855 rows would make it furniture. */}
                  <span className="mt-0.5 block">
                    <Basis value={column.basis} caveat={column.caveat} />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <Row
                key={row.key}
                row={row}
                columns={active_columns.length + 2}
                expanded={open === row.name}
                onToggle={() => setOpen(open === row.name ? null : row.name)}
              />
            ))}
          </tbody>
        </table>
      </div>

      {matched.length === 0 ? (
        <p className="mt-3 text-sm text-[color:var(--muted)]">{t("noMatch")}</p>
      ) : null}

      {/* The column definitions, in the open rather than on a `title`.
          Hover is not a place to keep the only copy of anything: it is
          unreachable by touch, awkward with a screen reader, and invisible to a
          reader who does not already suspect there is something to find. It
          matters more here than on most tables, because two of these columns
          carry no unit at all — KPG's demand and reactance are per unit on a
          100 MVA base, and the only thing that says so is the definition. */}
      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-[color:var(--accent-text)]">
          {t("columnsTitle")}
        </summary>
        <dl className="mt-2 space-y-3">
          {active_columns.map((column) => (
            <div key={column.fieldId}>
              <dt className="flex flex-wrap items-baseline gap-2 font-medium">
                {column.label}
                <span className="font-mono text-xs font-normal text-[color:var(--muted)]">
                  {column.fieldId}
                </span>
                <Basis value={column.basis} caveat={null} />
              </dt>
              {column.definition ? (
                <dd className="text-[color:var(--muted)]">{column.definition}</dd>
              ) : null}
              {column.caveat ? (
                <dd className="mt-1 text-[color:var(--muted)]">
                  <span className="og-eyebrow">{t("caveat")}</span> {column.caveat}
                </dd>
              ) : null}
            </div>
          ))}
        </dl>
      </details>

      {matched.length > PAGE ? (
        <div className="mt-4 flex items-center gap-3 text-sm">
          <button
            type="button"
            className="og-tag px-3 py-1"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            {t("previous")}
          </button>
          <span className="text-xs text-[color:var(--muted)]">
            {t("page", { page: page + 1, pages: Math.ceil(matched.length / PAGE) })}
          </span>
          <button
            type="button"
            className="og-tag px-3 py-1"
            disabled={(page + 1) * PAGE >= matched.length}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("next")}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function Row({
  row,
  columns,
  expanded,
  onToggle,
}: {
  row: ComponentRow;
  columns: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("modelPage");
  const structured = row.values.filter((value) => value.structured);
  const expandable = row.derivation !== null || structured.length > 0;

  return (
    <>
      <tr className="border-t align-top" style={{ borderColor: "var(--border)" }}>
        <th scope="row" className="py-2 pr-4 text-left font-normal">
          <span className="font-mono text-xs font-medium">{row.name || t("unnamed")}</span>
          {expandable ? (
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              className="ml-2 text-xs font-medium text-[color:var(--accent-text)] hover:underline"
            >
              {expanded ? t("hideDerivation") : t("showDerivation")}
            </button>
          ) : null}
        </th>
        <td className="py-2 pr-4 font-mono text-xs text-[color:var(--muted)]">
          {/* Both identities, named. An OSM way id and a Sienna component name
              are durable in different ways, and a reader joining on the wrong
              one gets silence rather than an error. Where there is no upstream
              id the cell says so — it never falls back to the internal index,
              which would look exactly like a stable identity and is not one. */}
          {row.sourceId ?? <span className="not-italic">{t("noUpstream")}</span>}
        </td>
        {row.values.map((value) => (
          <td key={value.column.fieldId} className="py-2 pr-4 tabular-nums">
            {value.text ?? (
              <span className="text-[color:var(--muted)]">
                {value.structured ? t("structured") : t("notCarried")}
              </span>
            )}
          </td>
        ))}
      </tr>
      {expanded ? (
        <tr style={{ borderColor: "var(--border)" }}>
          <td colSpan={columns} className="pb-3 text-xs">
            {row.derivation ? (
              /* A bordered inset rather than `og-panel`, which is the dark
                 emphasis block. Inside a table row that treatment reads as a
                 warning, and this is the opposite — it is the working, and the
                 rows that have it are better documented than the rows that do
                 not. */
              <div
                className="px-4 py-3"
                style={{
                  borderRadius: "var(--radius)",
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                }}
              >
                <p>
                  <span className="og-eyebrow">{t("method")}</span>{" "}
                  <span className="font-mono">{row.derivation.method}</span>
                  {row.derivation.lineType ? ` · ${row.derivation.lineType}` : ""}
                </p>
                {row.derivation.inputs.length > 0 ? (
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                    {row.derivation.inputs.map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt className="text-[color:var(--muted)]">{key}</dt>
                        <dd className="tabular-nums">{value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                {/* The author's own statement of what the rule gets wrong.
                    It is the most useful line on the page and the one a file
                    download buries. */}
                {row.derivation.approximations.map((note) => (
                  <p key={note} className="mt-2 text-[color:var(--muted)]">
                    {note}
                  </p>
                ))}
              </div>
            ) : null}
            {structured.map((value) => (
              <pre
                key={value.column.fieldId}
                className="mt-2 overflow-x-auto px-4 py-3 font-mono text-[11px]"
                style={{
                  borderRadius: "var(--radius)",
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                }}
              >
                {value.column.label}: {JSON.stringify(value.raw, null, 1)}
              </pre>
            ))}
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The record's `og:valueBasis`, rendered the way the Schema tab renders it —
 *  as the word the record uses, never re-graded into a scale of this page's
 *  own. A field with no declared basis says so; it does not default to the
 *  flattering one. */
function Basis({ value, caveat }: { value: string | null; caveat: string | null }) {
  const t = useTranslations("modelPage");
  if (!value) {
    return <span className="text-xs font-normal text-[color:var(--muted)]">{t("noBasis")}</span>;
  }
  return (
    <span
      className="px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
      style={{
        borderRadius: "var(--radius)",
        background: "color-mix(in srgb, var(--accent) 12%, transparent)",
        color: "var(--accent-text)",
      }}
      title={caveat ?? undefined}
    >
      {value}
      {caveat ? " ⓘ" : ""}
    </span>
  );
}
